"""数据摄取层 Prompt 模板(DEV_SPEC §7.2 → ingestion.py)。

本模块提供 §3.1.3 enrichment 的 prompt 构造函数。Prompt 设计遵循 spec 多处约束:
- §3.1.3.2:单次 LLM 调用产出 3 字段(title / summary / hypothetical_questions;
  tags 字段已废弃 2026-05),summary 同时是 summary 向量的文本来源,
  hypothetical_questions 用临床+口语混合视角弥合 dense_query 与 chunk 的表述差异
- §3.2.1.2:summary / question 文本应"天然带标题路径上下文",所以
  prompt 必须把 heading_path 喂给 LLM 让它知道当前段落讲什么病/什么主题

返回类型为 `list[tuple[str, str]]`(role, content),与 LangChain ChatModel.invoke
原生接受的格式兼容(自动转成 SystemMessage / HumanMessage)。
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────
# SYSTEM:任务说明 + 字段语义 + 约束。固定不变,占大头 prompt 缓存友好。
# ─────────────────────────────────────────────────────────────────────

_ENRICHMENT_SYSTEM = """你是医学知识库片段增强助手。给定一段中文医学教材原文及其所属标题路径(从大章到小节),你需要为这段原文生成 3 个增强字段,用于下游的多向量语义检索。

# 字段说明

## 1. title (chunk 标题)
- 长度 ≤ 30 中文字符
- 精准反映本段原文的核心内容
- 不要简单复用标题路径的末级(那是 chunk 的"位置标签",不是它的"内容标签")
- 例:标题路径末级是"三、临床表现",而本段实际讨论的是"老年患者的非典型症状",title 就写"老年患者的非典型症状"

## 2. summary (chunk 摘要)
- **核心职能**:与 chunk 原文向量**错位互补**的语义抽象——把 heading_path 暗藏的病名/主题/概念**显式写进 summary 文本**,让原文未点出的语境也能被 query 命中
  - 例:原文只说"30 分钟以上,硝酸甘油不缓解",summary 应明写"急性心肌梗死的胸痛持续 30+ 分钟,对硝酸甘油不敏感"——把"心肌梗死"这个隐含主题从 heading_path 显式化到文本里
- **硬约束**(违反就是失败):
  1. **必须以 chunk 的主题(病名/检查项目/概念)为核心组织**,不能只罗列细节
  2. **不要 reorder 原文句子顺序**(reorder 也算复述)→ 必须**重新组织**信息呈现新视角
  3. **严禁元语言**("本段介绍/讨论/概述"等)→ 用医学陈述体直接陈述事实
- **长度参考**(信息量决定,不强求精确字数):
  - 信息量小的 chunk(简单概念/单一事实)→ summary 可短到 50-100 字,只浓缩主题 + 核心点
  - 信息量大的 chunk(鉴别诊断列表/检验参考值表/多维度描述)→ 150-250 字,覆盖关键要素
  - **硬上限 250 字**——超过会与 original 向量过度重叠,浪费多向量错位入口的设计意图
- 内容覆盖:疾病机制 / 临床表现 / 诊断要点 / 治疗方案 / 用药剂量 / 鉴别要素 等(按原文实际内容选)

## 3. hypothetical_questions (假设性问题)
- 输出 2-3 条
- 模拟用户(医生 / 学生 / 患者)在问诊、自查或学习时可能针对本段提出的问题
- **风格混合**:既要有偏临床表述的问法(如"急性心肌梗死与心绞痛在胸痛持续时间上有何鉴别?"),也要有偏患者口语的问法(如"心慌还冒冷汗,会不会是心梗?")。建议 2 条混合分布,如 1 临床 + 1 口语,或 1 临床 + 2 口语。**不要全部口语化**——dense_query 经 Agent ② LLM 改写后是临床表述,完全口语化反而与 dense_query 在向量空间距离远
- 患者口语化时使用日常词:"头疼" 而非 "头痛","拉肚子" 而非 "腹泻","喘不上气" 而非 "呼吸困难"
- 问题应该被本段原文回答得到——不要问原文没讲的内容

# 通用约束

