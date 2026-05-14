"""tests/unit/test_system_config.py — H7 动态配置层单元测试(DEV_SPEC §5.3)。

覆盖:
1. get_dynamic_config — 经 H1 cache 读 PG,缓存未命中 → 回源 → 写回
2. get_dynamic_config — PG 也没值时返 default
3. set_dynamic_config — 同事务写 system_config + config_change_log
4. set_dynamic_config — 写完调 invalidate_config 失效缓存
5. _infer_value_type — bool/int/float/str/dict 分桶正确

mock 掉 PG session_scope 与 cache module,纯单元测试零依赖。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.db.postgres import system_config as cfg


# ────────────────────────────────────────────────────────────────────────────
# get_dynamic_config — 经 cache 读
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.postgres.system_config.get_config_cached")
def test_get_returns_cached_value(mock_cached):
    mock_cached.return_value = 0.7
    assert cfg.get_dynamic_config("llm_temperature") == 0.7
    mock_cached.assert_called_once()
    # 第二个 arg 是 loader callable(类型校验,不直接调)
    args, _ = mock_cached.call_args
    assert args[0] == "llm_temperature"
    assert callable(args[1])


@patch("src.db.postgres.system_config.get_config_cached")
def test_get_returns_default_when_neither_cache_nor_pg_have_it(mock_cached):
    mock_cached.return_value = None  # cache miss + PG miss(loader 也返 None)
    assert cfg.get_dynamic_config("absent_key", default=42) == 42


@patch("src.db.postgres.system_config.get_config_cached")
def test_get_returns_value_zero_not_default(mock_cached):
    """边界:值是 falsy(0 / 空串 / False)时不应错把 default 当结果"""
    mock_cached.return_value = 0
    assert cfg.get_dynamic_config("k", default=999) == 0


# ────────────────────────────────────────────────────────────────────────────
# _load_from_pg
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.postgres.system_config.session_scope")
def test_load_from_pg_returns_value_when_row_exists(mock_scope):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    session.execute.return_value.first.return_value = (0.5,)
    assert cfg._load_from_pg("llm_temperature") == 0.5


@patch("src.db.postgres.system_config.session_scope")
def test_load_from_pg_returns_none_when_no_row(mock_scope):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    session.execute.return_value.first.return_value = None
    assert cfg._load_from_pg("absent_key") is None


@patch("src.db.postgres.system_config.session_scope")
def test_load_from_pg_returns_none_on_error(mock_scope):
    """PG 不可用 → 返 None,不抛(让上层 default 兜底,业务请求不挂)"""
    mock_scope.side_effect = Exception("PG down")
    assert cfg._load_from_pg("k") is None


# ────────────────────────────────────────────────────────────────────────────
# set_dynamic_config
# ────────────────────────────────────────────────────────────────────────────


@patch("src.db.postgres.system_config.invalidate_config")
@patch("src.db.postgres.system_config.session_scope")
def test_set_inserts_new_row_and_writes_change_log(mock_scope, mock_inv):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    session.get.return_value = None  # key 不存在 → INSERT 路径

    cfg.set_dynamic_config(
        "rag_top_k",
        20,
        operator_id="op-1",
        description="检索 top-K",
        change_reason="initial",
    )

    # 应 add() 两次:SystemConfig + ConfigChangeLog
    assert session.add.call_count == 2
    sc_obj, log_obj = (c.args[0] for c in session.add.call_args_list)
    assert sc_obj.key_name == "rag_top_k"
    assert sc_obj.value == 20
    assert sc_obj.value_type == "INT"
    assert log_obj.config_key == "rag_top_k"
    assert log_obj.old_value is None
    assert log_obj.new_value == 20
    assert log_obj.operator_id == "op-1"
    assert log_obj.change_reason == "initial"

    mock_inv.assert_called_once_with("rag_top_k")


@patch("src.db.postgres.system_config.invalidate_config")
@patch("src.db.postgres.system_config.session_scope")
def test_set_updates_existing_row(mock_scope, mock_inv):
    session = MagicMock()
    mock_scope.return_value.__enter__.return_value = session
    existing = MagicMock(spec=["value", "value_type", "updated_by", "description"])
    existing.value = 10  # 旧值,日志要记
    session.get.return_value = existing

    cfg.set_dynamic_config(
        "rag_top_k", 30, operator_id="op-2", change_reason="bumped"
    )

    # existing 字段被改;只 add() 一次(ConfigChangeLog),existing 走 ORM update
    assert existing.value == 30
    assert existing.value_type == "INT"
    assert existing.updated_by == "op-2"
    assert session.add.call_count == 1
    log_obj = session.add.call_args[0][0]
    assert log_obj.old_value == 10  # 旧值进 log
    assert log_obj.new_value == 30
    mock_inv.assert_called_once_with("rag_top_k")


# ────────────────────────────────────────────────────────────────────────────
# _infer_value_type
# ────────────────────────────────────────────────────────────────────────────


def test_infer_value_type_buckets():
    assert cfg._infer_value_type(True) == "BOOL"   # bool 在 int 之前判定
    assert cfg._infer_value_type(42) == "INT"
    assert cfg._infer_value_type(3.14) == "FLOAT"
    assert cfg._infer_value_type("hi") == "STRING"
    assert cfg._infer_value_type({"a": 1}) == "JSON"
    assert cfg._infer_value_type([1, 2, 3]) == "JSON"
