"""Allowlisted experiment candidate; every proposal still passes Governor."""
from app.core.actions import ActionEnvelope, ActionType
from app.orchestration.policy import BuiltInPolicyV1, HELP_SEEK


class FadingExperimentPolicy(BuiltInPolicyV1):
    policy_version = 'simulation-autonomy-fading-candidate-v1'

    def choose(self, ctx):
        base = super().choose(ctx)
        if ctx.item and base.type == ActionType.HINT:
            # Fade only from observable system estimates, never simulator latent.
            mastery = ctx.snapshot.mastery_of(ctx.item.kc_id)
            reduction = 2 if mastery and mastery.p_mastery >= .8 else 1
            level = max(0, base.params['ladder_level'] - reduction)
            base.params.update(ladder_level=level,
                form=('nudge', 'directive', 'worked_partial', 'worked_full')[level],
                text=ctx.item.hint_text(level) or ctx.item.hint_text(0))
        return base
