from enum import IntEnum
from typing import Literal
from uuid import UUID

from app.modules.learning.contracts import Contract

class EvidenceDays(IntEnum):
    fortnight = 14
    month = 30
    quarter = 90


Source = Literal["all", "synthetic", "adult_test", "real_student"]
Category = Literal["all", "answer", "checkpoint", "hint", "reflection", "activity", "self_report", "human_judgment"]


class EvidenceCounts(Contract):
    facts: int = 0
    answers: int = 0
    checkpoint_answers: int = 0
    checkpoint_passed: int = 0
    hints: int = 0
    reflections: int = 0
    activity_contributions: int = 0
    self_reports: int = 0
    human_judgments: int = 0


class EvidenceGroup(EvidenceCounts):
    source_kind: str


class EvidenceDaily(EvidenceCounts):
    day: str
    source_kind: str


class EvidenceStage(Contract):
    source_kind: str
    stage: str
    passed: int = 0
    failed: int = 0
    unverifiable: int = 0


class EvidenceRecord(Contract):
    record_id: UUID
    fact_id: UUID
    workspace_id: UUID
    workspace_title: str
    archived: bool
    record_kind: str
    source_kind: str
    stage: str
    status: str | None
    created_at: int
    task_id: str
    excerpt: str


class CompetencyState(Contract):
    name: str
    status: Literal["rubric_pending"] = "rubric_pending"
    score: None = None
    explanation: str = "量规尚未审核，过程材料未自动映射为此维度的能力证据。"


class EvidenceOut(Contract):
    assistance: str = 'all'
    scope: Literal["global", "workspace"]
    workspace_count: int
    days: int
    timezone: Literal["UTC+08:00"] = "UTC+08:00"
    starts_at: int
    as_of: int
    groups: list[EvidenceGroup]
    daily: list[EvidenceDaily]
    stages: list[EvidenceStage]
    competencies: list[CompetencyState]
    model_estimates: Literal[0] = 0
    records: list[EvidenceRecord]
    record_count: int
    offset: int
    limit: Literal[20] = 20
    policy_version: Literal["evidence-observations.v1"] = "evidence-observations.v1"
