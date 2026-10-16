"""docs/10 M1 第一刀验收：R-10 过程所有权、scaffold_type 支架轴、路径推荐、成长证据。"""
from __future__ import annotations

from app.core.actions import ActionEnvelope, ActionType, ScaffoldType
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import MentalStateSnapshot
from app.learning.evidence import evidence_report
from app.learning.path import recommend_path
from app.orchestration.policy import choose_scaffold_type
from app.storage.db import Store

GOV = ActionGovernor()


def _ctx(artifact: bool = False, checkpoint: bool = False) -> GovernorContext:
    return GovernorContext(
        snapshot=MentalStateSnapshot(learner_id="s"),
        ladder_pos=1, hints_used=0, hint_budget=5, frustration_streak=0,
        artifact_present=artifact, checkpoint_mode=checkpoint,
    )


# ---------- R-10 过程所有权 ----------

def _env_full_design(env_type: ActionType = ActionType.ENV_OP) -> ActionEnvelope:
    env = ActionEnvelope(
        session_id="s1", type=env_type,
        params={"full_design": True, "design_kind": "experiment",
                "text": "实验方案：变量、材料、程序……"},
        policy_provenance={"generated_by": "policy_v1", "grounded_to_kg": ["KG.PHY.BUOY"]},
    )
    return env


def test_r10_denies_full_design_without_student_artifact():
    outcome = GOV.decide(_env_full_design(), _ctx(artifact=False))
    assert outcome.rule_id == "R-10" and outcome.decision == "deny"


def test_r10_allows_with_partial_artifact():
    outcome = GOV.decide(_env_full_design(), _ctx(artifact=True))
    assert outcome.rule_id is None and outcome.decision == "allow"


def test_r10_denies_in_exam_mode_even_with_artifact():
    outcome = GOV.decide(_env_full_design(), _ctx(artifact=True, checkpoint=True))
    assert outcome.rule_id == "R-10" and outcome.decision == "deny"


def test_r10_ignores_non_design_actions():
    env = ActionEnvelope.hint(session_id="s1", kc_id="MATH.G7.EQ.SOLVE", level=1,
                              form="directive", text="先看看变量。")
    outcome = GOV.decide(env, _ctx(artifact=False))
    assert outcome.rule_id is None


# ---------- scaffold_type 支架轴 ----------

def test_hint_carries_scaffold_type():
    env = ActionEnvelope.hint(session_id="s1", kc_id="K", level=1, form="directive",
                              text="先看看变量。", scaffold_type="conceptual")
    assert env.params["scaffold_type"] == "conceptual"
    assert ScaffoldType(env.params["scaffold_type"]) == ScaffoldType.conceptual


def test_scaffold_matrix_trigger_state_mapping():
    assert choose_scaffold_type("这个概念到底是什么意思？") == "conceptual"
    assert choose_scaffold_type("下一步的步骤是什么？") == "procedural"
    assert choose_scaffold_type("我忘了验算") == "metacognitive"
    assert choose_scaffold_type("你的依据是什么？") == "epistemic"
    assert choose_scaffold_type("他们俩谁的观点对？") == "collaborative"
    assert choose_scaffold_type("不想做了，没意思") == "participation"


def test_scaffold_matrix_default_is_strategic():
    assert choose_scaffold_type("") == "strategic"


# ---------- 学习路径推荐（docs/11 §6.5） ----------

def _snap_with_mastery(pairs: list[tuple[str, float]]):
    from app.core.schema import KCMastery
    snap = MentalStateSnapshot(learner_id="s")
    snap.knowledge_state.kc_masteries = [
        KCMastery(kc_id=k, p_mastery=p, ci95=(max(0, p - 0.05), min(1, p + 0.05)))
        for k, p in pairs
    ]
    return snap


def test_path_recommend_weak_first_and_status():
    snap = _snap_with_mastery([("K.A", 0.82), ("K.B", 0.41), ("K.C", 0.95), ("K.D", 0.55)])
    path = recommend_path(snap)
    assert path["status"] == "proposed"
    assert path["nodes"][0]["kc_id"] == "K.B"          # 薄弱优先
    assert path["nodes"][0]["status"] == "current"
    dones = [n for n in path["nodes"] if n["status"] == "done"]
    assert all(n["p_mastery"] >= 0.9 for n in dones)   # 0.9 门槛
    assert path["nodes"] == sorted(path["nodes"], key=lambda n: n["seq"])


def test_path_recommend_empty_knowledge():
    path = recommend_path(_snap_with_mastery([]))
    assert path["nodes"] == [] and path["status"] == "proposed"


# ---------- 成长证据（docs/11 §6） ----------

def test_evidence_report_shape_and_honesty():
    store = Store(":memory:")
    store.ensure_learner("s")
    report = evidence_report(store, "s")
    assert report["contract_version"] == "EvidenceReport@1"
    assert report["mastery"]["current"] is None        # 无快照 → 诚实返回 None
    assert any("快照" in n for n in report["notes"])
    assert "autonomy_index" not in report              # 防古德哈特：不输出原始分


def test_store_snapshots_for_learner_ordered():
    store = Store(":memory:")
    store.ensure_learner("s")
    s1 = MentalStateSnapshot(learner_id="s")
    s2 = MentalStateSnapshot(learner_id="s")
    store.append_snapshot(s1)
    store.append_snapshot(s2)
    got = store.snapshots_for_learner("s")
    assert [x.snapshot_id for x in got] == [s1.snapshot_id, s2.snapshot_id]
