from typing import Literal
from pydantic import Field
from app.modules.learning.contracts import Contract


class ObservationRef(Contract):
    fact_id: str
    workspace_id: str
    workspace_title: str
    archived: bool
    source_kind: str
    task_id: str
    content_version: str
    created_at: int
    kind: str


class KnowledgeNode(Contract):
    node_id: str
    unit_id: str | None
    unit_title: str
    kc_id: str | None
    knowledge_points: list[str]
    content_version: str | None
    domain: str | None
    mapping_status: str
    source_kind: str | None
    facts: int
    task_count: int
    reference_count: int
    refs: list[ObservationRef]
    measures: dict[str,int]
    evidence_state: Literal['has_records','no_records']


class Measure(Contract):
    source_kind: str
    metric: str
    label: str
    submissions: int
    passed: int
    failed: int
    unverifiable: int
    refs: list[ObservationRef]


class SupportEvent(ObservationRef):
    mode: str
    hints_used: int
    basis: str
    event: str
    status: str | None
    restorable: bool | None
    ability_judgment: Literal[False]


class CompetencyMaterial(Contract):
    name: str
    candidate_count: int
    refs: list[ObservationRef]
    routing_rule: str
    status: Literal['awaiting_review','no_classified_material']
    score: None = None
    reviewed_count: Literal[0] = 0


class PlanningObservations(Contract):
    schema_version: Literal['learning-observations.v1']
    fingerprint: str
    starts_at: int
    as_of: int
    scope: Literal['workspace','global']
    source_filter: str
    workspace_count: int
    counts: dict[str,int]
    observations: list[dict]
    references: list[ObservationRef]
    uncertainties: list[str]
    delivery: Literal['local_only']
    model_dispatch: str


class ReviewMatrixRow(Contract):
    source_kind: str
    reason: str
    cards: int
    due: int
    scheduled: int
    window_recalls: int
    refs: list[ObservationRef]


class LearningInsights(Contract):
    assistance: str = 'all'
    review_matrix: list[ReviewMatrixRow] = Field(default_factory=list)
    schema_version: Literal['learning-observations.v1']
    scope: Literal['workspace','global']
    workspace_count: int
    starts_at: int
    as_of: int
    days: int
    source_filter: str
    include_archived: bool
    knowledge_nodes: list[KnowledgeNode]
    prerequisite_edges: list[dict[str,str]]
    measures: list[Measure]
    support_events: list[SupportEvent]
    support_event_count: int
    competencies: list[CompetencyMaterial]
    planning_summary: PlanningObservations
    facts_count: int
    limitations: list[str]
