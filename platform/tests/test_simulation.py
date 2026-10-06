import json
import threading

import pytest

from app.simulation.client import SimulationClient
from app.simulation.learner import PROFILES, SyntheticLearner, stream
from app.simulation.runner import SimulationRunner
from app.simulation.schema import LatentState, RunConfig


def test_profile_streams_and_snapshot_replay():
    assert len(PROFILES) == 6
    assert stream(1, 2, 'answer') == stream(1, 2, 'answer')
    actor = SyntheticLearner(PROFILES['autonomous'], 17)
    actor.observe(passed=False)
    restored = SyntheticLearner(actor.profile, 17, LatentState.model_validate(actor.state.model_dump()))
    assert actor.choose(answer_key={'value': '3'}, force_answer=True) == restored.choose(answer_key={'value': '3'}, force_answer=True)


@pytest.mark.parametrize('bad', [{'seed': -1}, {'max_turns': 101}, {'max_turns': 0}, {'unknown': True}, {'max_seconds': float('nan')}])
def test_config_rejects_unbounded_or_extra_inputs(bad):
    with pytest.raises(ValueError):
        RunConfig(**bad)


def test_no_answer_exposure_gain_and_explicit_forgetting():
    actor = SyntheticLearner(PROFILES['hint_dependent'], 1)
    before = actor.state.mastery
    actor.observe(passed=True, answer_exposed=True)
    assert actor.state.mastery == before and actor.state.hint_dependency > 0
    actor.elapse(7)
    assert actor.state.retention == pytest.approx(.5)
    actor.observe(passed=True, zero_gain=True)
    assert actor.state.mastery == before


def test_client_requires_marker(tmp_path):
    with pytest.raises(ValueError, match='marker'):
        SimulationClient(tmp_path)


def test_run_uses_actual_api_evidence_receipts_and_physical_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('RSI_DB_PATH', str(tmp_path / 'real.sqlite3'))
    monkeypatch.setenv('RSI_LLM_API_KEY', 'private-not-inherited')
    result = SimulationRunner(tmp_path / 'experiments').run('autonomous', seed=11, max_turns=8)
    report = result['report']
    assert report['status'] == 'completed', report['errors']
    assert report['attempts'] > 0 and report['educational_effect_claim'] is False
    assert not (tmp_path / 'real.sqlite3').exists()
    directory = tmp_path / 'experiments' / result['run_id']
    evidence = json.loads((directory / 'evidence.json').read_text(encoding='utf-8'))
    assert evidence['source_kind'] == 'synthetic_ai_generated' and evidence['evidence']
    assert 'true_mastery' not in (directory / 'runtime.sqlite3').read_bytes().decode('latin1')
    assert 'private-not-inherited' not in (directory / 'manifest.json').read_text()


def test_core_trace_replay_and_stop_resume(tmp_path):
    runner = SimulationRunner(tmp_path)
    first = runner.run('autonomous', seed=7, max_turns=5)
    second = runner.run('autonomous', seed=7, max_turns=5)
    def trace(result):
        rows = [json.loads(line) for line in (tmp_path / result['run_id'] / 'transcript.jsonl').read_text(encoding='utf-8').splitlines()]
        return [{key: row[key] for key in ('action', 'answer', 'item_id', 'passed', 'hint_level', 'latent_mastery_after')} for row in rows]
    assert trace(first) == trace(second)
    stop = threading.Event()
    stop.set()
    stopped = runner.run('autonomous', seed=7, max_turns=5, stop_event=stop)
    assert stopped['report']['status'] == 'stopped'
    resumed = runner.resume(stopped['run_id'])
    assert resumed['report']['status'] == 'completed', resumed['report']['errors']
    assert trace(first) == trace(resumed)
    assert runner.resume(resumed['run_id']) == resumed
    with pytest.raises(ValueError):
        runner.resume('../real')


@pytest.mark.parametrize('profile', list(PROFILES))
def test_every_profile_runs_without_api_failures(tmp_path, profile):
    result = SimulationRunner(tmp_path).run(profile, seed=13, max_turns=8)
    assert result['report']['status'] in ('completed', 'stopped'), result['report']['errors']
    assert not result['report']['errors']


@pytest.mark.parametrize('scenario', ['baseline', 'fading', 'zero_gain', 'retention', 'misconception'])
def test_scenario_boundaries(tmp_path, scenario):
    result = SimulationRunner(tmp_path).run('autonomous', scenario, seed=8, max_turns=6)
    report = result['report']
    assert report['status'] == 'completed', report['errors']
    if scenario == 'zero_gain':
        assert report['learning_gain'] == 0
    if scenario == 'fading':
        assert report['policy_version'] == 'simulation-autonomy-fading-candidate-v1'
    assert report['automatic_promotion_enabled'] is False