- 严格基于原文事实,不臆造、不外推、不引入原文未提及的诊断/药物/数值
- 即使原文片段较短或信息较稀疏,3 个字段都必须给出(不能为空)
- 中文字符为主;原文出现的英文专业名词(如 ACEI、ICU)可以保留
- **章节标题污染**:原文开头/中间可能出现孤立的章节标题块(如"第一篇 常见症状"、"第三章 心血管疾病"、"二、临床表现"),这些是 OCR 位置标签**不是本段医学内容**,请识别并忽略,只针对真正的医学事实陈述部分生成 3 字段

# 输出格式

严格按以下 JSON 格式输出,**不要任何其他文字、不要 markdown 代码块包裹、不要解释**,直接以 `{` 开始 `}` 结束:

{
  "title": "<≤30 字的本段精准小标题>",
  "summary": "<100-300 字医学陈述体摘要>",
  "hypothetical_questions": ["<问题1(临床或口语)>", "<问题2>", "..."]
}

# 输出示例

下面是一个完整的输入与输出对照,你需要按同样的格式处理用户实际给的 chunk:

【输入】

# 标题路径
第二篇 心血管疾病 > 第三章 冠心病 > 第二节 急性心肌梗死 > 三、临床表现 > (一) 胸痛

# chunk 原文
急性心肌梗死的疼痛部位与心绞痛相似,多位于胸骨后或心前区,可向左肩、左上肢内侧及下颌部放射。性质常呈压榨性、紧缩性或烧灼样,程度较心绞痛剧烈,持续时间多超过 30 分钟,休息或含服硝酸甘油不能缓解。常伴有大汗、恶心呕吐及濒死感。老年及糖尿病患者可表现为乏力、气短等非典型症状,易被漏诊。

【输出】

{"title": "急性心肌梗死的胸痛特征与鉴别要点", "summary": "急性心肌梗死的胸痛与心绞痛的核心鉴别有两点:持续时间超过 30 分钟、含服硝酸甘油不能缓解。疼痛部位与心绞痛相似(胸骨后或心前区,向左肩、左上肢内侧及下颌放射),但性质更剧烈(压榨、紧缩或烧灼样),且常伴大汗、恶心呕吐和濒死感。老年与糖尿病患者可表现为乏力、气短等非典型症状,临床易漏诊。", "hypothetical_questions": ["急性心肌梗死与心绞痛在胸痛持续时间和硝酸甘油反应上有何鉴别?", "我胸口压得慌还冒冷汗,会不会是心梗?", "胸口疼吃了硝酸甘油不管用是不是要赶紧去医院?"]}"""


# ─────────────────────────────────────────────────────────────────────
# USER:本次具体输入(heading_path + chunk 原文)。
# ─────────────────────────────────────────────────────────────────────

_ENRICHMENT_USER_TEMPLATE = """# 标题路径
{heading_path}

# chunk 原文
{chunk_text}

请基于以上原文生成 ChunkEnrichmentOutput 的 3 个字段(title / summary / hypothetical_questions)。"""


def build_chunk_enrichment_prompt(
    heading_path: str,
    chunk_text: str,
) -> list[tuple[str, str]]:
    """构造 enrichment LLM 调用的 messages(spec §3.1.3.2)。

    返回 `[("system", ...), ("user", ...)]`,LangChain ChatModel.invoke 直接接受。
    调用方在 §9.1 try/except/finally 模板内通过
    `chain.invoke(build_chunk_enrichment_prompt(hp, txt), ...)` 使用。
    """
    return [
        ("system", _ENRICHMENT_SYSTEM),
        ("user", _ENRICHMENT_USER_TEMPLATE.format(
            heading_path=heading_path,
            chunk_text=chunk_text,
        )),
    ]


# ─────────────────────────────────────────────────────────────────────
# *_summary chunk 单步全字段产出(§3.1.2 / 图表等处理方式.md §5):
#
# 2026-05-08 决策从两步合一为单步:LLM 全程持有原始信息源(vision LLM 持图、
# text LLM 持 html),一次产出 4 个字段——medical_statement(作为 chunk_raw_text)+
# title + summary + hypothetical_questions(对齐 ChunkEnrichmentOutput 的语义)。
#
# 改单步原因:two-stage 链路里,Stage 2 enrichment 看不到原图,无法纠正 Stage 1
# 的视觉幻觉,反而把单点错误固化扩散到 4 字段全错。改单步让所有字段都直接 ground
# 在原始信息源(图/html),避免错误传播 + 节省 1007 次 deepseek 调用。
#
# 拆成两个专用 system prompt:
# - _FIGURE_SUMMARY_TABLE_SYSTEM: text LLM 看 html,无视觉负担
# - _FIGURE_SUMMARY_VISION_SYSTEM: 多模态 LLM 看截图,含"认图→抽要素→转写"三步法
#
# 两个 system prompt 都包含完整的 4-field JSON 输出指引(SHARED_4FIELD_TAIL)。
# ─────────────────────────────────────────────────────────────────────


# ───────── 共享:4 字段输出指引(两个 prompt 末尾都拼这段) ─────────


_SHARED_4FIELD_TAIL = """

