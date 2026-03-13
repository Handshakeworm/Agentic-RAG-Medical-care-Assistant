"""
Skill 注册中心：自动发现所有 Skill 文件夹（含 SKILL.md 的目录）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from .loader import SkillLoader, SkillDef

logger = logging.getLogger(__name__)

# 默认扫描：skills/ 目录下所有含 SKILL.md 的子文件夹
_DEFAULT_SKILLS_ROOT = Path(__file__).parent.parent


class SkillRegistry:
    """
    自动发现并管理所有 Skill。

    用法：
        registry = SkillRegistry()           # 扫描默认目录
        skill = registry.get("chunk-enrichment")
        registry.reload()                    # 热重载
    """

    def __init__(self, skills_root: str | Path | None = None):
        self._root = Path(skills_root) if skills_root else _DEFAULT_SKILLS_ROOT
        self._skills: dict[str, SkillDef] = {}
        self._load_all()

    def _load_all(self) -> None:
        self._skills.clear()
        if not self._root.exists():
            logger.warning("Skills 根目录不存在: %s", self._root)
            return

        for candidate in sorted(self._root.iterdir()):
            skill_md = candidate / "SKILL.md"
            if candidate.is_dir() and skill_md.exists():
                try:
                    skill = SkillLoader.load(candidate)
                    self._skills[skill.name] = skill
                    logger.info("已加载 Skill: %s v%s (%s)", skill.name, skill.version, candidate.name)
                except Exception as e:
                    logger.error("加载失败 [%s]: %s", candidate.name, e)

    def reload(self) -> None:
        """热重载所有 Skill。"""
        logger.info("重新加载 Skills...")
        self._load_all()

    def get(self, name: str) -> SkillDef:
        if name not in self._skills:
            available = ", ".join(self._skills.keys())
            raise KeyError(f"Skill '{name}' 不存在。可用: [{available}]")
        return self._skills[name]

    def filter_by_tag(self, tag: str) -> list[SkillDef]:
        return [s for s in self._skills.values() if tag in s.tags]

    def list_all(self) -> list[dict]:
        return [
            {"name": s.name, "version": s.version, "description": s.description, "tags": s.tags}
            for s in self._skills.values()
        ]

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self) -> Iterator[SkillDef]:
        return iter(self._skills.values())
