#!/usr/bin/env python3
"""
Spec Sync — splits DEV_SPEC.md into chapter files under auto-coder/references/.

Usage:
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


# Chapter number -> English slug
NUMBER_SLUG_MAP = {
    1: "overview",
    2: "tech-stack",
    3: "rag-pipeline",
    4: "agent-design",
    5: "infrastructure",
    6: "evaluation",
    7: "skills",
    8: "schedule",
}

# DEV_SPEC uses "# N." for top-level chapters, but Section 1.2 is large enough
# to warrant its own file. We split Section 1 into two:
#   Chapter 1 (overview):   from "# 1." to "## 1.2"
#   Chapter 2 (tech-stack): from "## 1.2" to "# 2."
# Sections 2-7 in DEV_SPEC map to chapters 3-8 here.


def detect_chapters(content: str) -> List[Chapter]:
    lines = content.split('\n')
    # Collect split points: (chapter_number, title, line_index)
    splits: List[Tuple[int, str, int]] = []

    for i, line in enumerate(lines):
        # Match "# N. Title" (top-level chapter headers)
        m = re.match(r'^# (\d+)\.\s+(.+)$', line)
        if m:
            sec_num = int(m.group(1))
            title = m.group(2).strip()
            if sec_num == 1:
                # Chapter 1: overview (starts at # 1.)
                splits.append((1, title, i))
            else:
                # DEV_SPEC sections 2-7 -> chapters 3-8
                ch_num = sec_num + 1
                splits.append((ch_num, title, i))
            continue

        # Match "## 1.2" to split tech-stack out of Section 1
        m2 = re.match(r'^## 1\.2\s+(.+)$', line)
        if m2:
            splits.append((2, m2.group(1).strip(), i))

    if not splits:
        raise ValueError("No chapters found. Expected '# N. Title' headers in DEV_SPEC.md")

    # Sort by line position to handle insertion of 1.2 split
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
    repo_root = skill_dir.parent.parent               # project root
    dev_spec  = repo_root / "DEV_SPEC.md"
    specs_dir = skill_dir / "references"
    hash_file = specs_dir / ".spec_hash"

    if not dev_spec.exists():
        print(f"ERROR: {dev_spec} not found"); sys.exit(1)

    # Hash check
    current_hash = hashlib.sha256(dev_spec.read_bytes()).hexdigest()
    if not force and hash_file.exists() and hash_file.read_text().strip() == current_hash:
        print("specs up-to-date"); return

    content = dev_spec.read_text(encoding='utf-8')
    chapters = detect_chapters(content)
    lines = content.split('\n')

    specs_dir.mkdir(parents=True, exist_ok=True)

    # Clean orphans
    old = {f.name for f in specs_dir.glob("*.md")}
    new = {ch.filename for ch in chapters}
    for f in old - new:
        (specs_dir / f).unlink()

    # Write chapters
    for ch in chapters:
        (specs_dir / ch.filename).write_text('\n'.join(lines[ch.start_line:ch.end_line]), encoding='utf-8')

    hash_file.write_text(current_hash)
    print(f"synced {len(chapters)} chapters:")
    for ch in chapters:
        print(f"  {ch.filename} ({ch.line_count} lines) — {ch.cn_title}")


if __name__ == "__main__":
    sync(force="--force" in sys.argv)
