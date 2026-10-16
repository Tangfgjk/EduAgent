"""docs/02 §4 校验清单的 CI 落地：禁止特质字段、不确定性必填、extra=forbid。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.schema import (
    AutonomyIndex, GoalContract, GoalStatement, KCMastery, MentalStateSnapshot,
)


def test_snapshot_roundtrip():
    snap = MentalStateSnapshot(
        learner_id="s1",
        knowledge_state={"kc_masteries": [{
            "kc_id": "MATH.G7.EQ.SOLVE", "p_mastery": 0.62,
            "ci95": [0.5, 0.74], "model": "BKT",
        }]},
        autonomy_index={"composite": 0.5},
    )
    data = snap.model_dump_json()
    snap2 = MentalStateSnapshot.model_validate_json(data)
    assert snap2.knowledge_state.kc_masteries[0].p_mastery == 0.62
    assert snap2.schema_version == "1.0"


def test_no_trait_fields_allowed():
    """特质字段（aptitude/iq/ability_level）无法写入——extra=forbid。"""
    with pytest.raises(ValidationError):
        MentalStateSnapshot(learner_id="s1", aptitude=0.9)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AutonomyIndex(composite=0.5, iq=120)  # type: ignore[call-arg]


def test_source_contains_no_trait_field_names():
    """docs/02 规则②：CI 正则扫描源码，特质字段名出现即构建失败。"""
    src = Path(__file__).resolve().parent.parent / "app" / "core" / "schema.py"
    text = src.read_text(encoding="utf-8")
    assert not re.search(r"\b(aptitude|iq_score|ability_level)\b", text)


def test_mastery_requires_uncertainty():
    """规则①：估计字段必须带 ci95。"""
    with pytest.raises(ValidationError):
        KCMastery(kc_id="MATH.G7.EQ.SOLVE", p_mastery=0.5)  # 缺 ci95


def test_contract_authorship_locked_to_student():
    """R-02 的 Schema 层兜底：GoalStatement.authored_by 只能为 student。"""
    contract = GoalContract(
        learner_id="s1",
        goal_statement=GoalStatement(text="学会一元一次方程", authored_by="student"),
    )
    assert contract.goal_statement.authored_by == "student"
    with pytest.raises(ValidationError):
        GoalStatement(text="x", authored_by="agent")  # type: ignore[arg-type]
