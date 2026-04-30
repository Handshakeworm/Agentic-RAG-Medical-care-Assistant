# 输出模式

当技能需要产出一致、高质量的输出时使用这些模式。

## 模板模式

为输出格式提供模板。根据需求匹配严格程度。

**严格要求时（如 API 响应或数据格式）：**

```markdown
## 报告结构

务必使用以下精确的模板结构：

# [分析标题]

## 摘要
[关键发现的一段话概述]

## 主要发现
- 发现 1 及支持数据
- 发现 2 及支持数据
- 发现 3 及支持数据

## 建议
1. 具体可执行的建议
2. 具体可执行的建议
```

**灵活指导时（需要适应性调整时）：**

```markdown
## 报告结构

以下是合理的默认格式，但请根据实际情况调整：

# [分析标题]

## 摘要
[概述]

## 主要发现
[根据实际发现调整各部分]

## 建议
[根据具体上下文定制]

根据具体分析类型按需调整各部分。
```

## 示例模式

当输出质量依赖于查看示例时，提供输入/输出对：

```markdown
## 提交信息格式

按照以下示例生成提交信息：

**示例 1：**
输入：添加了基于 JWT 令牌的用户认证
输出：
feat(auth): implement JWT-based authentication

Add login endpoint and token validation middleware

**示例 2：**
输入：修复了报告中日期显示不正确的 bug
输出：
fix(reports): correct date formatting in timezone conversion

Use UTC timestamps consistently across report generation

遵循此风格：type(scope): 简要描述，然后是详细说明。
```

示例比单纯的描述更能帮助智能体理解期望的风格和详细程度。
