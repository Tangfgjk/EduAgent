"""Stateless random streams and explicit, non-BKT latent dynamics."""
import hashlib
import math

from app.learning.verifier import _parse_arithmetic, _sanitize
from app.simulation.schema import KnowledgeState, LatentState, LearnerMove, LearnerProfile


PROFILES = {
    'weak_foundation': LearnerProfile(profile_id='weak_foundation', mastery=.15, help_seeking=.6),
    'confident_knowledge_low_confidence': LearnerProfile(
        profile_id='confident_knowledge_low_confidence', mastery=.85, confidence=.15, help_seeking=.65),
    'autonomous': LearnerProfile(profile_id='autonomous', mastery=.65, autonomy=.9, help_seeking=.05),
    'frustration_sensitive': LearnerProfile(profile_id='frustration_sensitive', mastery=.25,
        frustration_sensitivity=.95, persistence=.35),
    'hint_dependent': LearnerProfile(profile_id='hint_dependent', mastery=.25,
        autonomy=.15, help_seeking=.85, learning_rate=.025),
    'misconception': LearnerProfile(profile_id='misconception', mastery=.4, misconception_rate=.9),
}


def stream(seed: int, turn: int, mechanism: str) -> float:
    key = f'synthetic-stream-v1:{seed}:{turn}:{mechanism}'.encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], 'big') / 2**64


