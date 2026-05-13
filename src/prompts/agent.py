"""src/prompts/agent.py — Agent 各 LLM 调用点的 prompt 构造函数(DEV_SPEC §4.1.2)。

每个函数返回**一段文本字符串**(消息内容),由调用方包成 LangChain 消息后
喂给 `chain.invoke(prompt, config={...})`。

设计约定:
- 一个调用点一个函数,函数名与 §9.3 调用点名对应
- 函数签名只接 plain Python 数据(str / list / dict),不依赖 MedicalState
  类型 —— 调用方从 state 抽字段后传入,prompt 模块不感知 state 对象
- prompt 内联 schema 字段说明,降低对 §9.5 的查阅依赖;LLM 自己从
  `with_structured_output` 拿严格 schema,prompt 文本作为补充语义提示
- 多模态调用(① .5 / ⑨ / ⑩ Step 1)返回 `(messages, prompt_text)` 二元组:
  `messages: list[BaseMessage]` 供 chain.invoke 消费(含图);
  `prompt_text: str` 供 §9.6 `final_prompt` 审计存档(纯文本镜像)

每个 prompt 都尽量短小聚焦:同一节点不同 step 的 prompt 各自独立,避免一个
"上帝 prompt"什么都管。
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage


# ────────────────────────────────────────────────────────────────────────────
# ① info_collect Step 1
# ────────────────────────────────────────────────────────────────────────────


def build_info_collect_prompt(patient_input: str) -> str:
    """① info_collect Step 1:从 patient_input 提取主诉 + 现病史 + 13 维槽位。

    输出由 `InfoCollectOutput` schema 严格约束,LLM 只需聚焦"提取什么、不
    提取什么"。
    """
    return f"""你是医院分诊台问诊助手。下面是患者的自述。请仅从该自述中提取本次就诊的信息,
**不要自行编造、不要泛化、不要补充未提及的内容**。

【患者自述】
{patient_input}

【提取要求】
1. chief_complaint(主诉):主要症状 + 持续时间,1 句话,例:"腹痛3天"
2. present_illness(现病史):用 1-3 句话展开本次发病的:起病时间、诱因、症状特点
   (部位/性质/程度)、伴随症状、加重/缓解因素、治疗经过
3. present_illness_slots(13 维结构化槽位):
   - 单值槽(str | None):onset_time / onset_mode / trigger / location / nature /
     severity / duration_pattern / progression / treatment_tried / treatment_response
   - 多值槽(list[str]):aggravating(加重因素) / relieving(缓解因素) /
     associated_symptoms(伴随症状)
   - 患者**未提及**的维度严格保持 None / 空列表,**不要瞎填**

注意:这是初诊采集,信息缺失是正常的,后续会通过追问补全。"""


# ────────────────────────────────────────────────────────────────────────────
# ①.5 analyze_initial_reports / ⑨ process_exam_result
# ────────────────────────────────────────────────────────────────────────────


def build_report_parsing_prompt(num_reports: int) -> str:
    """①.5 / ⑨ 多模态 LLM 直读报告 → 结构化关键发现。

    Args:
        num_reports: 本次解析的报告数量,prompt 中提示 LLM 输出对应数量的 finding 项

    多模态附件(图片 base64 / PDF 文件)由调用方组装到 LangChain message 中,
    本函数只产文本提示部分。
    """
    return f"""你是医学报告结构化解析助手。下面附了 {num_reports} 份检查报告(图片或 PDF)。
请逐份解析,产出结构化的 ReportFindings 列表,每份报告对应 findings 列表中的一项。

【提取规则】
- report_type:从 ['blood_routine','urine_routine','biochemistry','imaging','ecg',
  'physical_exam','pathology','other'] 中选最贴切的
- report_date:从报告头/落款抽取日期,YYYY-MM-DD 格式;识别不到 → null
- abnormal_values:**保留原始数值**,如 "WBC 12.3×10⁹/L↑" "Hb 85g/L↓",不要意译为
  "白细胞高"
- impressions:报告诊断印象原文,如 "右肺上叶磨玻璃结节"
- positive_findings:阳性发现 + **异常值的临床解读**(如 WBC↑ → "白细胞升高"、
  Hb↓ → "贫血"),用医学文献语言,可直接用于 query 召回
