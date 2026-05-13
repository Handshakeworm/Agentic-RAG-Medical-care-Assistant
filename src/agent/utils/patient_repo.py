"""src/agent/utils/patient_repo.py — 患者档案 / 报告引用 PG 查询(DEV_SPEC §2.4.5 / §4.1.2 ①)。

B1/B2 完工后(2026-05-12),§2.4.5 八张患者历史表 ORM 已落地(`models_patient.py`),
本模块从占位 stub 升级为真查询。

返回结构对齐 spec §4.1.1 `medical_history` 字段 + §4.1.2 ① Step 2 加载映射:

  load_medical_history(user_id) → {
      "past_history":        {medical_history, surgical_trauma, transfusion},  # 既往史
      "allergy_history":     [...]                                              # 过敏史 ⚠️ safety_gate
      "medication_history":  [...]                                              # 用药史 ⚠️ safety_gate
      "personal_history":    {smoking, alcohol, occupation, ...},               # 个人史
      "obstetric_history":   {...} | None,                                      # 婚育史(女性)
      "family_history":      [...]                                              # 家族史
  }

设计原则(沿用占位时期的契约):
- 缺患者 / 缺记录 → 返回安全空值,不抛异常 — Agentic 流程对空档案 robust
- ORM 字段 → dict 转换显式 _row_to_dict,不用 SQLAlchemy automap(降低魔法)
- 一次会话调一次,所以 8 张表 8 个 query 可接受 — 不优化成 join
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select

from src.db.postgres.connection import session_scope
from src.db.postgres.models_patient import (
    Allergy,
    ExamReport,
    FamilyHistory,
    MedicalHistory,
    Medication,
    MenstrualReproductive,
    Patient,
    SurgicalTraumaHistory,
    TransfusionHistory,
)


_logger = logging.getLogger(__name__)


def _date_to_iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def load_medical_history(user_id: str) -> dict:
    """从 PG 加载结构化病史档案(spec §4.1.1 medical_history 字段)。

    `user_id` 即 `patients.id`(spec §2.4.5:patients 1:1 users)。
    """
    with session_scope() as s:
        patient: Patient | None = s.get(Patient, user_id)

        # 患者基本档案不存在 → 返空。Agent 节点对空档案 robust
        if patient is None:
            _logger.debug("load_medical_history(%s) — patient 行不存在,返空档案", user_id)
            return _empty_history()

        personal_history = {
            "smoking_status": patient.smoking_status,
            "smoking_pack_years": (
                float(patient.smoking_pack_years) if patient.smoking_pack_years else None
            ),
            "alcohol_status": patient.alcohol_status,
            "alcohol_detail": patient.alcohol_detail,
            "occupation": patient.occupation,
            "occupational_exposure": patient.occupational_exposure,
            "travel_history": patient.travel_history,
            "infectious_contact": patient.infectious_contact,
        }

        # past_history:既往疾病 + 手术/外伤 + 输血 三块合一(spec §4.1.2 ① Step 2)
        med_hist = s.execute(
            select(MedicalHistory).where(MedicalHistory.patient_id == user_id)
        ).scalars().all()
        surg_trauma = s.execute(
            select(SurgicalTraumaHistory).where(SurgicalTraumaHistory.patient_id == user_id)
        ).scalars().all()
        transfusions = s.execute(
            select(TransfusionHistory).where(TransfusionHistory.patient_id == user_id)
        ).scalars().all()
        past_history = {
            "medical_history": [
                {
                    "category": r.category,
                    "condition": r.condition,
                    "icd10_code": r.icd10_code,
                    "diagnosed_at": _date_to_iso(r.diagnosed_at),
                    "resolved_at": _date_to_iso(r.resolved_at),
                    "control_status": r.control_status,
                    "notes": r.notes,
                }
                for r in med_hist
            ],
            "surgical_trauma": [
                {
                    "type": r.type,
                    "name": r.name,
                    "occurred_at": _date_to_iso(r.occurred_at),
                    "hospital": r.hospital,
                    "has_complications": r.has_complications,
                    "complications": r.complications,
                    "sequelae": r.sequelae,
                }
                for r in surg_trauma
            ],
            "transfusion": [
                {
                    "transfusion_date": _date_to_iso(r.transfusion_date),
                    "blood_product": r.blood_product,
                    "reason": r.reason,
                    "adverse_reaction": r.adverse_reaction,
                    "reaction_detail": r.reaction_detail,
                }
                for r in transfusions
            ],
        }

        # allergy_history:safety_gate ⑪ 抽 banned_drugs 用 — substance/drug 字段命名
        # 与 §4.1.2 ⑪ rule 层一致(spec _rule_layer_constraints 取 substance | drug | name)
        allergies = s.execute(
            select(Allergy).where(Allergy.patient_id == user_id)
        ).scalars().all()
        allergy_history = [
            {
                "substance": r.allergen,
                "allergen_type": r.allergen_type,
                "reaction": r.reaction,
                "reaction_type": r.reaction_type,
                "severity": r.severity,
                "status": r.status,
            }
            for r in allergies
        ]

        # medication_history:当前 + 历史用药全列(safety_gate 自己按 ended_at 区分)
        meds = s.execute(
            select(Medication).where(Medication.patient_id == user_id)
        ).scalars().all()
        medication_history = [
            {
                "drug_name": r.drug_name,
                "drug_category": r.drug_category,
                "dosage": r.dosage,
                "frequency": r.frequency,
                "route": r.route,
                "started_at": _date_to_iso(r.started_at),
                "ended_at": _date_to_iso(r.ended_at),
                "is_current": r.ended_at is None,
                "prescribed_by": r.prescribed_by,
                "is_self_medication": r.is_self_medication,
            }
            for r in meds
        ]

        family = s.execute(
            select(FamilyHistory).where(FamilyHistory.patient_id == user_id)
        ).scalars().all()
        family_history = [
            {
                "relation": r.relation,
                "condition": r.condition,
                "condition_category": r.condition_category,
                "onset_age": r.onset_age,
                "notes": r.notes,
            }
            for r in family
        ]

        # obstetric_history(女性 1:1)— spec §4.1.2 ⑪ rule 层取 pregnancy_status / lactation_status
        # 这里把 ORM 的 is_pregnant / is_lactating bool 翻译成 spec 约定的字符串值
        ob_row: MenstrualReproductive | None = s.execute(
            select(MenstrualReproductive).where(
                MenstrualReproductive.patient_id == user_id
            )
        ).scalar_one_or_none()
        obstetric_history: dict | None
        if ob_row is None:
            obstetric_history = None
        else:
            obstetric_history = {
                "menarche_age": ob_row.menarche_age,
                "cycle_days": ob_row.cycle_days,
                "period_days": ob_row.period_days,
                "last_menstrual_period": _date_to_iso(ob_row.last_menstrual_period),
                "pregnancy_status": "pregnant" if ob_row.is_pregnant else "not_pregnant",
                "lactation_status": "lactating" if ob_row.is_lactating else "not_lactating",
                "gravidity": ob_row.gravidity,
                "parity": ob_row.parity,
                "menopause_age": ob_row.menopause_age,
                "notes": ob_row.notes,
            }

        return {
            "past_history": past_history,
            "allergy_history": allergy_history,
            "medication_history": medication_history,
            "personal_history": personal_history,
            "obstetric_history": obstetric_history,
            "family_history": family_history,
        }


def load_initial_exam_reports(user_id: str) -> list[dict]:
    """加载患者已上传的检查报告文件引用(spec §4.1.1 exam_reports 字段)。

    Returns: `list[{"file_ref": str, "report_type": str, "report_date": str | None}]`
    """
    with session_scope() as s:
        rows = s.execute(
            select(ExamReport).where(ExamReport.patient_id == user_id)
        ).scalars().all()
        return [
            {
                "file_ref": r.file_path,
                "report_type": r.report_type,
                "report_date": _date_to_iso(r.report_date),
                "report_name": r.report_name,
            }
            for r in rows
            if r.file_path  # 文件路径为空的行无意义,跳过
        ]


def _empty_history() -> dict:
    return {
        "past_history": {"medical_history": [], "surgical_trauma": [], "transfusion": []},
        "allergy_history": [],
        "medication_history": [],
        "personal_history": {},
        "obstetric_history": None,
        "family_history": [],
    }
