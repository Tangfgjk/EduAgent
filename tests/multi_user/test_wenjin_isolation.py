"""Wenjin state, source excerpts and policy consent remain owner scoped."""

from fastapi import HTTPException
import pytest

from deeptutor.learning.models import KnowledgePoint, LearningModule, LearningProgress
from deeptutor.learning.storage import LearningStore


@pytest.mark.asyncio
async def test_learning_evidence_and_policy_controls_do_not_cross_accounts(as_user):
    from deeptutor.api.routers.mastery_path import (
        WenjinConsentRequest,
        get_wenjin_state,
        update_wenjin_consent,
    )

    with as_user("owner"):
        store = LearningStore()
        store.save(
            LearningProgress(
                book_id="private_path",
                wenjin_enabled=True,
                research_sources=[
                    {
                        "id": "source",
                        "url": "https://example.org",
                        "title": "Private source",
                        "excerpt": "Private excerpt",
                    }
                ],
                modules=[
                    LearningModule(
                        id="m",
                        name="Module",
                        order=0,
                        knowledge_points=[
                            KnowledgePoint(
                                id="kp", name="Private objective", module_id="m", type="concept"
                            ),
                        ],
                    )
                ],
            )
        )
        assert (await get_wenjin_state("private_path"))["sources"][0][
            "excerpt"
        ] == "Private excerpt"
    with as_user("other"):
        with pytest.raises(HTTPException) as denied:
            await get_wenjin_state("private_path")
        assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as denied:
            await update_wenjin_consent("private_path", WenjinConsentRequest(enabled=True))
        assert denied.value.status_code == 404
    with as_user("owner"):
        assert not LearningStore().load("private_path").evolution