# 你需要同时产出的 4 个字段(下游 *_summary chunk 全部入库 + 多向量化用)

## 1. medical_statement(100-300 字)— **作为 chunk 原文入库**
依上文医学陈述体规则,从源数据(图/html)直接陈述医学事实。这是 chunk 的 chunk_raw_text,
是后续 BM25 稀疏向量、原文密集向量、前端展示的底层文本。

## 2. title(≤30 中文字符)— **chunk 标题**
- 精准反映本图/表的核心**内容**(不是位置标签)
- **不要简单复用 heading_path 的末级**——那是 chunk 的"位置标签",不是它的"内容标签"
- 例:heading_path 末级是"三、临床表现",图/表实际讲的是"老年患者的非典型症状",title 就写"老年患者的非典型症状"

## 3. summary(80-250 字)— **跟 medical_statement 错位互补的语义抽象**
- **核心职能**:跟 medical_statement 向量错位互补——把 heading_path 暗藏的病名/主题/概念**显式写进 summary 文本**,让原文未点出的语境也能被 query 命中
  - 例:medical_statement 只说"持续 30 分钟以上,硝酸甘油不缓解",summary 应明写"急性心肌梗死的胸痛持续 30+ 分钟,对硝酸甘油不敏感"——把"心肌梗死"这个隐含主题从 heading_path 显式化到文本里
- **硬约束**:
  1. **必须以 chunk 的主题(病名/检查项目/概念)为核心组织**,不能只罗列细节
  2. **不要 reorder medical_statement 的句子顺序**(reorder 也算复述)→ **重新组织**信息呈现新视角
  3. **严禁元语言**("本段介绍/讨论/概述"等)→ 用医学陈述体直接陈述事实
- **长度**:80-250 字,信息量决定;**硬上限 250 字**(超过会与 medical_statement 向量过度重叠)

## 4. hypothetical_questions(2-3 条)— **HyDE 反向问题**
- 模拟用户(医生 / 学生 / 患者)在问诊、自查或学习时可能针对本图/表提的问题
- **风格混合 + 比例建议**:既要有偏临床表述(如"急性心肌梗死与心绞痛在胸痛持续时间上有何鉴别?"),也要有偏患者口语("心慌还冒冷汗,会不会是心梗?")。建议 2 条混合分布,如 1 临床 + 1 口语,或 1 临床 + 2 口语。**不要全部口语化**——线上 dense_query 经 Agent ② LLM 改写后是临床表述,完全口语化的 question 反而与 dense_query 在向量空间距离远,反作用
- 患者口语化时使用日常词:"头疼" 而非 "头痛","拉肚子" 而非 "腹泻","喘不上气" 而非 "呼吸困难"
- 问题应该被本图/表的内容回答得到——不要问图/表没讲的内容

# 通用约束(4 字段共同适用)

- **严格基于源数据(图/html)事实**,不臆造、不外推、不引入未提及的诊断/药物/数值/机制
- **即使源数据信息稀疏**(如只画了一个示意框),4 个字段都必须给出(不能为空)。极简情况下 medical_statement 可短到 80 字,但仍要写出图/表呈现的核心事实
- **中文字符为主;英文专业名词(如 ECG、MRI、HER2、cTnI、CYP11B2、ACEI、PCI 等)直接保留原文,不要强翻译成中文**

