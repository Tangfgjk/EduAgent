from fastapi import HTTPException
import pytest

from deeptutor.learning.external_diagnosis_models import ExternalCommitRequest
from deeptutor.learning.models import KnowledgePoint, LearningModule, LearningProgress
from deeptutor.learning.storage import LearningStore
from tests.capabilities.test_external_diagnoses import request


@pytest.mark.asyncio
async def test_external_import_routes_are_owner_scoped(as_user):
    from deeptutor.api.routers.mastery_path import (
        commit_external_diagnosis,
        get_diagnostic_import_impact,
        get_external_diagnoses,
        preview_external_diagnosis,
        revoke_external_diagnosis,
    )

    with as_user("owner"):
        store = LearningStore()
        store.save(
            LearningProgress(
                book_id="p",
                modules=[
                    LearningModule(
                        id="m",
                        name="Chapter",
                        order=0,
                        knowledge_points=[
                            KnowledgePoint(id="k", name="Equation", module_id="m", type="concept")
                        ],
                    )
                ],
            )
        )
        req = request()
        preview = await preview_external_diagnosis("p", req)
        body = ExternalCommitRequest(
            **req.model_dump(mode="json"),
            outline_digest=preview["outline_digest"],
            preview_digest=preview["preview_digest"],
            belongs_to_me=True,
            mapping_confirmed=True,
        )
        saved = await commit_external_diagnosis("p", body)
    with as_user("other"):
        for operation in (
            lambda: preview_external_diagnosis("p", req),
            lambda: commit_external_diagnosis("p", body),
            lambda: get_external_diagnoses("p"),
            lambda: get_diagnostic_import_impact("p"),
            lambda: revoke_external_diagnosis("p", saved["import_id"]),
        ):
            with pytest.raises(HTTPException) as denied:
                await operation()
            assert denied.value.status_code == 404
    with as_user("owner"):
        result = await get_external_diagnoses("p")
        assert len(result["imports"]) == 1 and result["imports"][0]["revoked_at"] is None


@pytest.mark.asyncio
async def test_same_account_different_workspace_cannot_read_imports(as_user):
    from deeptutor.api.routers.mastery_path import get_external_diagnoses
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.service import get_content_workspace_service

    with as_user("owner"):
        service = get_content_workspace_service()
        first = service.create_workspace("First")["workspace_id"]
        second = service.create_workspace("Second")["workspace_id"]
        with workspace_context(first):
            LearningStore().save(LearningProgress(book_id="private"))
            assert (await get_external_diagnoses("private"))["imports"] == []
        with workspace_context(second):
            with pytest.raises(HTTPException) as denied:
                await get_external_diagnoses("private")
            assert denied.value.status_code == 404
