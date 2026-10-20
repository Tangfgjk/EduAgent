from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field

from app.modules.learning.contracts import Contract
from app.modules.learning.purpose_scopes import PurposeScope
from app.modules.learning.tutor_contracts import CoachingOut
from app.infrastructure.runtime.model_tasks import runtime_status, publish_configuration, pause_configuration


class ModelConfigurationOut(Contract):
    configuration_id: UUID
    vendor: str
    model: str
    max_output_tokens: int
    account_daily_calls: int
    site_daily_calls: int
    prompt_version: str
    created_at: int


class ModelRuntimeOut(Contract):
    provider_catalog: list[dict] = Field(default_factory=list)
    purpose_scopes: list[PurposeScope] = Field(default_factory=list)
    verification_boundary: str = "仅比较有界有理数表达式与已审核参考值；不理解题意、不验证证明或评定教学效果。"
    revision: int
    active: ModelConfigurationOut | None
    deepseek_key_configured: bool
    budget_day: str
    budget_timezone: Literal["Asia/Shanghai"]
    reserved_calls: int
    input_tokens_known: int
    output_tokens_known: int
    missing_usage_calls: int
    billing_unknown_calls: int
    recent: list[CoachingOut]


class PublishModel(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    vendor: Literal['deepseek','fake','openai','anthropic','gemini','qwen','glm','doubao','moonshot']
    model: str | None = Field(default=None,min_length=1,max_length=160,pattern=r'^[a-zA-Z0-9_.:/-]+$')
    prompt_version: Literal["probability.followup.v1", "probability.followup.v2", "probability.followup.v3"] = "probability.followup.v1"
    max_output_tokens: int = Field(default=512, ge=64, le=512, strict=True)
    account_daily_calls: int = Field(default=30, ge=0, le=30, strict=True)
    site_daily_calls: int = Field(default=100, ge=0, le=100, strict=True)
    acknowledge_development_only: Literal[True]


class PauseModel(Contract):
    expected_revision: int = Field(ge=0, strict=True)


def model_runtime_router(admin):
    router = APIRouter(prefix="/api/v1/admin/model-runtime")
    auth = Depends(admin, scope="function")

    @router.get("", response_model=ModelRuntimeOut)
    def inspect(ctx=auth):
        return runtime_status(ctx.session)

    @router.post("/publish", response_model=ModelRuntimeOut)
    def publish(body: PublishModel, ctx=auth):
        return publish_configuration(ctx.session, ctx.actor, body.model_dump())

    @router.post("/pause", response_model=ModelRuntimeOut)
    def pause(body: PauseModel, ctx=auth):
        return pause_configuration(ctx.session, ctx.actor, body.expected_revision)

    return router
