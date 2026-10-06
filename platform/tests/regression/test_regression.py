"""Versioned, data-driven behavioral regression. No live clock or model judge."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.agents.derived import DerivedRunner
from app.core.actions import ActionEnvelope, ActionType
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import MentalStateSnapshot
from app.learning.decisions import recommend
from app.learning.gates import evaluate_gate
from app.learning.mastery_port import update_mastery
from app.learning.replay import replay
from app.learning.retrieval import LocalRetrieval, RetrievalContext
from app.learning.review_port import retrievability, update_retention
from app.learning.schema import AssessmentProfile, LearningEvidence, MasteryState, RetentionState
from app.learning.service import ConsentDenied, EvidenceConflict, LearningService
from app.llm.client import FakeLLM
from app.orchestration.policy import choose_scaffold_type
from app.storage.db import Store


SUITE_PATH = Path(__file__).with_name("冻结场景-20261006.json")
SUITE = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
SCENARIOS = SUITE["scenarios"]


def make_context(initial):
    snapshot = MentalStateSnapshot(learner_id="regression")
    snapshot.affect_motivation.frustration = initial.get("frustration", 0)
    return GovernorContext(snapshot=snapshot, ladder_pos=initial.get("ladder_pos", 0),
          hints_used=initial.get("hints_used", 0), hint_budget=initial.get("hint_budget", 3),
          frustration_streak=initial.get("frustration_streak", 0),
          artifact_present=initial.get("artifact_present", False),
          checkpoint_mode=initial.get("checkpoint_mode", False), current_difficulty=.5)


def make_evidence(scenario, number=1, **updates):
    fields = dict(evidence_id=f"e{number}", learner_id="regression", session_id="session",
                  kc_refs=["MATH.G7.EQ.SOLVE"], attempt_id=f"a{number}", artifact_ref=f"artifact:{number}",
                  verdict_ref=f"verdict:{number}", verdict_status="passed", verifier_version="symbolic-v1",
                  confidence=1, occurred_at=datetime.fromisoformat(scenario["as_of"]), event_seq=number,
                  consent_scope=["teaching"], consent_version="c1", authorization_source="student:local",
                  assessment_id=f"task{number}",assessment_kind="review", score=1)
    fields.update(updates)
    return LearningEvidence(**fields)


def setup_service(scenario):
    service = LearningService(Store())
    service.set_consent("regression", ["teaching"], "c1", "student:local",
                        datetime.fromisoformat(scenario["as_of"]))
    return service


def test_frozen_scenario_schema_and_coverage():
    required = {"scenario_id", "scenario_version", "initial_state", "input", "expected_decision",
                "expected_rule", "expected_evidence", "expected_state_transition", "rubric",
                "parameter_versions", "as_of", "source"}
    assert len(SCENARIOS) >= 30
    assert len({s["scenario_id"] for s in SCENARIOS}) == len(SCENARIOS)
    assert all(required <= s.keys() for s in SCENARIOS)
    assert all(datetime.fromisoformat(s["as_of"]).tzinfo for s in SCENARIOS)
    tags = {tag for s in SCENARIOS for tag in s["coverage"]}
    assert {f"R-{i:02d}" for i in range(1, 11)} <= tags
    assert {"conceptual", "procedural", "strategic", "metacognitive", "epistemic", "collaborative", "participation"} <= tags
    assert {"primary", "secondary", "tertiary", "passed", "failed", "partial", "unverifiable",
            "idempotence", "withdrawal", "prerequisite", "ungrounded", "derived_permission"} <= tags


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["scenario_id"])
def test_frozen_scenario(scenario, tmp_path):
    kind = scenario["kind"]
    data = scenario["input"]
    expected = scenario["expected_state_transition"]
    if kind in {"rule", "stage_safety"}:
        envelope = ActionEnvelope(type=ActionType(data["action_type"]), params=data["params"],
                 policy_provenance={"generated_by": "frozen-regression-v1", "grounded_to_kg": data.get("kc_refs", ["math"])})
        result = ActionGovernor().decide(envelope, make_context(scenario["initial_state"]))
        assert result.decision == scenario["expected_decision"]
        assert result.rule_id == scenario["expected_rule"]
        assert expected == "none"
        if result.decision == "rewrite":
            assert "聪明" not in result.envelope.params["text"]
            assert "策略" in result.envelope.params["text"]
    elif kind == "scaffold":
        selected = choose_scaffold_type(data["student_text"])
        assert selected == scenario["expected_decision"]
        hint = ActionEnvelope.hint("session", "math", 0, "nudge", "说说下一步", scaffold_type=selected)
        assert ActionGovernor().decide(hint, make_context(scenario["initial_state"])).decision == "allow"
    elif kind == "verdict":
        initial = MasteryState(learner_id="regression", kc_id="MATH.G7.EQ.SOLVE", p_mastery=.8)
        new, reason = update_mastery(initial, make_evidence(scenario, verdict_status=data["verdict_status"]))
        assert reason == scenario["expected_decision"]
        assert new.state_version - initial.state_version == expected["version_delta"]
        assert new.effective_evidence_count == expected["effective_count"]
        assert (new.p_mastery > initial.p_mastery) == expected.get("probability_increases", False)
        if expected["version_delta"] == 0:
            assert new == initial
        assert new.evidence_refs == scenario["expected_evidence"]
    elif kind == "gate":
        history = [make_evidence(scenario, i, confidence=data.get("confidence", 1)) for i in range(1, data["count"] + 1)]
        state = MasteryState(learner_id="regression", kc_id="MATH.G7.EQ.SOLVE")
        for evidence in history:
            state, _ = update_mastery(state, evidence)
        if data.get("force_probability") is not None:
            state = state.model_copy(update={"p_mastery": data["force_probability"]})
        result = evaluate_gate(state, history, AssessmentProfile())
        assert result.status == scenario["expected_decision"]
        assert result.effective_evidence_count == expected["effective_count"]
        assert result.evidence_refs == scenario["expected_evidence"]
    elif kind == "service":
        service = setup_service(scenario)
        try:
            evidence = make_evidence(scenario)
            operation = data["operation"]
            if operation == "duplicate":
                first = service.consume(evidence)
                assert service.consume(evidence) == first
                assert service.masteries("regression")[0].state_version == expected["version"]
                assert len(service.transitions("regression")) == expected["transitions"]
            elif operation == "conflict":
                service.consume(evidence)
                with pytest.raises(EvidenceConflict):
                    service.consume(evidence.model_copy(update={"score": .5}))
                assert service.masteries("regression")[0].state_version == expected["version"]
            elif operation in {"withdraw", "research_read"}:
                service.consume(evidence)
                if operation == "withdraw":
                    service.set_consent("regression", [], "c2", "student:local", evidence.occurred_at)
                with pytest.raises(ConsentDenied):
                    service.evidences("regression", "teaching" if operation == "withdraw" else "research")
                if operation == "withdraw":
                    with pytest.raises(ConsentDenied):
                        service.consume(make_evidence(scenario, 2))
            elif operation == "rollback":
                def fault(stage):
                    if stage == "transition":
                        raise RuntimeError("injected")
                with pytest.raises(RuntimeError):
                    service.consume(evidence, fault=fault)
                assert service.masteries("regression") == []
                assert service.transitions("regression") == []
                assert service.evidences("regression") == []
            elif operation == "cas":
                service.consume(evidence)
                with pytest.raises(EvidenceConflict):
                    service.consume(make_evidence(scenario, 2), expected_versions={"MATH.G7.EQ.SOLVE:mastery": 0})
                assert service.masteries("regression")[0].state_version == expected["version"]
            else:
                raise AssertionError(f"Unhandled service scenario {operation}")
            persisted_ids = [row[0] for row in service.store.conn.execute(
                "SELECT evidence_id FROM learning_evidence ORDER BY event_seq").fetchall()]
            assert persisted_ids == scenario["expected_evidence"]
        finally:
            service.store.close()
    elif kind == "prerequisite":
        service = setup_service(scenario)
        try:
            for i in range(1, 7):
                service.consume(make_evidence(scenario, i, kc_refs=["MATH.ADVANCED"]))
            result = recommend(service, "regression", datetime.fromisoformat(scenario["as_of"]),
                               prerequisites={"MATH.BASE": [], "MATH.ADVANCED": ["MATH.BASE"]})
            node = next(node for node in result["nodes"] if node["kc_id"] == "MATH.ADVANCED")
            assert node["decision"] == scenario["expected_decision"]
            assert node["missing_prerequisites"] == ["MATH.BASE"]
            assert result["status"] == "proposed"
            assert node["gate"]["status"] == "mastered"
            assert node["evidence_refs"] == scenario["expected_evidence"]
        finally:
            service.store.close()
    elif kind == "replay":
        evidence = [make_evidence(scenario, i) for i in range(1, 7)]
        first = replay(evidence, "regression", datetime.fromisoformat(scenario["as_of"]))
        second = replay(list(reversed(evidence)), "regression", datetime.fromisoformat(scenario["as_of"]))
        assert first == second
        assert first.mastery["MATH.G7.EQ.SOLVE"].effective_evidence_count == expected["effective_count"]
        assert first.evidence_refs == scenario["expected_evidence"]
    elif kind == "retrieval":
        source = tmp_path / "source.md"
        source.write_text("equation balance", encoding="utf-8")
        port = LocalRetrieval(tmp_path)
        port.import_document(source, source_id="source")
        results = port.retrieve("equation", RetrievalContext(kc_refs=["math"]))
        assert results[0].grounding_status == scenario["expected_decision"]
        assert not results[0].usable_for_teaching
    elif kind == "derived":
        llm = FakeLLM([json.dumps(data["payload"])])
        report = DerivedRunner(llm).run(data["role"], session_id="s", kc_refs=["math"],
                  evidence_refs=["e1"], student_work="x", governor=ActionGovernor(), context=make_context(scenario["initial_state"]))
        assert report.exit_status == scenario["expected_decision"]
        assert report.actions == []
        assert report.evidence_refs == scenario["expected_evidence"]
        assert len(llm.calls) == 1
    elif kind == "retention":
        initial = RetentionState(learner_id="regression", kc_id="MATH.G7.EQ.SOLVE")
        new, reason = update_retention(initial, make_evidence(scenario, assessment_kind=data["assessment_kind"],
                                        verdict_status=data.get("verdict_status", "passed")))
        assert reason == scenario["expected_decision"]
        assert new.state_version == expected["version"]
        assert new.lapse_count == expected["lapses"]
        assert new.evidence_refs == scenario["expected_evidence"]
        if reason is None:
            assert retrievability(new, datetime.fromisoformat(scenario["as_of"])) == 1
    else:
        raise AssertionError(f"Unhandled frozen scenario kind: {kind}")
