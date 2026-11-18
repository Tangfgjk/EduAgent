from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from deeptutor.learning.models import LearningProgress
from deeptutor.learning.storage import LearningStore


@pytest.mark.asyncio
async def test_preference_owner_scoped_and_never_grants_mastery(as_user):
    from deeptutor.api.routers.mastery_path import (
        DiagnosticPreferenceRequest,
        set_diagnostic_preference,
    )

    with as_user("owner"):
        LearningStore().save(LearningProgress(book_id="p"))
        await set_diagnostic_preference("p", DiagnosticPreferenceRequest(strategy="teach_first"))
        state = LearningStore().load("p")
        assert state.learner_profile.teaching_strategy == "teach_first"
        assert not state.quiz_attempts and not state.mastery_levels
        await set_diagnostic_preference("p", DiagnosticPreferenceRequest(strategy="probe_first"))
        assert LearningStore().load("p").learner_profile.teaching_strategy == "probe_first"
    with as_user("other"):
        with pytest.raises(HTTPException) as denied:
            await set_diagnostic_preference(
                "p", DiagnosticPreferenceRequest(strategy="teach_first")
            )
        assert denied.value.status_code == 404
    with pytest.raises(ValidationError):
        DiagnosticPreferenceRequest(strategy="skip_to_mastered")
