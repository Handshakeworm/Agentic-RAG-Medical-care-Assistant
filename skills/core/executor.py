"""
Skill 执行器：调用 LLM，解析结构化输出。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from .loader import SkillDef, SkillResult

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 响应中提取 JSON。"""
    match = _JSON_BLOCK_RE.search(text)
    candidate = match.group(1).strip() if match else text.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法从 LLM 响应中解析 JSON:\n{text[:500]}")


class SkillExecutor:
    """
    执行 Skill：渲染 Prompt → 调用 LLM → 解析输出。

    用法：
        executor = SkillExecutor(base_url="http://localhost:8000/v1")
        result = await executor.run(skill, chunk_text="...", heading_path="...")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "not-needed",
        default_model: str = "qwen3.5-9b",
        timeout: int = 60,
        max_retries: int = 2,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self.max_retries = max_retries

    def _build_llm(self, skill: SkillDef, overrides: dict | None = None) -> ChatOpenAI:
        ov = overrides or {}
        return ChatOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.default_model,
            temperature=ov.get("temperature", skill.temperature),
            max_tokens=ov.get("max_tokens", skill.max_tokens),
            timeout=self.timeout,
        )

    async def run(
        self,
        skill: SkillDef,
        model_overrides: dict | None = None,
        **inputs,
    ) -> SkillResult:
        """执行 Skill，返回结构化结果。"""
        # 渲染
        try:
            rendered = skill.render(**inputs)
        except Exception as e:
            return SkillResult.failed(skill.name, skill.version, "", f"渲染失败: {e}")

        # 调用
        llm = self._build_llm(skill, model_overrides)
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await llm.ainvoke([HumanMessage(content=rendered)])
                raw_text = response.content
                output = _extract_json(raw_text)

                # 校验缺失字段
                missing = set(skill.get_output_keys()) - set(output.keys())
                for key in missing:
                    output[key] = None
                if missing:
                    logger.warning("Skill [%s] 输出缺少字段: %s", skill.name, missing)

                return SkillResult(
                    skill_name=skill.name,
                    skill_version=skill.version,
                    output=output,
                    raw_response=raw_text,
                    prompt_rendered=rendered,
                )
            except Exception as e:
                last_error = e
                logger.warning("Skill [%s] 第 %d 次失败: %s", skill.name, attempt, e)

        return SkillResult.failed(
            skill.name, skill.version, rendered,
            f"重试 {self.max_retries} 次后失败: {last_error}",
        )

    def run_sync(self, skill: SkillDef, model_overrides: dict | None = None, **inputs) -> SkillResult:
        import asyncio
        return asyncio.run(self.run(skill, model_overrides, **inputs))
