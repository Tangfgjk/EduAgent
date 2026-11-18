from fastapi import HTTPException
import pytest

from deeptutor.learning.models import LearningProgress
from deeptutor.learning.storage import LearningStore


@pytest.mark.asyncio
async def test_diagnostic_facts_are_owner_scoped(as_user):
    from deeptutor.api.routers.mastery_path import get_diagnostic_facts

    with as_user("owner"):
        LearningStore().save(LearningProgress(book_id="private"))
        assert (await get_diagnostic_facts("private"))["project_id"] == "private"
    with as_user("other"):
        with pytest.raises(HTTPException) as denied:
            await get_diagnostic_facts("private")
        assert denied.value.status_code == 404
