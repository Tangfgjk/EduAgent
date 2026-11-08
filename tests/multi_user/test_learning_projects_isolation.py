"""Uploaded documents and chapter state never cross account boundaries."""

from fastapi import HTTPException
import pytest

from deeptutor.learning.models import (
    KnowledgePoint,
    LearningModule,
    LearningProgress,
    TopicMetadata,
)
from deeptutor.learning.projects import import_file, read_upload
from deeptutor.learning.storage import LearningStore


@pytest.mark.asyncio
async def test_uploaded_project_and_chapter_endpoints_owner_scoped(as_user):
    from deeptutor.api.routers.mastery_path import get_learning_project, prepare_project_chapter

    with as_user("owner"):
        source = await import_file("private.txt", b"Private student material.")
        store = LearningStore()
        store.save(
            LearningProgress(
                book_id="secret",
                modules=[
                    LearningModule(
                        id="m",
                        name="Private chapter",
                        order=0,
                        knowledge_points=[
                            KnowledgePoint(
                                id="k", name="Private objective", type="concept", module_id="m"
                            )
                        ],
                    )
                ],
            )
        )
        store.put_topic(TopicMetadata(path_id="secret", goal="Private goal"), [source])
        assert (await get_learning_project("secret"))["topic"]["metadata"]["goal"] == "Private goal"
    with as_user("other"):
        with pytest.raises(ValueError):
            read_upload(source.source_id)
        for operation in (get_learning_project("secret"), prepare_project_chapter("secret", "m")):
            with pytest.raises(HTTPException) as denied:
                await operation
            assert denied.value.status_code == 404