- negative_findings:阴性发现 / 已排除项,如 "未见肝内胆管扩张"、"肝功能正常"

报告本身已是标准医学术语,**不需要做实体链接**,直接读图/读字面提取。
若某类发现报告中不存在,对应字段返回空列表,不要编造。"""


# ────────────────────────────────────────────────────────────────────────────
# ② build_query Step 1 / 2 / 4
# ────────────────────────────────────────────────────────────────────────────


def build_ner_prompt(text: str) -> str:
    """② build_query Step 1:LLM NER 从文本抽取医学实体。"""
    return f"""你是医学命名实体识别助手。请从下面的患者陈述中抽取**医学实体**。

【输入文本】
{text}

【实体类型】(entity_type 取值)
- symptom(症状):如"头痛"、"恶心"、"胸闷"
- disease(疾病):如"糖尿病"、"高血压"
- drug(药物):如"二甲双胍"、"奥美拉唑"
- anatomy(解剖部位):如"右上腹"、"胸骨后"

【字段说明】
- text:实体原文(保留患者口语,不归一)
- entity_type:实体类型(见上)
- negation:是否被否定。如"没有发烧" → True;"发烧" → False
- temporality:时间属性。current(本次/当前) / past(既往) / family(家族)
- value:量化值,如体温 "38.5°C"、持续时间 "3天";无则 null

不要重复抽取同一实体的不同表述(如同时抽"肚子疼"和"腹痛"),保留患者原始表述即可——
后续 Step 2 Entity Linking 会做术语标准化。"""


def build_entity_linking_prompt(
    original_text: str, candidates: list[dict]
) -> str:
    """② build_query Step 2:从 terms_collection Top-5 候选中选最匹配的标准术语。

    Args:
        original_text: NER 抽出的实体原始文本(如"肚子疼")
        candidates: terms_collection.search_aliases 产出的 Top-5 候选,每项含
                    concept_id / preferred_term / alias / score / icd10 / category

    LLM 输出 EntityLinkingMatch(本实体一项),`matches` 由调用方聚合多个实体后传给
    `with_structured_output(EntityLinkingResult)`。
    """
    cand_lines = []
    for i, c in enumerate(candidates, start=1):
        cand_lines.append(
            f"{i}. concept_id={c.get('concept_id')} | preferred_term={c.get('preferred_term')}"
            f" | alias={c.get('alias')} | category={c.get('category')}"
            f" | score={c.get('score'):.4f}"
        )
    cand_block = "\n".join(cand_lines) if cand_lines else "(无候选)"

    return f"""你是医学术语链接助手。下面是患者用词与术语库 Top-5 候选,请选择**语义最贴近**的一条
作为标准术语;若都不贴近,选择"无匹配"。

【患者用词】
{original_text}

【Top-5 候选】
{cand_block}

【输出规则】
- 选定一条 → original_text=患者用词原文,concept_id / preferred_term 取该候选,
  confidence ∈ [0.5, 1.0] 反映匹配把握
- 无匹配 → concept_id=null, preferred_term=null, confidence ∈ [0, 0.5)

注意:
- "肚子疼"与"腹痛"都映射到 R10.4 / "腹痛" — 患者口语 → 标准术语是常见情况
- 部位修饰(如"右上腹疼")可降级匹配到更宽泛的"腹痛",但 confidence 应略低
- "胸闷"与"胸痛"是不同症状,不要混淆"""


def build_query_construction_prompt(
    confirmed_symptoms: list[str],
    medical_history_summary: str,
    report_positive: list[str],
    report_impressions: list[str],
    filled_slots: dict[str, Any],
    sparse_queries_preview: list[str],
) -> str:
    """② build_query Step 4:LLM 整合证据 → 改写 dense_query;sparse_queries 预填供参考。

    Sparse 路实际词袋由 `query_processing.build_sparse_queries` 确定性产出,
    LLM 只是把它列出来确认 schema 合规;dense_query 是 LLM 真正需要"创作"的字段。
    """
    slots_lines = [f"  - {k}: {v}" for k, v in filled_slots.items() if v]
    slots_block = "\n".join(slots_lines) if slots_lines else "  (无)"

    sparse_preview = "\n".join(f"  - {q}" for q in sparse_queries_preview) or "  (无)"
    pos_block = "; ".join(report_positive) or "(无)"
    imp_block = "; ".join(report_impressions) or "(无)"
    sym_block = "、".join(confirmed_symptoms) or "(无)"

    return f"""你是医学检索 query 改写助手。请把患者已确认的证据整合成**一句语义连贯的自然语言查询**,
