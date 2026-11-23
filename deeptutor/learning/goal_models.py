"""Learner-confirmed goals; editing and assessment revisions are distinct."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GoalLevel = Literal["understand", "apply", "transfer"]


class GoalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    assessment_version: int = Field(ge=1)
    level: GoalLevel
    summary: str = Field(min_length=1, max_length=1500)
    time_budget: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=600)
    confirmed_at: float = Field(ge=0)
    evidence_since: float = Field(ge=0)
    confirmed_by: str = "learner"
    request_id: str = ""


class GoalProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    level: GoalLevel
    summary: str = Field(min_length=1, max_length=1500)
    reason: str = Field(min_length=1, max_length=600)
    learner_quote: str = Field(default="", max_length=1500)
    session_id: str = ""
    turn_id: str = ""
    created_at: float
