"""Disposable localhost-only browser fixture; never uses the user's learner database."""
import uvicorn
import os

from app.config import Settings
from app.core.schema import utcnow
from app.gateway.routes import create_app
from app.gateway.security import hash_password
from app.learning.service import LearningService
from app.llm.client import FakeLLM
from app.storage.db import Store


def main():
    password = os.environ.get("EDU_QA_PASSWORD")
    if not password:
        raise RuntimeError("Set EDU_QA_PASSWORD to a disposable fixture credential")
    settings = Settings(learner_id="e2e_validation", local_auth_enabled=True,
        assessment_require_ticket=True,
        local_auth_password_hash=hash_password(password),
        local_teacher_token=os.environ.get("EDU_QA_TEACHER_TOKEN", password),
        local_parent_token=os.environ.get("EDU_QA_PARENT_TOKEN", password))
    store = Store()
    LearningService(store).set_consent(settings.learner_id,["teaching"],"synthetic-browser-v1","synthetic-local-qa",utcnow())
    uvicorn.run(create_app(settings,FakeLLM(),store),host="127.0.0.1",port=int(os.environ.get("EDU_QA_PORT", "8002")),log_level="warning")


if __name__ == "__main__":
    main()