便于 Dense 向量检索召回相关医学文献 chunk。

【已确认症状】
{sym_block}

【现病史已填维度】
{slots_block}

【报告阳性发现】
{pos_block}

【报告诊断印象】
{imp_block}

【病史关键摘要】
{medical_history_summary or "(无)"}

【Dense Query 要求】
- 用医学文献风格,而不是患者口语
- 长度 ≤ 60 字,信息密度高,把鉴别特征写出来(如"进食后加重的上腹胀痛伴反酸")
- 不要列举,不要否定词("没有发烧"不进 query)
- 数值不进 query(白细胞具体数字不写,但"白细胞升高"可写)

【Sparse Queries(已由确定性术语扩展生成,直接照搬,**不要修改**)】
{sparse_preview}"""


# ────────────────────────────────────────────────────────────────────────────
# ⑤ select_discriminative_symptom — 维度选择 + 可问性评估
# ────────────────────────────────────────────────────────────────────────────


def build_dimension_selection_prompt(
    chief_complaint: str,
    empty_slots: list[str],
    candidate_diseases_preview: list[str],
    quota: int,
) -> str:
    """⑤ 维度缺口优先 — 从空槽中选 1~2 个最有鉴别价值的维度。"""
    slots_block = ", ".join(empty_slots) or "(无)"
    diseases_block = "、".join(candidate_diseases_preview[:8]) or "(尚未召回)"
    return f"""你是临床问诊助手。患者主诉是"{chief_complaint}",目前候选疾病大致包括:{diseases_block}。
现病史的以下维度槽位仍为空:[{slots_block}]。

请从空槽中选出**最多 {quota} 个**对当前候选疾病鉴别**最有价值**的维度名(填到 selected_slots)。

判断原则:
- 选能把候选疾病一分为二的维度。例如鉴别胆囊炎 vs 胃溃疡,"trigger"(诱因)和
  "aggravating"(加重因素)信息量高
- 不要选那些已在 chief_complaint 里隐含的维度
- 维度名必须是空槽列表中的原文,不要拼写或翻译"""


def build_askability_prompt(symptom: str) -> str:
    """⑤ 贪心循环内 — 单症状可问性评估。"""
    return f"""你是临床问诊助手。请判断症状"{symptom}"是否适合**直接向普通患者**追问。

判断标准:
- 可问(askable=true):患者能感知并回答的主观体验,如"反酸"、"胸闷"、"夜间盗汗"
- 不可问(askable=false):需要医生体格检查或辅助检查才能确认的体征/检验,如
  "Murphy 征阳性"、"肝浊音界缩小"、"白细胞升高"、"心包摩擦音"

reason 字段简短说明判断理由(≤ 30 字)。"""


# ────────────────────────────────────────────────────────────────────────────
# ⑥a generate_followup
# ────────────────────────────────────────────────────────────────────────────


def build_followup_question_prompt(
    chief_complaint: str,
    questions: list[dict],
    confirmed_symptoms: list[str],
    denied_symptoms: list[str],
) -> str:
    """⑥a 生成混合类型(维度级 + 症状级)追问问题,患者口语风格。"""
    items = []
    for q in questions:
        if q.get("type") == "dimension":
            items.append(f"  - 维度问题:补全 {q['slot']} 这个维度")
        elif q.get("type") == "symptom":
            items.append(f"  - 症状问题:确认是否有 {q['term']}")
    items_block = "\n".join(items) if items else "  (无)"

    confirmed_block = "、".join(confirmed_symptoms) or "(无)"
    denied_block = "、".join(denied_symptoms) or "(无)"

    return f"""你是问诊助手。患者主诉:"{chief_complaint}"。
