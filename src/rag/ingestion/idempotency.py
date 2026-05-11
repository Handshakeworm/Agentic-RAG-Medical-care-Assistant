"""幂等性工具:source_id / heading_path_id / chunk_id / content_hash 的确定性生成器(DEV_SPEC §3.1.4)。

本模块仅提供**纯函数**——无 IO、无状态、无外部依赖,给定相同输入永远返回相同输出。
消费方:
- C1 mineru_loader → `compute_source_id`
- C2 chunking      → `compute_heading_path_id` / `compute_chunk_id` / `compute_content_hash`
- C5 embedding     → 复用 `compute_content_hash` 做增量判断
- C6 storage       → 用 chunk_id 派生 Milvus 记录 ID(`{chunk_id}_summary` 等)

所有哈希均为 SHA-256;source_id / heading_path_id / chunk_id / content_hash 的截断长度
直接来自 spec 各对应小节,本模块不私自调整。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

# spec §3.1.4.1:source_id 取 SHA-256 前 16 位 hex(64 bit)
_SOURCE_ID_HEX_LEN = 16

# 父块的 relative_chunk_index 固定串(spec §3.1.4.2 父块约定)
_PARENT_REL_INDEX = "parent"

# 全角→半角字符映射表(spec §3.1.4.2 normalize 步骤 2)
_FULLWIDTH = (
    "　！＂＃＄％＆＇（）＊＋，－．／０１２３４５６７８９：；＜＝＞？"
    "＠ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ［＼］＾＿"
    "｀ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ｛｜｝～"
)
_HALFWIDTH = (
    " !\"#$%&'()*+,-./0123456789:;<=>?"
    "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_"
    "`abcdefghijklmnopqrstuvwxyz{|}~"
)
_F2H_TABLE = str.maketrans(_FULLWIDTH, _HALFWIDTH)


def normalize(text: str) -> str:
    """文本规范化(spec §3.1.4.2)。

    顺序:NFC → 全角转半角 → 转小写 → 去首尾空白 → 合并内部空白。
    被 source_id / heading_path_id 共用,确保哈希在格式扰动下保持稳定。
    """
    s = unicodedata.normalize("NFC", text)
    s = s.translate(_F2H_TABLE)
    s = s.lower()
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def compute_source_id(file_name: str) -> str:
    """文件名 → 16 位 hex source_id(spec §3.1.4.1)。

    步骤:Path.stem 去扩展名 → normalize → SHA-256 → 前 16 位 hex。
    扩展名不影响;同名重传命中同一 source_id,触发 upsert(非新增)。
    """
    stem = Path(file_name).stem
    norm = normalize(stem)
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    return digest[:_SOURCE_ID_HEX_LEN]


def compute_heading_path_id(titles: list[str]) -> str:
    """标题层级序列 → heading_path_id(spec §3.1.4.2)。

    输入约定:`titles` 是从 H1 到当前层的标题文本列表(已按层级排好序),
    例如 `["第一章 常见症状", "第一节 发热"]`。
    **只拼实际存在的层级,不补空位**;空列表返回空串哈希(顶层无标题兜底)。

    本函数**不**负责 title 层级判定——mineru 输出 title.level 全 1 不可信,C2 chunking
    通过"目录权威清单 + 正文匹配"得到权威 heading_path 后才喂进来(见 §3.1.2)。
    """
    level_ids = [
        hashlib.sha256(normalize(t).encode("utf-8")).hexdigest()
        for t in titles
    ]
    joined = ":".join(level_ids)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def compute_chunk_id(
    source_id: str,
    heading_path_id: str,
    relative_chunk_index: int | str,
) -> str:
    """组合 source + 路径 + 序号 → chunk_id(spec §3.1.4.2)。

    `relative_chunk_index`:
    - 子块:同 heading 路径下从 0 开始的序号(int)
    - 父块:固定串 `"parent"`(spec §3.1.4.2 父块约定),由 `compute_parent_chunk_id` 封装
    """
    payload = f"{source_id}:{heading_path_id}:{relative_chunk_index}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_parent_chunk_id(source_id: str, heading_path_id: str) -> str:
    """父块 ID:固定 `relative_chunk_index="parent"`(spec §3.1.4.2 父块约定)。

    保证同一 heading 节下只产生一个父块 ID,且与所有子块 ID 不冲突
    (子块 rel_idx 是数字,"parent" 是字符串,哈希输入永不相等)。
    """
    return compute_chunk_id(source_id, heading_path_id, _PARENT_REL_INDEX)


def compute_content_hash(chunk_text: str) -> str:
    """child / parent chunk_text → SHA-256 全 hex(spec §3.1.4.3)。

    child / parent 只有一路文本来源(chunk_raw_text),直接 hash 即可。
    table / figure 行因两路来源(chunk_raw_text 走 BM25 + medical_statement 走 dense)
    用 `compute_media_content_hash` 拼起来哈希,见下。
    """
    return hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()


def compute_media_content_hash(chunk_raw_text: str, medical_statement: str) -> str:
    """table / figure 行的 content_hash(spec §3.1.4.3 B 方案)。

    两路文本来源(chunk_raw_text 是 BM25 输入、medical_statement 是 dense original 输入)
    任一变化都要触发重新 embed,所以拼起来一起 hash:

        SHA256( chunk_raw_text + "\\n" + medical_statement )

    分隔符 "\\n" 避免边界混淆(如 `"a" + "b\\nc"` vs `"a\\nb" + "c"`)。
    """
    return hashlib.sha256(
        (chunk_raw_text + "\n" + medical_statement).encode("utf-8")
    ).hexdigest()