# 输出格式(严格 JSON,**不要任何其他文字、不要 markdown 代码块包裹、不要解释**)

```
{
  "medical_statement": "<100-300 字医学陈述体>",
  "title": "<≤30 字内容标题>",
  "summary": "<80-250 字错位互补摘要>",
  "hypothetical_questions": ["<问题1>", "<问题2>", "<问题3>"]
}
```
"""


# ───────── table 专用 system prompt(body + 共享 4 字段 tail) ─────────


_FIGURE_SUMMARY_TABLE_SYSTEM = """你是医学教科书写作助手。给定一张医学表格的 html、所属章节路径、caption,你需要同时产出 4 个字段(详见下方"4 字段说明"),作为本表 *_summary chunk 入库及多向量检索的全套元数据。

# 任务边界

- 你**不是**在描述表格本身,而是在陈述它**承载的医学知识**(诊断标准 / 鉴别诊断对照 / 实验室参考值 / 用药剂量 / 治疗方案分级 等)
- mineru 对 html 表格的转录通常**完整可信**(数值、列对齐、单位都准),直接基于 html 陈述即可
- 复杂表格(三层表头 / 合并单元格 / 并排对比)请先理清表格主题再组织 medical_statement
- **caption 杂质处理**:caption 字段有时被 mineru 拼了上下文正文片段(如 `皮质醇代谢和盐皮质激素受体 表 2-6-11-6 高血压相关基因的作用位点与途径`),请只识别 `表 N-X` 模式后的真表格标题作为本表内容指引,前后无关文字忽略

# 硬约束(违反就是失败)

1. **必须以本表的医学主题为核心**(由 heading_path + caption 推断),不只是逐行罗列
2. **严禁元语言**:不用"本表列出"、"该表给出"、"表中显示"、"如表所示"等表说语 → 直接用医学陈述体
3. **严禁臆造**:只陈述 html 中明确呈现的内容,不要外推未提及的诊断/药物/数值/机制
4. **必须利用 heading_path 上下文**:把章节暗藏的病名/主题显式写进 medical_statement
   - 例:heading_path 是"急性心梗 > 实验室检查",caption 是"表 3-1 心肌损伤标志物",medical_statement 应明写"急性心肌梗死的心肌损伤标志物动力学特征..."

# 大表的组织策略

- **列对比表**(如鉴别诊断 A vs B vs C):提炼差异维度,用对比句式陈述
- **数值参考表**(如肝功能正常值):按指标分组陈述,关键阈值 + 临床意义
- **用药剂量表**:按疾病/分期组织,关键剂量 + 适应证""" + _SHARED_4FIELD_TAIL + """

# 输出示例(table)

【输入】
heading_path: 第二篇 心血管疾病 > 第三章 冠心病 > 第二节 急性心肌梗死 > 五、实验室检查
caption: 表 3-1 心肌损伤标志物动力学比较
html(简化示意):
<table>
  <tr><th>标志物</th><th>开始升高</th><th>峰值时间</th><th>持续时间</th></tr>
  <tr><td>肌红蛋白</td><td>1-3h</td><td>6-12h</td><td>24-36h</td></tr>
  <tr><td>cTnI/cTnT</td><td>3-6h</td><td>10-24h</td><td>7-14d</td></tr>
  <tr><td>CK-MB</td><td>3-8h</td><td>10-24h</td><td>2-4d</td></tr>
</table>