已确认有的症状:{confirmed_block}
已否认的症状:{denied_block}

请把下列待追问项**自然合并成 2-3 句**患者口语化的追问。**不要列举式**,不要"问题1/问题2",
要像聊天一样自然过渡。

【待追问项】
{items_block}

输出要求:
- 直接给问题文本,不要前缀"请问"反复出现
- 维度问题:用问"是什么情况下/怎样的/最近有没有变化"等口语表达,不要直接说"诱因/性质"
  这类术语
- 控制在 2-3 句以内
- 涉及隐私/心理症状要用委婉表达"""


# ────────────────────────────────────────────────────────────────────────────
# ⑦ process_followup_answer
# ────────────────────────────────────────────────────────────────────────────


def build_followup_parse_prompt(
    followup_question: str,
    followup_answer: str,
    questions: list[dict],
) -> str:
    """⑦ 解析患者回答 → 症状状态分流 + 维度槽位回填 + 新症状提取。"""
    items_lines = []
    for q in questions:
        if q.get("type") == "dimension":
            items_lines.append(f"  - 维度槽位 {q['slot']}(回填到 slot_fills)")
        elif q.get("type") == "symptom":
            items_lines.append(f"  - 症状 {q['term']}(回填到 symptom_responses)")
    items_block = "\n".join(items_lines) if items_lines else "  (无)"

    return f"""你是问诊回答解析助手。请按以下规则把患者回答结构化。

【追问问题】
{followup_question}

【本轮追问的待回答项】
{items_block}

【患者回答】
{followup_answer}

【解析规则】
1. 症状级回答(symptom_responses,每项 term=症状标准术语,status 取值):
   - confirmed:患者明确表示有
   - denied:患者明确表示没有
   - uncertain:患者明确表示不知道/不确定
   - unanswered:患者回答里完全没涉及该症状(不要硬猜)
2. 维度级回填(slot_fills,key=槽位名):
   - 单值槽(onset_time/onset_mode/trigger/location/nature/severity/
     duration_pattern/progression/treatment_tried/treatment_response):value=str
   - 多值槽(aggravating/relieving/associated_symptoms):value=list[str]
3. new_symptoms:患者回答里**主动提到的、本轮未问到的新症状**;若无则空列表

槽位名必须与本轮待回答项中的 slot 完全一致,不要新造槽名。"""


# ────────────────────────────────────────────────────────────────────────────
# ⑧a recommend_exam(自由文本输出)
# ────────────────────────────────────────────────────────────────────────────


def build_recommend_exam_prompt(
    diagnosis_results: list[dict],
    unaskable_symptoms: list[dict],
    candidate_chunks_preview: list[str],
    existing_report_findings: list[dict],
) -> str:
    """⑧a recommend_exam(自由文本):基于诊断结果 + 不可问体征推断需要的检查。

    输出文本由调用方解析后填到 `recommended_tests`(list[str])。
    """
    diag_lines = [
        f"  - {r.get('disease')} (p={r.get('probability', 0):.2f}, type={r.get('differentiation_type')})"
        for r in diagnosis_results[:5]
    ]
    diag_block = "\n".join(diag_lines) or "  (无诊断结果)"

    unaskable_lines = [
        f"  - {u.get('preferred_term')} (info_gain={u.get('info_gain', 0):.2f})"
        for u in unaskable_symptoms[:8]
    ]
    unaskable_block = "\n".join(unaskable_lines) or "  (无)"

    chunks_preview = "\n".join(f"  - {c[:80]}" for c in candidate_chunks_preview[:3]) or "  (无)"

    existing_lines = []
    for r in existing_report_findings[:5]:
        existing_lines.append(
            f"  - {r.get('report_type')} ({r.get('report_date')}): "
            f"impressions={r.get('impressions')[:2]}"
        )
    existing_block = "\n".join(existing_lines) or "  (无)"

    return f"""你是医生检查建议助手。请基于诊断候选 + 鉴别要点,推荐 3-5 项检查,按优先级排序,
**不要静默删除已有报告对应的检查**——对已有报告的项,额外加复用评估说明。

【诊断候选】
{diag_block}

