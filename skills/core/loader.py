"""
SKILL.md 加载器：解析 YAML frontmatter + Markdown body。

官方 Skill 结构：
    your-skill-name/
    ├── SKILL.md          # 必需 — YAML头(元数据) + 正文(Prompt指令)
    ├── scripts/          # 可选 — 执行过程中用到的脚本
    ├── references/       # 可选 — 相关文档、说明
    └── assets/           # 可选 — 模板、静态资源
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Template, StrictUndefined

logger = logging.getLogger(__name__)

# 匹配 YAML frontmatter: --- 开头到 --- 结束
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class SkillDef:
    """一个 Skill 的完整定义，从 SKILL.md 加载。"""

    # --- 来自 YAML frontmatter ---
    name: str
    version: str
    description: str
    tags: list[str] = field(default_factory=list)
    model: dict[str, Any] = field(default_factory=dict)  # temperature, max_tokens 等
    output_schema: dict[str, Any] = field(default_factory=dict)

    # --- 来自 Markdown body ---
    body: str = ""  # 完整正文（即 Prompt 模板，Jinja2 语法）

    # --- 目录信息 ---
    skill_dir: Path | None = None  # Skill 文件夹路径

    @property
    def temperature(self) -> float:
        return self.model.get("temperature", 0.2)

    @property
    def max_tokens(self) -> int:
        return self.model.get("max_tokens", 1024)

    @property
    def scripts_dir(self) -> Path | None:
        if self.skill_dir and (self.skill_dir / "scripts").is_dir():
            return self.skill_dir / "scripts"
        return None

    @property
    def references_dir(self) -> Path | None:
        if self.skill_dir and (self.skill_dir / "references").is_dir():
            return self.skill_dir / "references"
        return None

    @property
    def assets_dir(self) -> Path | None:
        if self.skill_dir and (self.skill_dir / "assets").is_dir():
            return self.skill_dir / "assets"
        return None

    def render(self, **kwargs) -> str:
        """渲染 body 中的 Jinja2 模板变量。"""
        template = Template(self.body, undefined=StrictUndefined)
        return template.render(**kwargs)

    def get_output_keys(self) -> list[str]:
        return list(self.output_schema.keys())

    def read_reference(self, filename: str) -> str:
        """读取 references/ 下的文档内容。"""
        ref_dir = self.references_dir
        if not ref_dir:
            raise FileNotFoundError(f"Skill [{self.name}] 没有 references/ 目录")
        path = ref_dir / filename
        return path.read_text(encoding="utf-8")

    def read_asset(self, filename: str) -> str:
        """读取 assets/ 下的模板或资源。"""
        asset_dir = self.assets_dir
        if not asset_dir:
            raise FileNotFoundError(f"Skill [{self.name}] 没有 assets/ 目录")
        path = asset_dir / filename
        return path.read_text(encoding="utf-8")


@dataclass
class SkillResult:
    """Skill 执行结果。"""
    skill_name: str
    skill_version: str
    output: dict[str, Any]
    raw_response: str
    prompt_rendered: str
    success: bool = True
    error: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.output.get(key, default)

    @staticmethod
    def failed(name: str, version: str, prompt: str, error: str) -> SkillResult:
        return SkillResult(
            skill_name=name, skill_version=version,
            output={}, raw_response="", prompt_rendered=prompt,
            success=False, error=error,
        )


class SkillLoader:
    """
    从 SKILL.md 文件加载 Skill 定义。

    SKILL.md 格式：
        ---
        name: chunk-enrichment
        version: "1.0"
        description: "为 Chunk 生成增强元数据"
        tags: [pipeline]
        model:
          temperature: 0.2
          max_tokens: 1024
        output_schema:
          title: { type: str }
          summary: { type: str }
        ---

        （以下为 Markdown 正文，即 Prompt 模板）
        你是一名医学文献分析专家...
    """

    @staticmethod
    def load(skill_dir: str | Path) -> SkillDef:
        """从 Skill 文件夹加载，文件夹下必须有 SKILL.md。"""
        skill_dir = Path(skill_dir)
        skill_md = skill_dir / "SKILL.md"

        if not skill_md.exists():
            raise FileNotFoundError(f"找不到 {skill_md}")

        raw = skill_md.read_text(encoding="utf-8")

        # 分离 frontmatter 和 body
        match = _FRONTMATTER_RE.match(raw)
        if not match:
            raise ValueError(f"SKILL.md 缺少 YAML frontmatter: {skill_md}")

        frontmatter = yaml.safe_load(match.group(1))
        body = raw[match.end():].strip()

        return SkillDef(
            name=frontmatter["name"],
            version=frontmatter.get("version", "0.1"),
            description=frontmatter.get("description", ""),
            tags=frontmatter.get("tags", []),
            model=frontmatter.get("model", {}),
            output_schema=frontmatter.get("output_schema", {}),
            body=body,
            skill_dir=skill_dir,
        )