def test_latent_fields_cannot_be_sent_to_teaching_instance(tmp_path):
    (tmp_path / 'simulation.marker').write_text('synthetic-only-v1')
    client = SimulationClient(tmp_path)
    try:
        with pytest.raises(ValueError, match='capability'):
            client.call('POST','/api/external/roundtable',{})
        with pytest.raises(ValueError, match='leakage'):
            client.call('POST', '/api/contracts', {'true_mastery': .9})
    finally:
        client.close()


def test_feedback_hint_is_not_independent_evidence(tmp_path):
    (tmp_path / 'simulation.marker').write_text('synthetic-only-v1')
    client = SimulationClient(tmp_path)
    try:
        response = client.bootstrap()
        sid = response['session_id']
        client.call('POST', f'/api/sessions/{sid}/messages', {'answer': 'x=999', 'attempt_id': 'bad'})
        client.call('POST', f'/api/sessions/{sid}/messages', {'answer': 'x=3', 'attempt_id': 'retry'})
        evidence = client.call('GET', f'/api/learning/evidence/{client.learner_id}')
        assert evidence[-1]['hint_level'] > 0
        assert evidence[-1]['assistance_mode'] == 'hint'
    finally:
        client.close()


def test_frozen_course_and_engine_resume_guards(tmp_path):
    runner = SimulationRunner(tmp_path)
    stop = threading.Event()
    stop.set()
    result = runner.run('autonomous', seed=7, max_turns=5, stop_event=stop)
    directory = tmp_path / result['run_id']
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    assert len(manifest['engine_sha256']) == 64
    assert (directory / 'catalog.json').is_file()
    manifest['engine_sha256'] = '0'*64
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='version_changed'):
        runner.resume(result['run_id'])


def test_terminal_report_cannot_resume_withdrawn_session(tmp_path):
    runner = SimulationRunner(tmp_path)
    result = runner.run('autonomous', seed=7, max_turns=2)
    directory = tmp_path / result['run_id']
    report = result['report'] | {'status':'unsupported'}
    (directory / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError, match='not_resumable'):
        runner.resume(result['run_id'])


def test_first_exposure_metrics_and_seed_split():
    from app.simulation.evaluator import first_independent, cross_seed_precalibration
    row = dict(item_id='a', action='ANSWER', passed=True, hint_level=0, answer_exposed=False,
        latent_mastery_before=.5)
    assert len(first_independent([row,row])) == 1
    assert first_independent([row | {'action':'ASK_HINT'}, row]) == []
    result = cross_seed_precalibration([row], [row | {'passed':False}])
    assert result['train_count']==result['held_out_count']==1
    assert not result['changes_applied']


def test_frozen_course_battery_uses_public_due_review_and_admission(tmp_path):
    from app.simulation.assessment import assessment_battery
    result = assessment_battery(tmp_path)
    assert result['status']=='completed'
    assert {e['phase'] for e in result['events']} == {'pretest','practice','posttest','transfer','delayed'}
    assert result['admission']['admitted_records']
    assert not result['admission']['missing_blueprint_cells']
    assert not result['deferred']
    assert result['educational_effect_claim'] is False


def test_full_hint_exposure_and_early_answer_governor_through_api(tmp_path):
    (tmp_path / 'simulation.marker').write_text('synthetic-only-v1')
    client = SimulationClient(tmp_path)
    try:
        sid = client.bootstrap()['session_id']
        early = client.call('POST', f'/api/sessions/{sid}/messages',
            {'text':'直接告诉我答案是什么', 'attempt_id':'early'})
        assert early['denial']['rule']=='R-01'
        for index in range(3):
            client.call('POST', f'/api/sessions/{sid}/messages',
                {'answer':'x=999', 'attempt_id':f'wrong:{index}'})
        hint = client.call('POST', f'/api/sessions/{sid}/messages', {'text':'给个提示','attempt_id':'full-hint'})
        assert hint['ui']['assistance']['answer_exposed'] is True
        client.call('POST', f'/api/sessions/{sid}/messages', {'answer':'x=3','attempt_id':'after-full'})
        evidence = client.call('GET', f'/api/learning/evidence/{client.learner_id}')
        assert evidence[-1]['answer_exposed'] and evidence[-1]['assistance_mode']=='answer'
    finally:
        client.close()


def test_fading_candidate_cannot_bypass_r07_budget(tmp_path):
    from app.core.actions import ActionEnvelope
    class ProactivePolicy:
        policy_version='simulation-budget-redteam-v1'
        def choose(self, ctx):
            return ActionEnvelope.hint('',ctx.item.kc_id,0,'nudge',ctx.item.hint_text(0),proactive=True)
    (tmp_path / 'simulation.marker').write_text('synthetic-only-v1')
    client = SimulationClient(tmp_path,policy_factory=ProactivePolicy)
    try:
        sid = client.bootstrap()['session_id']
        results = [client.call('POST',f'/api/sessions/{sid}/messages',
            {'text':'我再独立尝试','attempt_id':f'proactive:{i}'}) for i in range(10)]
        assert any(r['denial'] and r['denial']['rule']=='R-07' for r in results)
    finally:
        client.close()