【需检查鉴别的体征(unaskable)】
{unaskable_block}

【相关文献片段(供参考)】
{chunks_preview}

【患者已有报告】
{existing_block}

【输出格式】
按优先级编号列出检查,每项 1-2 句说明:
1. 检查名(优先级原因)
2. ...

对与已有报告交集的检查,在该项里追加"已有[日期]报告,可携带评估是否需要复做"
之类的复用说明,不要直接删掉。

口吻面向患者,避免医学术语堆砌,涉及禁食/造影剂等特殊条件要写明。"""


# ────────────────────────────────────────────────────────────────────────────
# ⑩ diagnose 三步 prompt
# ────────────────────────────────────────────────────────────────────────────


def build_evidence_assembly_prompt(
    *,
    parent_texts: list[str],
    figures: list[dict],
    vector_hints: list[str],
    confirmed_symptoms: list[str],
    denied_symptoms: list[str],
    slots: dict[str, Any],
    history_summary: str,
    report_findings: list[dict],
) -> tuple[list[BaseMessage], str]:
    """⑩ Step 1(spec §3.2.3 + §9.3 vision LLM 行):证据归集 EvidenceSheet,**不做概率判断**。

    返回多模态 messages + 纯文本 prompt(后者供 §9.6 final_prompt 审计存档)。
    figure 的 image_data_uri 作为 image_url 消息块附加;medical_statement 已在
    context builder 中排除,**不进 prompt**(spec §3.1.5.1 + §3.2.3 关键认知)。

    Args:
        parent_texts: 规则 1/2 展开后的父块文本列表(与 reranked_chunks 同序)
        figures: 规则 2/3 去重后的图表 chunk 列表,每条含 chunk_raw_text + image_data_uri
        vector_hints: 规则 4 vector_hits matched_text(已去重 + 已与父块原文去重)
        confirmed_symptoms / denied_symptoms / slots / history_summary / report_findings:
            患者多维度证据
    """
    chunks_block = "\n".join(
        f"[chunk {i}] {(c or '')[:300]}" for i, c in enumerate(parent_texts[:8])
    ) or "(无)"

    # 图表块:仅放文本(table=html / figure=caption + footnote);截图走多模态消息单独附加
    if figures:
        figures_block = "\n".join(
            f"[figure {i} | {f['chunk_type']}] {(f.get('chunk_raw_text') or '')[:300]}"
            for i, f in enumerate(figures)
        )
    else:
        figures_block = "(无)"

    hints_block = "\n".join(f"- {h[:200]}" for h in vector_hints[:8]) or "(无)"

    confirmed_block = "、".join(confirmed_symptoms) or "(无)"
    denied_block = "、".join(denied_symptoms) or "(无)"
    slots_block = json.dumps(
        {k: v for k, v in slots.items() if v}, ensure_ascii=False
    )
    reports_block = json.dumps(report_findings[:5], ensure_ascii=False)[:1500]

    prompt_text = f"""你是医学证据归集助手。请从下面的医学文献片段 + 患者证据中,**只做事实级别的证据归集**——
列出每个候选疾病的支持/反对证据,但**不要做概率判断**(那是 Step 2 的事)。

【医学文献片段(精排父块,Top-K)】
{chunks_block}

【同节图表 chunk(table 见 html / figure 见随附截图)】
{figures_block}

【召回线索(matched_text,辅助判断 chunk 被召回的语义焦点;非权威医学事实)】
{hints_block}

【患者已确认症状】{confirmed_block}
【患者已否认症状】{denied_block}
【现病史已填维度】{slots_block}
【病史摘要】{history_summary or "(无)"}
【检查报告发现】{reports_block}

【输出 EvidenceSheet.candidates,每个候选包含】
- disease:候选疾病名
- supporting:支持证据(症状匹配 / 图表数据 / 检查报告 / 病史)
- opposing:反对证据(否认症状/阴性发现)
- history_factors:每项 {{item, direction(increase/decrease/neutral)}}
- slot_relevance:每项 {{slot, value, impact}}(现病史维度对该候选的诊断意义)
- report_evidence:每项 {{finding, role(quantitative_support/qualitative_support/exclusion)}}

