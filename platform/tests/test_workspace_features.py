from app.learning.workspace_features import (
    competency_radar, create_context, create_harness, harnesses,
    install_feature_tables, review_harness,
)
from app.storage.db import Store


def test_temporary_context_and_candidate_harness_are_isolated_and_reviewable():
    store = Store()
    try:
        install_feature_tables(store)
        context = create_context(store, "s1", "temporary", "快速讨论")
        assert context["formal"] == 0
        assert competency_radar(store, "s1")["dimensions"][0]["value"] is None
        candidate = create_harness(store, "s1", None, {
            "subjective_feedback": "我需要先写步骤再检查",
            "objective_evidence_refs": [], "materials": ["教材第1章"], "standards": ["考纲：方程"],
        })
        assert candidate["status"] == "candidate"
        assert "requires learner/teacher review" in candidate["skill_md"]
        approved = review_harness(store, "s1", candidate["version_id"], "approved")
        assert approved["status"] == "approved"
        assert harnesses(store, "s1")[0]["status"] == "approved"
        assert review_harness(store, "s1", candidate["version_id"], "rolled_back")["status"] == "rolled_back"
    finally:
        store.close()
