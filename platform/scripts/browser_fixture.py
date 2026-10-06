"""Disposable localhost-only browser fixture; never uses the user's learner database."""
import uvicorn

from app.config import Settings
from app.core.schema import utcnow
from app.gateway.routes import create_app
from app.learning.service import LearningService
from app.llm.client import FakeLLM
from app.storage.db import Store


def main():
    local = Settings.load()
    settings = Settings(learner_id="e2e_validation", local_auth_enabled=True,
        local_auth_password_hash=local.local_auth_password_hash,
        local_teacher_token=local.local_teacher_token, local_parent_token=local.local_parent_token)
    store = Store()
    LearningService(store).set_consent(settings.learner_id,["teaching"],"synthetic-browser-v1","synthetic-local-qa",utcnow())
    uvicorn.run(create_app(settings,FakeLLM(),store),host="127.0.0.1",port=8002,log_level="warning")


if __name__ == "__main__":
    main()