至少给出 1 个候选;若文献片段都不相关,也要给一个候选(disease="待进一步评估"),
supporting/opposing 留空,后续 Step 2 会判定为 insufficient。"""

    # 多模态消息组装:base text + 每张可加载的 figure 截图作 image_url 块
    content: list[dict] = [{"type": "text", "text": prompt_text}]
    for f in figures:
        uri = f.get("image_data_uri")
        if uri:
            content.append({"type": "image_url", "image_url": {"url": uri}})

    if len(content) == 1:
        # 没图就直接 str content,避免 provider 把 list 当多模态特殊处理
        messages: list[BaseMessage] = [HumanMessage(content=prompt_text)]
    else:
        messages = [HumanMessage(content=content)]

    return messages, prompt_text


def build_diagnosis_ranking_prompt(
    evidence_sheet_json: str,
    unaskable_symptoms: list[dict],
) -> str:
    """⑩ Step 2:基于 EvidenceSheet 做鉴别诊断排序 + unaskable 条件推理。"""
    unaskable_block = json.dumps(unaskable_symptoms[:8], ensure_ascii=False)
    return f"""你是临床鉴别诊断助手。基于已经归集好的证据表 + 高增益但患者无法自答的体征,
做鉴别诊断排序。

【EvidenceSheet(Step 1 输出)】
{evidence_sheet_json}

【需检查鉴别的体征】
{unaskable_block}

【排序规则】
- 按概率降序输出,所有候选概率之和不必等于 1(每个独立判断)
- 客观检查证据权重 > 主观症状描述
- 病史作为先验概率调节器逐项归因
- 对每个候选,差异 evidence_chain 写 3-5 条关键推理理由

【differentiation_type 选择】
- "confirmed":top1 概率显著领先(≥ 0.6)且证据闭环
- "need_exam":多个候选概率接近,鉴别关键依赖 unaskable 体征/检查 → 在 unaskable_impact
  里说明"做了 X 检查能区分 Y 与 Z"
- "insufficient":候选分散、证据不足以支持任何高概率判断

输出 DiagnosisRanking.ranked,**每项的 failure_reason 字段保持 null**(由节点代码兜底填写,
不在 LLM 职责范围)。"""


def build_diagnosis_calibration_prompt(
    ranking_json: str,
    confirmed_symptoms: list[str],
    denied_symptoms: list[str],
    report_findings: list[dict],
) -> str:
    """⑩ Step 3:置信度校准 + 事实核查 + 标签校准。"""
    confirmed_block = "、".join(confirmed_symptoms) or "(无)"
    denied_block = "、".join(denied_symptoms) or "(无)"
    reports_block = json.dumps(report_findings[:5], ensure_ascii=False)[:1500]

    return f"""你是诊断结果质检助手。请对 Step 2 的排序结果做**事实核查 + 概率校准 + 标签校准**,
直接输出修正后的结果。

【Step 2 输出(待校准)】
{ranking_json}

【原始事实(用于交叉验证)】
- 患者已确认症状:{confirmed_block}
- 患者已否认症状:{denied_block}
- 检查报告:{reports_block}

【三类校准】
1. 事实核查:Step 2 引用的 evidence_chain 是否与原始事实矛盾?有矛盾的删/改
2. 概率校准:top1 与 top2 差距是否合理?如 top1=0.95 但 top2=0.93 应拉大或拉小
3. 标签校准:differentiation_type 与概率是否匹配?(如 top1=0.35 不应标 confirmed,
   应改 need_exam 或 insufficient)

输出 DiagnosisOutput.results,**每项的 failure_reason 字段保持 null**。"""


# ────────────────────────────────────────────────────────────────────────────
# ⑪ safety_gate(LLM 兜底层)
# ────────────────────────────────────────────────────────────────────────────


def build_safety_gate_prompt(
    diagnosis_results: list[dict],
    medical_history: dict,
    rule_layer_constraints: dict,
) -> str:
    """⑪ LLM 兜底层:在规则层基础上识别交叉过敏 / 罕见相互作用 / 肝肾剂量调整。"""
    diag_block = json.dumps(diagnosis_results[:3], ensure_ascii=False)
    history_block = json.dumps(medical_history, ensure_ascii=False)
    rules_block = json.dumps(rule_layer_constraints, ensure_ascii=False)

    return f"""你是临床用药安全助手。下面是规则层已完成的安全约束,请在此基础上做**LLM 兜底**——
