import json
from types import SimpleNamespace

import pytest

from app.core.actions import ActionEnvelope
from app.simulation.course import freeze_course
from app.simulation.evaluator import item_seed_precalibration, paired_comparison, wilson_interval
from app.simulation.learner import PROFILES, SyntheticLearner
from app.simulation.policy import FadingExperimentPolicy
from app.simulation.runner import SimulationRunner
from app.simulation.schema import LatentState


def test_per_kc_learning_and_forgetting_do_not_share_mastery():
    actor = SyntheticLearner(PROFILES['autonomous'], 1)
    actor.activate('MATH.G7.EQ.BALANCE')
    actor.observe(passed=True)
    learned = actor.state.mastery
    actor.activate('MATH.G7.EQ.SOLVE')
    assert actor.state.mastery == actor.profile.mastery
    actor.elapse(7)
    assert actor.state.retention == pytest.approx(.5)
    actor.activate('MATH.G7.EQ.BALANCE')
    assert actor.state.mastery == learned and actor.state.retention == pytest.approx(.5)
    clone = SyntheticLearner(actor.profile, 1, LatentState.model_validate(actor.state.model_dump()))
    assert clone.choose(answer_key={'value':'3'}, force_answer=True, kc_id='MATH.G7.EQ.BALANCE') == actor.choose(answer_key={'value':'3'}, force_answer=True, kc_id='MATH.G7.EQ.BALANCE')


def test_all_six_behavior_actions_are_actual_choices_not_unused_enum():
    actions = set()
    for profile in PROFILES.values():
        for seed in range(100):
            actor = SyntheticLearner(profile, seed)
            actor.state.turn = 4
            actor.state.frustration = .9
            actions.add(actor.choose(answer_key={'value':'3'}).action)
    assert actions == {'ANSWER', 'ASK_HINT', 'ASK_EXPLANATION', 'SELF_CHECK', 'REFLECT', 'QUIT'}


def test_concept_correction_requires_grounded_content_not_generic_reward():
    actor = SyntheticLearner(PROFILES['misconception'], 3)
    kc = 'MATH.G7.EQ.SOLVE'
    actor.activate(kc)
    initial = actor.state.misconception_rate
    generic = ActionEnvelope.hint('',kc,1,'directive','做得好，继续加油！').model_dump()
    assert not actor.receive_teaching([generic], kc_id=kc)
    mathematical = ActionEnvelope.hint('',kc,1,'directive','等式两边同时减去同一常数，仍保持相等。').model_dump()
    assert actor.receive_teaching([mathematical], kc_id=kc)
    assert actor.state.misconception_rate < initial
    lower = actor.state.misconception_rate
    assert not actor.receive_teaching([mathematical], kc_id=kc, zero_gain=True)
    assert actor.state.misconception_rate == lower
    assert not actor.receive_teaching([mathematical], kc_id='MATH.G7.EQ.BALANCE')


def test_fading_changes_executed_hint_candidate_from_same_frozen_bank(monkeypatch):
    from app.orchestration.policy import BuiltInPolicyV1
    package, bank = freeze_course()
    item = bank[0]
    monkeypatch.setattr(BuiltInPolicyV1, 'choose', lambda self,ctx:
        ActionEnvelope.hint('',item.kc_id,2,'worked_partial',item.hint_text(2),proactive=False))
    ctx = SimpleNamespace(item=item, snapshot=SimpleNamespace(mastery_of=lambda kc:SimpleNamespace(p_mastery=.85)))
    action = FadingExperimentPolicy().choose(ctx)
    assert action.params['ladder_level'] == 0 and action.params['text'] == item.hint_text(0)
    assert not action.params['proactive']
    assert all(item.item_id in {asset.ref.asset_id for asset in package.catalog.assessments} for item in bank)


def test_paired_report_matches_course_seed_profile_and_exposes_missing():
    def report(seed, scenario, score, status='completed'):
        return dict(config=dict(profile_id='a',seed=seed,scenario_id=scenario), course_sha256='frozen',
            observed_pass_rate=score,status=status,engine_sha256='frozen-engine')
    summary = paired_comparison([report(1,'baseline',.4),report(1,'fading',.5),
        report(2,'baseline',.3),report(2,'fading',.6),report(3,'baseline',.7)])
    assert summary['matched_pairs'] == 2 and summary['missing_or_ineligible_pairs'] == 1
    assert summary['mean_delta'] == pytest.approx(.2) and summary['bootstrap95']
    assert not summary['educational_effect_claim']
    assert wilson_interval(0,0) is None
    interval = wilson_interval(3,4)
    assert interval['lower'] < .75 < interval['upper']


def test_pairing_does_not_mix_engine_versions_or_legacy_reports():
    baseline = dict(config=dict(profile_id='a', seed=1, scenario_id='baseline', max_turns=20),
        course_sha256='frozen', engine_sha256='first', status='completed', observed_pass_rate=.4)
    candidate = {**baseline, 'config':{**baseline['config'], 'scenario_id':'fading'}, 'engine_sha256':'changed'}
    assert paired_comparison([baseline, candidate])['matched_pairs'] == 0
    baseline.pop('engine_sha256')
    candidate.pop('engine_sha256')
    assert paired_comparison([baseline, candidate])['matched_pairs'] == 0


def test_double_holdout_disjoint_items_and_seeds_with_no_auto_changes():
    rows = [dict(seed=seed,item_id=f'item-{item}',run_id=str(seed),action='ANSWER',passed=True,
        hint_level=0,answer_exposed=False,latent_mastery_before=.4) for seed in (1,2) for item in range(20)]
    result = item_seed_precalibration(rows,training_seeds=[1],held_out_seeds=[2])
    assert result['train_count'] and result['held_out_count']
    assert not set(result['training_items']) & set(result['held_out_items'])
    assert not result['changes_applied'] and not result['automatic_promotion_enabled']
    with pytest.raises(ValueError):
        item_seed_precalibration(rows,training_seeds=[1],held_out_seeds=[1])


def test_frozen_course_uses_same_item_id_stem_and_kc_in_runtime_and_catalog(tmp_path):
    result = SimulationRunner(tmp_path).run('misconception',seed=17,max_turns=12)
    assert result['report']['status'] in ('completed','stopped'), result['report']['errors']
    directory = tmp_path / result['run_id']
    catalogue = json.loads((directory/'catalog.json').read_text(encoding='utf-8'))
    assets = {asset['ref']['asset_id']:asset for asset in catalogue['assessments']}
    bank = json.loads((directory/'question_bank.json').read_text(encoding='utf-8'))['items']
    for item in bank:
        assert item['stem'] == assets[item['item_id']]['stem']
        assert item['kc_id'] == assets[item['item_id']]['kc_refs'][0]['asset_id']
    report = result['report']
    assert report['per_kc'] and report['action_counts']['ASK_EXPLANATION'] >= 0
    assert not report['errors']
