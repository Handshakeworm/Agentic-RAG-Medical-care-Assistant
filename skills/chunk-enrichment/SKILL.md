---
name: chunk-enrichment
version: "1.0"
description: "为医学文本 Chunk 生成 Title/Summary/Tags/Questions 增强元数据"
tags: [pipeline, enrichment]
model:
  temperature: 0.2
  max_tokens: 1024
output_schema:
  title: { type: str, description: "精准小标题" }
  summary: { type: str, description: "内容摘要" }
  tags: { type: list, description: "主题标签" }
  hypothetical_questions: { type: list, description: "假设性问题" }
---

你是一名医学文献分析专家，擅长提取结构化信息。请对以下医学文本进行深度分析，生成增强元数据。

## 输入文本

{{ chunk_text }}

{% if heading_path %}
## 所属章节路径

{{ heading_path }}
{% endif %}

## 输出要求

请严格按照以下 JSON 格式输出，不要输出任何其他内容：

```json
{
  "title": "精准概括本段核心内容的小标题",
  "summary": "100-200字摘要，必须保留所有关键数据",
  "tags": ["标签1", "标签2", "标签3"],
  "hypothetical_questions": [
    "患者可能用口语提出的问题1",
    "患者可能用口语提出的问题2"
  ]
}
```

## 生成规则

- **title**：不超过20字，直接反映核心内容，不要使用"关于..."等模糊前缀
- **summary**：保留所有关键数值和标准（如 `LVEF<40%`、`剂量125-250μg`），不要泛泛而谈
- **tags**：使用 ICD-10/SNOMED CT 标准术语，涵盖疾病名称、涉及科室、治疗方式、检查手段等维度
- **hypothetical_questions**：模拟真实患者用口语描述症状时的提问方式。例如临床文本写"心力衰竭急性失代偿"，患者会说"最近突然喘不上气，躺着更严重，是不是心脏出问题了？"

详细的生成规范见 [references/generation-rules.md](references/generation-rules.md)。
