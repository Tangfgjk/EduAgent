"""Versioned, bounded experiment contracts."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Action = Literal['ANSWER', 'ASK_HINT', 'ASK_EXPLANATION', 'SELF_CHECK', 'REFLECT', 'QUIT']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class LearnerProfile(Contract):
    profile_id: str
    version: str = 'synthetic-profile-v1'
    mastery: Probability = .35
    autonomy: Probability = .4
    help_seeking: Probability = .4
    confidence: Probability = .5
    frustration_sensitivity: Probability = .5
    persistence: Probability = .8
    learning_rate: Probability = .08
    misconception_rate: Probability = .2
    slip: Probability = .1
    guess: Probability = .15
    half_life_days: float = Field(default=7, gt=0, le=365, allow_inf_nan=False)


class KnowledgeState(Contract):
    mastery: Probability
    retention: Probability = 1
    misconception_rate: Probability
    independent_success_streak: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)


class LatentState(Contract):
    state_version: str = 'synthetic-latent-v2-per-kc'
    mastery: Probability
    retention: Probability = 1
    frustration: Probability = 0
    autonomy: Probability
    hint_dependency: Probability = 0
    misconception_rate: Probability
    turn: int = Field(default=0, ge=0)
    active_kc: str = 'default'
    knowledge: dict[str, KnowledgeState] = Field(default_factory=dict)


class LearnerMove(Contract):
    action: Action
    text: str = Field(default='', max_length=2000)
    answer: str | None = Field(default=None, max_length=1000)
    hint_level: int = Field(default=0, ge=0, le=3)
    assistance_mode: Literal['none', 'hint', 'explanation', 'answer'] = 'none'
    answer_exposed: bool = False


class RunConfig(Contract):
    profile_id: str = 'weak_foundation'
    scenario_id: Literal['baseline', 'fading', 'zero_gain', 'retention', 'misconception'] = 'baseline'
    seed: int = Field(default=1, ge=0, le=2147483647)
    max_turns: int = Field(default=20, ge=1, le=100)
    max_seconds: float = Field(default=60, gt=0, le=60, allow_inf_nan=False)
    config_version: Literal['simulation-run-v1'] = 'simulation-run-v1'
    start_at: AwareDatetime = datetime(2026, 1, 1, tzinfo=timezone.utc)


class SimulationReport(Contract):
    run_id: str
    source_kind: Literal['synthetic_ai_generated'] = 'synthetic_ai_generated'
    evaluation_kind: Literal['simulation_precalibration'] = 'simulation_precalibration'
    simulator_version: str = 'structured-learner-v2-per-kc'
    status: Literal['completed', 'stopped', 'timed_out', 'failed', 'unsupported']
    resumable: bool = False
    config: RunConfig
    course_sha256: str
    engine_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    policy_version: str = 'policy_v1'
    profile_version: str = 'synthetic-profile-v1'
    turns: int
    attempts: int
    independent_attempts: int
    independent_passes: int
    assisted_attempts: int
    repeated_attempts: int
    mastery_mae: float | None
    predictive_brier: float | None
    predicted_pass_mean: float | None
    observed_pass_rate: float | None
    prediction_error: float | None
    learning_gain: float
    hint_dependency: Probability
    per_kc: dict = Field(default_factory=dict)
    action_counts: dict[str, int] = Field(default_factory=dict)
    independent_pass_interval: dict | None = None
    elapsed_seconds: float
    api_calls: int
    latency_p50_ms: float
    latency_p95_ms: float
    errors: list[str] = Field(default_factory=list)
    educational_effect_claim: Literal[False] = False
    automatic_promotion_enabled: Literal[False] = False
    topology: Literal['one_learner_isolated_asgi_instance'] = 'one_learner_isolated_asgi_instance'
    limitations: list[str] = Field(default_factory=lambda: [
        'constructed_latent_state_not_human_truth', 'simulator_bias',
        'not_real_parameter_calibration', 'not_shared_backend_load',
        'wall_clock_and_generated_ids_not_replay_equal',
        'constructed_per_kc_latent_states_not_human_truth',
        'cooperative_timeout_checked_between_local_api_calls',
    ])