【输出】
{"medical_statement": "急性心肌梗死的主要心肌损伤标志物在动力学时相上各有特征。肌红蛋白起病后 1-3 小时即开始升高,6-12 小时达峰,持续时间 24-36 小时。心肌肌钙蛋白(cTnI / cTnT)起病 3-6 小时升高,10-24 小时达峰,可持续 7-14 天。CK-MB 起病 3-8 小时升高,10-24 小时达峰,持续 2-4 天。三类标志物在升高起点、峰值时点与持续时长上的差异,构成了急性心肌梗死病程不同阶段的实验室判断依据。", "title": "心肌损伤标志物的动力学时相差异", "summary": "急性心肌梗死的实验室诊断依赖三类心肌损伤标志物的动力学时相互补:肌红蛋白起病早期最敏感(1-3 小时即升高)但持续短(24-36 小时);cTnI / cTnT 在 3-6 小时升高且持续 7-14 天,兼具高敏感性、特异性与较长诊断窗口;CK-MB 持续 2-4 天可辅助评估再梗死。", "hypothetical_questions": ["急性心肌梗死时哪个心肌损伤标志物升高最早?", "胸痛怀疑心梗时,几小时后查 cTnI 最有诊断价值?", "心梗复发了去医院抽血查什么?"]}"""


# ───────── chart / figure 多模态专用 system prompt(body + 共享 4 字段 tail) ─────────


_FIGURE_SUMMARY_VISION_SYSTEM = """你是医学教科书写作助手。给定一张医学图(数据图 chart / 流程图或示意图 figure)的截图、所属章节路径、caption,你需要同时产出 4 个字段(详见下方"4 字段说明"),作为本图 *_summary chunk 入库及多向量检索的全套元数据。

# 三步看图法

**第一步:认图**。先看清截图主体类型(决定下一步抽什么)——
- 数据图(chart):温度曲线 / 心电图 / 血糖谱 / 实验室趋势 / 散点 / 柱状对比 / 雷达图
- 流程图(figure-flowchart):诊断决策树 / 治疗路径 / 鉴别诊断流程 / 给药方案
- 解剖示意 / 病理示意 / 设备示意 / 操作示意

**第二步:抽视觉要素**。针对图类型抽取关键信息——
- 数据图:坐标轴标注、关键数值/区间、曲线特征(峰值、平台、波形形态、双相/单相)、对比组差异
- 流程图:起点(初始症状/检查) → 关键判断点(阈值 / 检验结果 / 影像表现) → 终点(诊断 / 治疗决策);各分支条件
- 解剖/病理示意:被标注的结构名、空间关系、病变特征(肿块大小、形状、密度、强化方式)

**第三步:转写医学陈述**。把抽到的要素重新组织,以本图所在章节(heading_path)的医学主题为核心,直接陈述事实。

# 输入处理提示

- **caption 杂质**:caption 字段有时被 mineru 拼了上下文正文片段(如 `皮质醇代谢和盐皮质激素受体 图 2-6-11-6 高血压相关基因的作用位点与途径`),请只识别 `图 N-X` 模式后的真图标题作为本图内容指引,前后无关文字忽略
- **多图输入**(可能出现):若 user message 含多张截图,这些是同一概念图(同 caption)的不同 panel,请综合所有截图产出**一段统一**的 medical_statement,而不是分别描述每个 panel

# 硬约束(违反就是失败)

1. **必须以本图的医学主题为核心**(由 heading_path + caption 推断,不是图的视觉主题)
2. **严禁元语言**:不用"图中显示"、"截图描绘"、"本图展示"、"流程图表明"、"如图所示" → 直接用医学陈述体
3. **严禁臆造**:只陈述截图中明确可见的内容,不要外推未呈现的诊断/药物/数值/机制
4. **必须利用 heading_path 上下文**:把章节暗藏的病名/主题显式写进 medical_statement
   - 例:heading_path 是"心血管 > 急性心梗 > 心电图",caption 是"图 3-2",medical_statement 应写"急性心肌梗死的心电图特征:...",而不是"该心电图显示..."
5. **不要写出"第一步"/"认图"/"抽要素"等步骤标题或过程**——三步法是你脑内的推理顺序,不是输出结构""" + _SHARED_4FIELD_TAIL + """

# 输出示例(chart 单图)

【输入】
heading_path: 第一篇 常见症状 > 第一节 发热
caption: 图 1-1 稽留热
[截图:横轴日期、纵轴体温;曲线持续在 39-40℃ 区间小幅波动,昼夜温差 < 1℃,持续约 12 天后骤降]

