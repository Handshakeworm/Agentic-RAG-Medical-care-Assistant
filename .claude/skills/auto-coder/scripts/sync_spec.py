#!/usr/bin/env python3
"""
规范同步 — 将 DEV_SPEC.md 按章节拆分为 auto-coder/references/ 下的独立文件。

用法：
    python scripts/sync_spec.py [--force]
"""

import hashlib
import re
import sys
from pathlib import Path
from typing import List, Tuple, NamedTuple


class Chapter(NamedTuple):
    number: int
    cn_title: str
    filename: str
    start_line: int
    end_line: int
    line_count: int


# 章节编号 -> 英文缩略名（与 DEV_SPEC 顶级章节 1:1 对应）
NUMBER_SLUG_MAP = {
    1: "overview",
    2: "tech-stack",
    3: "rag-pipeline",
    4: "agent-design",
    5: "infrastructure",
    6: "evaluation",
    7: "prompts",
    8: "schedule",
    9: "contracts",
}


def detect_chapters(content: str) -> List[Chapter]:
    lines = content.split('\n')
    # 收集分割点: (章节编号, 标题, 行索引)
    splits: List[Tuple[int, str, int]] = []

    in_code_block = False  # 跟踪围栏式代码块状态（```...```），避免把代码注释里的 "# 1." 当作章节

    for i, line in enumerate(lines):
        # 围栏式代码块边界切换（允许 ```python / ```text 等语言标识）
        if re.match(r'^```', line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # 匹配 "# N. 标题" 或 "# N 标题"（顶级章节标题，句号可选）
        m = re.match(r'^# (\d+)\.?\s+(.+)$', line)
        if m:
            sec_num = int(m.group(1))
            title = m.group(2).strip()
            splits.append((sec_num, title, i))

    if not splits:
        raise ValueError("未找到章节。DEV_SPEC.md 中应包含 '# N. 标题' 或 '# N 标题' 格式的标题")

    # 按行位置排序
    splits.sort(key=lambda x: x[2])

    chapters = []
    for idx, (num, title, start) in enumerate(splits):
        end = splits[idx + 1][2] if idx + 1 < len(splits) else len(lines)
        slug = NUMBER_SLUG_MAP.get(num, f"chapter-{num}")
        filename = f"{num:02d}-{slug}.md"
        chapters.append(Chapter(num, title, filename, start, end, end - start))

    return chapters


def sync(force: bool = False):
    skill_dir = Path(__file__).parent.parent          # auto-coder/
    repo_root = skill_dir.parent.parent.parent        # 项目根目录
    dev_spec  = repo_root / "DEV_SPEC.md"
    specs_dir = skill_dir / "references"
    hash_file = specs_dir / ".spec_hash"

    if not dev_spec.exists():
        print(f"错误：{dev_spec} 未找到"); sys.exit(1)

    # 哈希检查
    current_hash = hashlib.sha256(dev_spec.read_bytes()).hexdigest()
    if not force and hash_file.exists() and hash_file.read_text().strip() == current_hash:
        print("规范已是最新状态"); return

    content = dev_spec.read_text(encoding='utf-8')
    chapters = detect_chapters(content)
    lines = content.split('\n')

    specs_dir.mkdir(parents=True, exist_ok=True)

    # 清理孤立文件
    old = {f.name for f in specs_dir.glob("*.md")}
    new = {ch.filename for ch in chapters}
    for f in old - new:
        (specs_dir / f).unlink()

    # 写入章节文件
    for ch in chapters:
        (specs_dir / ch.filename).write_text('\n'.join(lines[ch.start_line:ch.end_line]), encoding='utf-8')

    hash_file.write_text(current_hash)
    print(f"已同步 {len(chapters)} 个章节：")
    for ch in chapters:
        print(f"  {ch.filename} ({ch.line_count} 行) — {ch.cn_title}")


if __name__ == "__main__":
    sync(force="--force" in sys.argv)
