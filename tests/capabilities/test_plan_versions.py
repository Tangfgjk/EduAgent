import pytest

from deeptutor.learning.models import KnowledgePoint, LearningModule, LearningProgress
from deeptutor.learning.plan_versions import history, rollback
from deeptutor.learning.storage import LearningStore


def test_versions_and_rollback_preserve_evidence(tmp_path):
    store = LearningStore(tmp_path)
    p = LearningProgress(
        book_id="p",
        modules=[
            LearningModule(
                id="m",
                name="Original",
                order=0,
                knowledge_points=[
                    KnowledgePoint(id="k", name="Skill", type="memory", module_id="m")
                ],
            )
        ],
    )
    store.save(p)

    def edit(tx):
        tx.progress.modules[0].name = "Adjusted"
        tx.emit("agent.plan_adjusted", {"reason": "Address learner difficulty"})

    updated, _ = store.mutate("p", edit)
    versions = history(store, "p")
    assert versions[0]["before"][0]["name"] == "Original"
    assert versions[0]["reason"] == "Address learner difficulty"
    with pytest.raises(RuntimeError):
        rollback(store, "p", versions[0]["id"], updated.version - 1)
    before = store.load("p").learning_evidence
    rollback(store, "p", versions[0]["id"], updated.version)
    assert store.load("p").modules[0].name == "Original"
    assert store.load("p").learning_evidence == before
