from deeptutor.learning.models import KnowledgePoint, LearningModule, LearningProgress
from deeptutor.learning.project_continuity import project_handoff


def test_handoff_tracks_live_revision_without_granting_credit():
    p = LearningProgress(
        book_id="project-a",
        wenjin_enabled=True,
        modules=[
            LearningModule(
                id="m",
                name="Module",
                order=0,
                knowledge_points=[
                    KnowledgePoint(id="k", name="Skill", type="memory", module_id="m")
                ],
            )
        ],
    )
    before = p.model_dump(mode="json")
    packet = project_handoff(p)
    assert packet["project_id"] == "project-a"
    assert packet["pending_task"] is None
    assert packet["objectives"][0]["achieved"] == []
    assert p.model_dump(mode="json") == before
    p.version += 1
    assert project_handoff(p)["source_revision"] == packet["source_revision"] + 1
