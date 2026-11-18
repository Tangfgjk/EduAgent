from fastapi import HTTPException
import pytest

from deeptutor.learning.models import LearningProgress
from deeptutor.learning.storage import LearningStore


@pytest.mark.asyncio
async def test_catalogue_and_snapshot_access_is_owner_scoped(as_user):
    from deeptutor.api.routers.mastery_path import (
        diagnostic_catalogue,
        diagnostic_dataset_get,
        diagnostic_source,
    )

    with as_user("owner"):
        LearningStore().save(LearningProgress(book_id="private"))
        assert (await diagnostic_catalogue("private"))["project_id"] == "private"
    with as_user("other"):
        for call in (
            lambda: diagnostic_catalogue("private"),
            lambda: diagnostic_dataset_get("private", "x"),
            lambda: diagnostic_source("private", "x"),
        ):
            with pytest.raises(HTTPException) as denied:
                await call()
            assert denied.value.status_code == 404