class SyntheticLearner:
    def __init__(self, profile: LearnerProfile, seed: int, state: LatentState | None = None):
        self.profile, self.seed = profile.model_copy(deep=True), seed
        self.state = state or LatentState(mastery=profile.mastery, autonomy=profile.autonomy,
                                         misconception_rate=profile.misconception_rate)

    def activate(self, kc_id: str):
        """Scalar fields remain a compatibility projection of the active KC."""
        if not kc_id or len(kc_id) > 100:
            raise ValueError('invalid_kc')
        self._store_active()
        knowledge = self.state.knowledge.get(kc_id)
        if knowledge is None:
            knowledge = KnowledgeState(mastery=self.profile.mastery, misconception_rate=self.profile.misconception_rate)
            self.state.knowledge[kc_id] = knowledge
        self.state.active_kc = kc_id
        self.state.mastery, self.state.retention = knowledge.mastery, knowledge.retention
        self.state.misconception_rate = knowledge.misconception_rate

    def _store_active(self):
        previous = self.state.knowledge.get(self.state.active_kc)
        self.state.knowledge[self.state.active_kc] = KnowledgeState(mastery=self.state.mastery,
            retention=self.state.retention, misconception_rate=self.state.misconception_rate,
            independent_success_streak=previous.independent_success_streak if previous else 0,
            attempts=previous.attempts if previous else 0)

    def pass_probability(self, hint_level=0):
        s, p = self.state, self.profile
        recall = s.mastery * s.retention
        independent = (recall * (1 - p.slip) + (1 - recall) * p.guess) * (1 - s.misconception_rate * .35)
        return min(.99, independent + hint_level * .16)

    def choose(self, *, answer_key: dict, hint_level=0, force_answer=False, kc_id=None,
               allow_deliberation=True) -> LearnerMove:
        if kc_id is not None:
            self.activate(kc_id)
        s, p = self.state, self.profile
        if not force_answer and s.frustration > .6 and stream(self.seed, s.turn, 'quit') > p.persistence:
            return LearnerMove(action='QUIT', text='我想先休息一下。')
        if not force_answer and not hint_level and stream(self.seed, s.turn, 'help') < p.help_seeking * (1 - s.autonomy * .5) * (1.2 - .4 * p.confidence):
            if stream(self.seed, s.turn, 'help_kind') < .4 + s.misconception_rate * .3:
                return LearnerMove(action='ASK_EXPLANATION', text='我需要原理提示：为什么等式两边要做同一运算？')
            return LearnerMove(action='ASK_HINT', text='我卡住了，可以给一个提示吗？')
        if not force_answer and allow_deliberation and s.turn > 0:
            deliberation = stream(self.seed, s.turn, 'deliberation')
            if deliberation < .12 * s.autonomy:
                return LearnerMove(action='SELF_CHECK', text='我想先代回检查自己的步骤，暂时不要给答案。')
            if deliberation < .12 * s.autonomy + .08:
                return LearnerMove(action='REFLECT', text='我想回顾刚才使用的逆运算，下次先独立尝试。')
        value = answer_key.get('value')
        if value is None:
            raise ValueError('unsupported_answer_type')
        number = _parse_arithmetic(_sanitize(str(value)))
        if not number.is_real or not number.is_finite:
            raise ValueError('unsupported_answer_value')
        passed = stream(self.seed, s.turn, 'answer') < self.pass_probability(hint_level)
        if not passed:
            # Stable sign and coefficient mistakes; do not ask an LLM to invent truth.
            number = -number if number != 0 and stream(self.seed, s.turn, 'sign') < s.misconception_rate else number + 1
        answer = f"{answer_key.get('var', 'x')}={number}"
        return LearnerMove(action='ANSWER', answer=answer,
            hint_level=hint_level, assistance_mode='hint' if hint_level else 'none')

    def observe(self, *, passed: bool, hint_level=0, answer_exposed=False, zero_gain=False, elapsed_days=0):
        s, p = self.state, self.profile
        gain = 0 if zero_gain or answer_exposed else p.learning_rate * (1 - s.mastery) * (1 if not hint_level else .35)
        retention = min(1, s.retention + (.2 if passed else .05)) * math.exp(-math.log(2) * elapsed_days / p.half_life_days)
        self.state = LatentState(
            mastery=min(1, s.mastery + gain), retention=retention,
            frustration=min(1, max(0, s.frustration + (-.2 if passed else .25 * p.frustration_sensitivity))),
            autonomy=min(1, max(0, s.autonomy + (.015 if passed and not hint_level else -.005))),
            hint_dependency=min(1, max(0, s.hint_dependency + (.08 if hint_level or answer_exposed else -.03))),
            misconception_rate=max(0, s.misconception_rate - (.04 if passed and not answer_exposed and not zero_gain else 0)),
            turn=s.turn + 1,
            active_kc=s.active_kc, knowledge=s.knowledge,
        )
        self._store_active()
        kc = self.state.knowledge[self.state.active_kc]
        kc.attempts += 1
        kc.independent_success_streak = kc.independent_success_streak + 1 if passed and not hint_level and not answer_exposed else 0

    def receive_teaching(self, actions, *, kc_id, zero_gain=False):
        """Only grounded executed mathematical scaffolds change misconception.

        This is an explicit synthetic hypothesis, never interpretation by LLM.
        Generic praise and mere success do not stand in for a conceptual lesson.
        """
        self.activate(kc_id)
        learned = False
        for action in actions:
            params = action.get('params', {})
            grounded = action.get('policy_provenance', {}).get('grounded_to_kg', [])
            text = params.get('text', '')
            conceptual = '两边' in text and any(word in text for word in ('同时', '同一', '相等'))
            if kc_id in grounded and action.get('type') in ('HINT', 'EXPLAIN', 'FEEDBACK') and conceptual:
                full = params.get('form') == 'worked_full' or params.get('mode') == 'worked_full'
                if not full and not zero_gain:
                    self.state.misconception_rate = max(0, self.state.misconception_rate - self.profile.learning_rate * .75)
                    learned = True
        self._store_active()
        return learned

    def elapse(self, days: float):
        if not math.isfinite(days) or days < 0:
            raise ValueError('elapsed days must be finite and nonnegative')
        self.state.retention *= math.exp(-math.log(2) * days / self.profile.half_life_days)
        self._store_active()
        for key, knowledge in self.state.knowledge.items():
            if key != self.state.active_kc:
                knowledge.retention *= math.exp(-math.log(2) * days / self.profile.half_life_days)
