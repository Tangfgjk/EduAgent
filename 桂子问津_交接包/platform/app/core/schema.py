"""docs/02 核心规格的心智状态 Schema —— Pydantic 落地。

设计规则（docs/02 §1）在本模块的体现：
① 不确定性显式：估计字段带 p + ci95 + model；
② 能力是状态不是特质：全模型 extra="forbid"，任何"固有能力"类字段不允许出现（CI 词表扫描见 tests/test_schema.py）；
④ 符号骨架神经填充：本模块定义骨架，感知器估计取值写入时固化 provenance；
⑥ schema_version 常量 + 快照 append-only（storage 层保证）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid4().hex


class _Forbidden(BaseModel):
    """全部模型禁止未声明字段——"固有能力"类特质字段天然无法写入。"""

    model_config = ConfigDict(extra="forbid")


# ---------- 知识与素养 ----------

KC_ID_PATTERN = r"^[A-Z]+(\.[A-Z0-9_]+)+$"


class KCMastery(_Forbidden):
    kc_id: str = Field(pattern=KC_ID_PATTERN, examples=["MATH.G7.EQ.SOLVE"])
    p_mastery: float = Field(ge=0, le=1)
    ci95: tuple[float, float] = Field(min_length=2, max_length=2)
    last_practiced_at: datetime | None = None
    model: str = "BKT"
    evidence_refs: list[str] = Field(default_factory=list)


class KnowledgeState(_Forbidden):
    kc_masteries: list[KCMastery] = Field(default_factory=list)


class CompetencyState(_Forbidden):
    competency_id: str
    level: int = Field(ge=1, le=5)
    assessor: Literal["agent_rubric", "teacher", "student_self", "peer"] = "agent_rubric"
    evidence_refs: list[str] = Field(default_factory=list)


# ---------- ToM 层：误区 = 竞争性假设 ----------

class MisconceptionHypothesis(_Forbidden):
    hypothesis_id: str
    p: float = Field(ge=0, le=1)
    description: str = ""


class MisconceptionSpace(_Forbidden):
    space_id: str = Field(pattern=KC_ID_PATTERN, examples=["MC.EQ.SIGN"])
    posterior: list[MisconceptionHypothesis] = Field(default_factory=list)
    planned_discriminators: list[str] = Field(default_factory=list)


# ---------- 认知负荷 / 情感动机 / 会话快变 ----------

class CognitiveLoad(_Forbidden):
    estimate: float = Field(default=0.5, ge=0, le=1)
    band: Literal["under", "optimal", "over"] = "optimal"
    signal_refs: list[str] = Field(default_factory=list)


class AffectMotivation(_Forbidden):
    engagement: float = Field(default=0.6, ge=0, le=1)
    frustration: float = Field(default=0.1, ge=0, le=1)
    self_efficacy: float = Field(default=0.5, ge=0, le=1)
    flow_band: Literal["bored", "flow", "anxious"] = "flow"
    attribution_style: Literal[
        "effort_positive", "ability_fixed_negative", "mixed", "unknown"
    ] = "unknown"
    trust_in_agent: float = Field(default=0.6, ge=0, le=1)
    signal_refs: list[str] = Field(default_factory=list)


class LastActionInterpretation(_Forbidden):
    action_ref: str
    perceived: Literal["encourage", "pressure", "condescending", "neutral", "unclear"] = "neutral"
    confidence: float = Field(default=0.5, ge=0, le=1)


class InSessionState(_Forbidden):
    """快变字段（分钟级），与慢变量分速演化（docs/09 MWM 对接）。"""

    attention_focus: str | None = None
    current_intention: Literal[
        "want_answer", "try_again", "ask_help", "reflect", "other"
    ] | None = None
    last_action_interpretation: LastActionInterpretation | None = None


# ---------- 目标 / 策略 / 元认知 / 自主性 ----------

class GoalContext(_Forbidden):
    active_goal_contract_id: str | None = None
    ownership: Literal["student", "negotiated", "externally_imposed"] = "negotiated"
    progress_ratio: float = Field(default=0.0, ge=0, le=1)


class TriedStrategy(_Forbidden):
    strategy_id: str
    skill_version: str = "1.0.0"
    outcome: Literal["effective", "neutral", "ineffective", "harmful"] = "neutral"
    context_tag: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class StrategyProfile(_Forbidden):
    tried_strategies: list[TriedStrategy] = Field(default_factory=list)
    preferred_hint_style: Literal["socratic", "minimal", "worked_example", "visual", "unknown"] = "unknown"
    help_seeking_pattern: Literal["too_early", "healthy", "reluctant", "avoidant", "unknown"] = "unknown"


class Calibration(_Forbidden):
    predicted_mean: float = Field(ge=0, le=1)
    actual_mean: float = Field(ge=0, le=1)
    delta_trend: Literal["improving", "stable", "worsening"] = "stable"


class Metacognition(_Forbidden):
    calibration: Calibration | None = None
    reflection_completion_rate: float = Field(default=0.0, ge=0, le=1)


class AutonomyIndex(_Forbidden):
    """原则 P4 的量化核心：自主性指数，北极星之一。可升可降。"""

    composite: float = Field(default=0.4, ge=0, le=1)
    subscores: dict[str, float] = Field(default_factory=lambda: {
        "goal_self_set_ratio": 0.0,
        "attempt_before_help_ratio": 0.0,
        "self_check_ratio": 0.0,
        "strategy_transfer_ratio": 0.0,
    })
    measurement_note: str = "v1 行为统计，未接 MSLQ 量表"


class Provenance(_Forbidden):
    models: list[str] = Field(default_factory=list)
    data_window: str = ""
    audit_id: str | None = None


# ---------- 心智状态快照 ----------

class MentalStateSnapshot(_Forbidden):
    snapshot_id: str = Field(default_factory=new_id)
    learner_id: str
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime = Field(default_factory=utcnow)
    ttl_seconds: int = Field(default=3600, ge=0)

    knowledge_state: KnowledgeState = Field(default_factory=KnowledgeState)
    competency_state: list[CompetencyState] = Field(default_factory=list)
    misconception_hypotheses: list[MisconceptionSpace] = Field(default_factory=list)
    cognitive_load: CognitiveLoad = Field(default_factory=CognitiveLoad)
    affect_motivation: AffectMotivation = Field(default_factory=AffectMotivation)
    in_session_state: InSessionState = Field(default_factory=InSessionState)
    goal_context: GoalContext = Field(default_factory=GoalContext)
    strategy_profile: StrategyProfile = Field(default_factory=StrategyProfile)
    metacognition: Metacognition = Field(default_factory=Metacognition)
    autonomy_index: AutonomyIndex = Field(default_factory=AutonomyIndex)
    provenance: Provenance = Field(default_factory=Provenance)

    def mastery_of(self, kc_id: str) -> KCMastery | None:
        return next((k for k in self.knowledge_state.kc_masteries if k.kc_id == kc_id), None)

    def misconception_space(self, space_id: str) -> MisconceptionSpace | None:
        return next((m for m in self.misconception_hypotheses if m.space_id == space_id), None)


# ---------- 学习契约（docs/02 §3.1） ----------

class GoalStatement(_Forbidden):
    text: str
    authored_by: Literal["student"] = "student"  # R-02：终稿只能由学生签署


class SuccessCriterion(_Forbidden):
    criterion_id: str = Field(default_factory=new_id)
    kind: Literal["post_test", "teach_back", "self_made_set"] = "post_test"
    threshold: float = Field(default=0.8, ge=0, le=1)
    kc_refs: list[str] = Field(default_factory=list)


class ExternalDeadline(_Forbidden):
    source: Literal["lms", "syllabus", "exam_calendar"]
    title: str
    due_at: datetime
    kc_refs: list[str] = Field(default_factory=list)


class GoalContract(_Forbidden):
    goal_contract_id: str = Field(default_factory=new_id)
    learner_id: str
    goal_statement: GoalStatement
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    difficulty_band: dict = Field(default_factory=lambda: {"min": 0.4, "max": 0.7})
    time_box: dict = Field(default_factory=dict)
    external_deadline_refs: list[ExternalDeadline] = Field(default_factory=list)
    negotiation_log_refs: list[str] = Field(default_factory=list)
    plan_version_refs: list[str] = Field(default_factory=list)
    status: Literal["drafting", "active", "achieved", "revised", "abandoned"] = "drafting"


class PlanVersion(_Forbidden):
    version_id: str = Field(default_factory=new_id)
    goal_contract_id: str
    project_id: str = "default"
    status: Literal["draft", "confirmed", "superseded"] = "draft"
    change_reason: str = "初始版本"
    content: dict = Field(default_factory=dict)
    confirmed_at: datetime | None = None


# ---------- 交互事件与验证裁决（docs/04 §1/§2） ----------

class ActorRef(_Forbidden):
    kind: Literal["student", "companion", "derived_agent"]
    ref: str = "companion_v1"


class Observation(_Forbidden):
    kind: Literal[
        "utterance", "answer", "artifact", "latency", "revision",
        "help_seeking", "env_result", "reflection",
    ]
    text: str | None = None
    payload: dict = Field(default_factory=dict)


class ContextSnapshot(_Forbidden):
    mental_state_snapshot: str = ""
    policy_version: str = "policy_v1"
    state_machine_pos: dict = Field(default_factory=dict)


class InteractionEvent(_Forbidden):
    event_id: str = Field(default_factory=new_id)
    learner_pseudo_id: str
    session_id: str = ""
    ts: datetime = Field(default_factory=utcnow)
    actor: ActorRef = Field(default_factory=lambda: ActorRef(kind="companion"))
    action_ref: str | None = None
    observation: Observation
    verdict_ref: str | None = None
    context_snapshot: ContextSnapshot = Field(default_factory=ContextSnapshot)
    consent_scope: Literal["teaching", "evolution", "research"] = "teaching"
    audit_id: str | None = None


class TaxonomyLevel(str, Enum):
    remember = "remember"
    understand = "understand"
    apply = "apply"
    analyze = "analyze"
    evaluate = "evaluate"
    create = "create"


class Verdict(_Forbidden):
    artifact_id: str = ""
    status: Literal["passed", "failed", "partial", "unverifiable"]
    score: float = Field(default=0.0, ge=0, le=1)
    rubric: dict = Field(default_factory=dict)
    taxonomy_level: TaxonomyLevel = TaxonomyLevel.apply
    misconception_hits: list[str] = Field(default_factory=list)
    explainability: str = ""
    verifier_id: str = "verifier.sympy.1.0"


# ---------- 题库条目（seeds/question_bank.json 的模型） ----------

class QuestionHint(_Forbidden):
    level: int = Field(ge=0, le=3)
    form: Literal["nudge", "directive", "worked_partial", "worked_full"]
    text: str


class QuestionItem(_Forbidden):
    item_id: str
    kc_id: str = Field(pattern=KC_ID_PATTERN)
    difficulty: float = Field(ge=0, le=1)
    taxonomy_level: TaxonomyLevel = TaxonomyLevel.apply
    pattern: Literal[
        "slides_lecture", "quiz", "interactive_sim", "pbl",
        "live_book", "whiteboard", "practice", "inquiry",
    ] = "practice"
    stem: str
    answer: dict  # {"var": "x", "value": "3"} 或 {"expr": "0.8x-10"}
    misconception_links: list[str] = Field(default_factory=list)
    hints: list[QuestionHint] = Field(default_factory=list)

    def hint_text(self, level: int) -> str | None:
        for h in self.hints:
            if h.level == level:
                return h.text
        return None
