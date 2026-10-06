from datetime import timedelta
import pytest
from app.learning.memory import MemoryService
from app.learning.development_metrics import cognitive_change, autonomy_behaviors
from tests.test_learning_storage import service, evidence, NOW


def test_memory_rebuild_deterministic_source_linked_and_no_second_mastery():
    s=service(); s.consume(evidence("a"))
    memory=MemoryService(s.store)
    first=memory.rebuild("s1",NOW)
    assert first==memory.rebuild("s1",NOW)
    assert first["l1"]["evidence_refs"]==["a"]
    assert "p_mastery" not in first["l3"]
    assert first["l3"]["state_refs"]
    assert memory.get("s1",first["view_id"])==first
    s.set_consent("s1",[],"withdraw","learner",NOW)
    with pytest.raises(PermissionError): memory.get("s1",first["view_id"])


def test_memory_revisions_require_rebuild_and_history_excludes_future():
    s=service(); s.consume(evidence("a")); memory=MemoryService(s.store)
    view=memory.rebuild("s1",NOW)
    s.consume(evidence("revision",supersedes="a",attempt_id="a",verdict_status="failed"))
    with pytest.raises(ValueError): memory.get("s1",view["view_id"])
    assert memory.rebuild("s1",NOW)["l1"]["evidence_refs"]==["revision"]
    assert memory.rebuild("s1",NOW-timedelta(seconds=1))["l1"]["evidence_refs"]==[]


def test_bloom_task_label_is_not_student_ability_and_rubric_windows_required():
    values=[]
    for i in range(4):
        values.append(evidence(str(i),assessment_id=str(i),occurred_at=NOW+timedelta(days=i),
            difficulty_band="easy",verifier_details={"taxonomy_level":"apply","bloom_rubric_version":"v1","comparison_group":"g","assessment_provenance":"synthetic-test"}))
    result=cognitive_change(values,learner_id="s1",as_of=NOW+timedelta(days=5),split_at=NOW+timedelta(days=2))
    assert result["status"]=="comparable" and result["after_distribution"]=={"apply":2}
    unlabeled=[e.model_copy(update={"verifier_details":{"taxonomy_level":"create"}}) for e in values]
    assert cognitive_change(unlabeled,learner_id="s1",as_of=NOW+timedelta(days=5),split_at=NOW+timedelta(days=2))["status"]=="insufficient_data"
    mixed=[e.model_copy(update={"difficulty_band":"hard"}) if i==3 else e for i,e in enumerate(values)]
    assert cognitive_change(mixed,learner_id="s1",as_of=NOW+timedelta(days=5),split_at=NOW+timedelta(days=2))["status"]=="incomparable"


def test_autonomy_decomposition_does_not_rank_or_reward_no_help():
    result=autonomy_behaviors([evidence("a"),evidence("b",assistance_mode="hint",hint_level=1)],[],learner_id="s1",as_of=NOW)
    assert result["independent_attempt_count"]==1 and result["assisted_attempt_count"]==1
    assert result["composite"] is None
