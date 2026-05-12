"""src/agent/utils/patient_repo.py — 患者档案 / 报告引用 PG 查询占位(DEV_SPEC §2.4.5 / §4.1.2 ①)。

**当前状态(2026-05-12)**:§2.4.5 患者档案 8 张表(`patients` / `medical_history` /
`allergies` / `medications` / 等)的 ORM 在 `src/db/postgres/models.py` 中**尚未实现**
(B 阶段任务 B3 只完成了 sources / raw_documents / chunks)。F2 info_collect 节点
依赖这些表来加载病史/报告,这里先提供**安全占位实现**——返回空 dict / 空 list,
让 Agent 流水线在缺表场景下仍能走通(MVP / 测试场景下患者档案为空是正常的)。

**TODO**(B 阶段补齐时):
- 实现 §2.4.5 八张表的 ORM
- 把下面三个函数的 `# TODO` 标记替换成真实查询
- 不要改函数签名,Node ① 已按当前签名调用

设计原则:
- 缺表 / 缺患者 / 缺记录 → 返回安全空值,不抛异常
- 节点代码不应感知"是真的没有,还是 ORM 没建好"——Agentic 流程对空档案是 robust 的
- 添加新表后这里改实现,节点测试无需更新
"""
from __future__ import annotations

import logging


_logger = logging.getLogger(__name__)


def load_medical_history(patient_id: str) -> dict:
    """加载结构化病史档案(spec §4.1.1 medical_history 字段)。

    Returns:
        {
          "past_history":        dict,        # 既往史(基础病/手术/外伤/输血/传染病)
          "allergy_history":     list[dict],  # 过敏史 ⚠️ safety_gate 输入
          "medication_history":  list[dict],  # 用药史 ⚠️ safety_gate 输入
          "personal_history":    dict,        # 个人史(烟酒/职业/旅居)
          "obstetric_history":   dict | None, # 婚育史(女性)
          "family_history":      list[dict],  # 家族史
        }

    缺数据时所有字段返回安全空值。
    """
    # TODO(B 阶段):接 §2.4.5 medical_history / allergies / medications / patients /
    #                menstrual_reproductive / family_history / surgical_trauma_history /
    #                transfusion_history 各表,按 spec §4.1.2 ① Step 2 表格映射拼装
    _logger.debug(
        "load_medical_history(%s) — patient ORM 未建,返回空档案占位", patient_id
    )
    return {
        "past_history": {},
        "allergy_history": [],
        "medication_history": [],
        "personal_history": {},
        "obstetric_history": None,
        "family_history": [],
    }


def load_initial_exam_reports(patient_id: str) -> list[dict]:
    """加载患者已上传的检查报告文件引用(spec §4.1.1 exam_reports 字段)。

    Returns:
        list[dict],每项 `{"file_ref": str}`(file_ref 是文件路径或对象存储 URL),
        缺数据时返回空 list。
    """
    # TODO(B 阶段):接 §2.4.5 exam_reports 表,SELECT file_path WHERE patient_id=...
    _logger.debug(
        "load_initial_exam_reports(%s) — exam_reports ORM 未建,返回空列表占位",
        patient_id,
    )
    return []