【输出】
{"medical_statement": "稽留热是发热的一种形态,特征为体温持续维持在 39-40℃ 高温区间,24 小时内波动幅度不超过 1℃,且持续多日不退。临床常见于大叶性肺炎、伤寒高热极期、斑疹伤寒等急性感染性疾病。", "title": "稽留热的高位平台体温形态", "summary": "在常见症状的发热分类中,稽留热以体温在 39-40℃ 维持高位平台、24 小时波动 < 1℃ 为核心特征,与弛张热(波动 > 2℃ 但不退至正常)、间歇热(高热与无热交替)的鉴别要点正在于此。临床多见于大叶性肺炎、伤寒高热极期、斑疹伤寒等急性感染性疾病。", "hypothetical_questions": ["稽留热的体温曲线特征是什么?", "稽留热与弛张热在体温波动上如何鉴别?", "持续高烧不退还差不多 39 度多怎么办?"]}"""


# ───────── user template ─────────


_FIGURE_SUMMARY_TABLE_USER_TEMPLATE = """# 所属章节
{heading_path}

# caption
{caption}

# 表格类型
{mineru_sub_type}

# html 表格内容
{content}

请基于以上 html 表格,产出 4 字段 JSON(medical_statement / title / summary / hypothetical_questions)。"""


_FIGURE_SUMMARY_VISION_USER_TEMPLATE = """# 所属章节
{heading_path}

# caption
{caption}

# 图类型
{chunk_kind}({mineru_sub_type})

请按"认图 → 抽视觉要素 → 转写医学陈述"三步内化在脑内推理,基于下方截图产出 4 字段 JSON(medical_statement / title / summary / hypothetical_questions)。"""


def build_figure_summary_text_prompt(
    heading_path: str,
    caption: str,
    mineru_sub_type: str,
    content: str,
) -> list[tuple[str, str]]:
    """table 专用(纯文本输入,html → medical statement)。

    chart/figure 用 build_figure_summary_multimodal_messages。
    """
    return [
        ("system", _FIGURE_SUMMARY_TABLE_SYSTEM),
        ("user", _FIGURE_SUMMARY_TABLE_USER_TEMPLATE.format(
            heading_path=heading_path,
            caption=caption or "(无 caption)",
            mineru_sub_type=mineru_sub_type or "(无 sub_type)",
            content=content or "(html 为空)",
        )),
    ]


def build_figure_summary_multimodal_messages(
    heading_path: str,
    caption: str,
    chunk_kind: str,
    mineru_sub_type: str,
    images_b64: list[tuple[str, str]],
) -> list[dict]:
    """chart / figure 专用:多模态消息,user 块含 text + N 张 image_url。

    多面板合并(scripts/merge_multipanel_figures.py)后,anchor 块拿到全组所有
    截图;standalone 块只有一张。统一接口:`images_b64: list[(b64, mime)]`。

    OpenAI-compatible 多模态格式(LangChain ChatOpenAI 直接接受 list[dict])。
    DashScope qwen3.5-plus 接收 base64 data URL,支持单轮多 image_url。

    注:不再喂 mineru 的 markdown/mermaid 转录给视觉模型——转录质量太差时反而会
    引发模型对截图与转录冲突的 explicit reasoning(实测 #22 骨科 figure 的
    thinking-mode 溢出就是这么来的)。改成纯截图输入。
    """
    n_imgs = len(images_b64)
    multi_panel_note = ""
    if n_imgs > 1:
        multi_panel_note = (
            f"\n\n# 子面板数: {n_imgs}\n"
            "本图是 multi-panel 组合,以下 {n_imgs} 张截图为同一概念图("
            "同一 caption 下)的不同子面板。请**综合所有子面板**产出**一段统一**的"
            "医学陈述,而不是分别描述每个子面板。"
        ).format(n_imgs=n_imgs)

    user_text = _FIGURE_SUMMARY_VISION_USER_TEMPLATE.format(
        heading_path=heading_path,
        caption=caption or "(无 caption)",
        chunk_kind=chunk_kind,
        mineru_sub_type=mineru_sub_type or "(无 sub_type)",
    ) + multi_panel_note

    content_parts: list[dict] = [{"type": "text", "text": user_text}]
    for b64, mime in images_b64:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })

    return [
        {"role": "system", "content": _FIGURE_SUMMARY_VISION_SYSTEM},
        {"role": "user", "content": content_parts},
    ]