识别规则层未覆盖的额外风险。

【诊断结果】{diag_block}
【病史】{history_block}
【规则层约束】{rules_block}

【兜底任务(只输出 additional_risks)】
- cross_allergy:交叉过敏风险,如对头孢过敏 → 警告青霉素类
- interaction:罕见或新近发现的药物相互作用
- dosage_adjustment:基于肝肾功能的剂量调整(从病史推断)

每项含 risk_type / description / severity(high/medium/low) / recommendation。
若无新风险,additional_risks 留空列表。**不要重复规则层已写的禁忌**。"""


# ────────────────────────────────────────────────────────────────────────────
# ⑫ generate_advice
# ────────────────────────────────────────────────────────────────────────────


def build_advice_prompt(
    diagnosis_results: list[dict],
    safety_constraints: dict,
    failure_reason: str | None,
) -> str:
    """⑫ 在 safety_constraints 约束内生成用药/检查/风险建议。"""
    diag_block = json.dumps(diagnosis_results[:3], ensure_ascii=False)
    safety_block = json.dumps(safety_constraints, ensure_ascii=False)
    failure_note = f"\n【系统失败提示】{failure_reason}" if failure_reason else ""

    return f"""你是医生治疗建议助手。基于诊断 + 安全约束,产出结构化建议。

【诊断结果】{diag_block}
【安全约束】{safety_block}{failure_note}

【输出规则】
- medications:用药建议,每项 drug_name / dosage / frequency / duration / notes
  ,所有药物**必须不在 banned_drugs 列表里**,且不触发 interaction_warnings
- exam_suggestions:建议检查项目(基于 differentiation_type=='need_exam' 的候选 +
  插入安全约束相关的功能监测)
- risk_warnings:风险提示与注意事项,**包含**:
  - 高危场景警告(如疑似心梗/脑卒中)
  - safety_constraints.contraindication_flags 的患者侧解释
  - 系统失败提示对应的患者侧告知(如 followup_round_capped → "建议线下就诊获得
    更全面评估";step_N_failed → "系统分析出现技术问题,本次结果不可作为依据")
- urgent_flag:疑似心梗/脑卒中/消化道大出血等高危情况 → True

口吻面向普通患者,不要堆砌术语。"""


# ────────────────────────────────────────────────────────────────────────────
# ⑬ format_response(自由文本)
# ────────────────────────────────────────────────────────────────────────────


def build_format_response_prompt(
    diagnosis_results: list[dict],
    medication_advice: list[dict],
    recommended_tests: list[str],
    risk_warnings: list[str],
    failure_reason: str | None,
) -> str:
    """⑬ 自由文本最终回复:整合诊断 + 建议 + 免责声明。"""
    diag_block = json.dumps(diagnosis_results[:3], ensure_ascii=False)
    med_block = json.dumps(medication_advice, ensure_ascii=False)
    tests_block = json.dumps(recommended_tests, ensure_ascii=False)
    risk_block = json.dumps(risk_warnings, ensure_ascii=False)

    failure_disclaimer = ""
    if failure_reason:
        failure_disclaimer = (
            "\n本次诊断因系统原因未能完整推理,结果仅供参考,请务必线下就诊。"
        )

    return f"""你是医院分诊台问诊助手。请把下列结构化结果整合成一段**患者可读**的自然语言回复。

【诊断】{diag_block}
【用药】{med_block}
【建议检查】{tests_block}
【风险提示】{risk_block}

【回复结构】
1. 一段简短诊断说明(候选疾病 + 大致可能性,口语化,不直接报概率数字)
2. 用药 / 检查 / 注意事项,分点说明
3. 风险提示(若有 urgent_flag → 强烈建议立即就医放到最前)
4. 免责声明:本结果仅作分诊参考,不代替线下医生诊断;具体方案请咨询执业医师{failure_disclaimer}

整段控制在 200-400 字。"""
