"""src/__main__.py — DEV_SPEC §8.3 A1 最小可运行入口。

`python -m src` 验证:
  1. 包结构能 import,无循环依赖
  2. `.env` 能加载,`config/settings.py` 各段能解析
  3. 关键路径变量已配置(模型 / 数据卷 / 数据库连接)

不启动任何服务、不连任何外部依赖,只做"按下电闸看灯能不能亮"的 skeleton 验证。
完整服务启动入口走 `src/api/app.py`(阶段 G)。
"""

from __future__ import annotations

from config.settings import settings
from src import __version__


def main() -> None:
    print(f"Medical RAG Agent  v{__version__}  (env={settings.ENV})")
    print()
    print("─── 数据库连接(.env)─────────────────────────")
    print(f"  PostgreSQL : {settings.postgres.HOST}:{settings.postgres.PORT}/{settings.postgres.DB}")
    print(f"  Milvus     : {settings.milvus.HOST}:{settings.milvus.PORT}")
    print(f"  Redis      : {settings.redis.URL}")
    print()
    print("─── 模型(.env)─────────────────────────────")
    print(f"  Embedding  : {settings.embedding.MODEL_PATH}")
    print(f"  Reranker   : {settings.reranker.MODEL_PATH}  (cutoff={settings.reranker.CUTOFF_LAYER})")
    print(f"  LLM        : {settings.llm.MODEL_NAME}  @  {settings.llm.BASE_URL}")
    print()
    print("─── 数据路径(.env)─────────────────────────")
    print(f"  PDF 输入   : {settings.paths.PDF_INPUT_DIR}")
    print(f"  MinerU 输出: {settings.paths.MINERU_OUTPUT_DIR}")
    print(f"  MinerU 模型: {settings.paths.MODELSCOPE_CACHE}  (源={settings.paths.MINERU_MODEL_SOURCE})")
    print()
    print("─── Agent 运行时常量(§9.7)───────────────────")
    al = settings.agent_limits
    print(f"  追问轮上限 MAX_FOLLOWUP_ROUNDS = {al.MAX_FOLLOWUP_ROUNDS}")
    print(f"  检查轮上限 MAX_EXAM_ROUNDS     = {al.MAX_EXAM_ROUNDS}")
    print(f"  RRF Top-N  RETRIEVE_TOP_N      = {al.RETRIEVE_TOP_N}")
    print()
    print("✓ skeleton imports OK")


if __name__ == "__main__":
    main()
