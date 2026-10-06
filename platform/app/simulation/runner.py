"""Bounded isolated runs; receipt keys allow replay after a checkpoint stop."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from app.orchestration.session import BANK_PATH, load_bank
from app.simulation.client import SimulationClient
from app.simulation.course import freeze_course
from app.simulation.evaluator import compare_reports, metrics, percentile, precalibrate
from app.simulation.learner import PROFILES, SyntheticLearner
from app.simulation.policy import FadingExperimentPolicy
from app.simulation.renderer import ExpressionDecision, LearnerRenderer, VisibleContext
from app.simulation.schema import LatentState, RunConfig, SimulationReport


def save(path: Path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def engine_fingerprint():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def course_fingerprint(directory):
    digest = hashlib.sha256()
    for name in ('question_bank.json', 'catalog.json'):
        digest.update(name.encode())
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()


class SimulationRunner:
    def __init__(self, output_root: Path):
        self.root = Path(output_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def run(self, profile_id='weak_foundation', scenario_id='baseline', seed=1,
            max_turns=20, stop_event=None, on_started=None):
        config = RunConfig(profile_id=profile_id, scenario_id=scenario_id, seed=seed, max_turns=max_turns)
        if profile_id not in PROFILES:
            raise ValueError('unknown_profile')
        directory = self.root / uuid4().hex
        directory.mkdir()
        (directory / 'simulation.marker').write_text('synthetic-only-v1', encoding='ascii')
        package, items = freeze_course()
        save(directory / 'question_bank.json', {'items': [item.model_dump(mode='json') for item in items]})
        save(directory / 'catalog.json', package.catalog.model_dump(mode='json'))
        save(directory / 'course.json', package.model_dump(mode='json'))
        save(directory / 'manifest.json', {
            'source_kind': 'synthetic_ai_generated', 'run_id': directory.name,
            'config': config.model_dump(mode='json'), 'course_sha256': course_fingerprint(directory),
            'engine_sha256': engine_fingerprint(),
            'package_sha256': package.content_hash,
            'teaching_bank_sha256': hashlib.sha256(BANK_PATH.read_bytes()).hexdigest(),
            'profile': PROFILES[profile_id].model_dump(), 'topology': 'one_learner_isolated_asgi_instance',
            'educational_effect_claim': False, 'automatic_promotion_enabled': False})
        if on_started:
            on_started(directory.name)
        return self._execute(directory, config, stop_event)

    def resume(self, run_id, stop_event=None):
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise ValueError('invalid_run_id')
        directory = (self.root / run_id).resolve()
        if directory.parent != self.root or not (directory / 'simulation.marker').is_file():
            raise ValueError('unknown_isolated_run')
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        if (manifest['source_kind'] != 'synthetic_ai_generated'
                or manifest.get('engine_sha256') != engine_fingerprint()
                or manifest['course_sha256'] != course_fingerprint(directory)
                or manifest.get('teaching_bank_sha256') != hashlib.sha256(BANK_PATH.read_bytes()).hexdigest()
                or manifest['profile'] != PROFILES[manifest['config']['profile_id']].model_dump()):
            raise ValueError('simulation_version_changed')
        if (directory / 'report.json').exists():
            report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
            if report['status'] == 'completed':
                return {'run_id': run_id, 'report': report}
            if report['status'] not in ('stopped', 'timed_out') or not report.get('resumable', False):
                raise ValueError('run_not_resumable')
        return self._execute(directory, RunConfig.model_validate(manifest['config']), stop_event)

    def _execute(self, directory, config, stop_event):
        if not self._lock.acquire(blocking=False):
            raise ValueError('runner_busy')
        client = None
        started, errors = time.monotonic(), []
        rows, status = [], 'completed'
        actor = SyntheticLearner(PROFILES[config.profile_id], config.seed)
        renderer = LearnerRenderer()
        frozen_engine = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))['engine_sha256']
        if config.scenario_id == 'misconception':
            actor.state.misconception_rate = .95
        frozen_bank = load_bank(directory / 'question_bank.json')
        bank = {item.stem: item for item in frozen_bank}
        policy_factory = FadingExperimentPolicy if config.scenario_id == 'fading' else None
        def simulation_clock():
            return config.start_at + timedelta(days=actor.state.turn * 2 if config.scenario_id == 'retention' else 0,
                                                minutes=actor.state.turn)
        prior_latencies, prior_requests = [], []
        checkpoint = directory / 'checkpoint.json'
        final = False
        needs_advance, resumable, course_finished = False, False, False
        try:
            client = SimulationClient(directory, policy_factory=policy_factory,
                catalog_path=str(directory / 'catalog.json'), clock=simulation_clock,
                bank_factory=lambda: frozen_bank)
            if checkpoint.exists():
                state = json.loads(checkpoint.read_text(encoding='utf-8'))
                actor.state = LatentState.model_validate(state['latent'])
                rows, response, sid = state['rows'], state['response'], state['session_id']
                client.draft_id = state['draft_id']
                needs_advance = state.get('needs_advance', False)
                course_finished = state.get('course_finished', False)
            else:
                response = client.bootstrap()
                sid = response['session_id']
            def persist():
                save(checkpoint, {'latent': actor.state.model_dump(), 'rows': rows,
                    'response': response, 'session_id': sid, 'draft_id': client.draft_id,
                    'needs_advance': needs_advance, 'course_finished': course_finished})
            def advance():
                nonlocal response, needs_advance, course_finished
                if needs_advance:
                    response = client.call('POST', f'/api/sessions/{sid}/messages',
                        {'text': '下一题，我先独立尝试', 'attempt_id': f"sim:{rows[-1]['turn']}:advance"})
                    needs_advance = False
                    course_finished = response.get('ui', {}).get('current_item') is None
                    persist()
            persist()
            advance()
            for turn in range(len(rows), config.max_turns):
                if course_finished:
                    break
                if stop_event is not None and stop_event.is_set():
                    status = 'stopped'
                    resumable = True
                    break
                if time.monotonic() - started >= config.max_seconds:
                    status = 'timed_out'
                    resumable = True
                    break
                stem = response['ui'].get('current_item')
                item = bank.get(stem)
                if item is None:
                    response = client.call('POST', f'/api/sessions/{sid}/messages',
                        {'text': '下一题', 'attempt_id': f'sim:{turn}:next'})
                    item = bank.get(response['ui'].get('current_item'))
                if item is None or 'value' not in item.answer:
                    status = 'unsupported'
                    errors.append('unsupported_or_exhausted_equation_bank')
                    break
                prior = client.call('GET', f'/api/learning/state/{client.learner_id}')
                estimate = next((s['p_mastery'] for s in prior['mastery'] if s['kc_id'] == item.kc_id), .2)
                new_kc = item.kc_id not in actor.state.knowledge
                actor.activate(item.kc_id)
                if config.scenario_id == 'misconception' and new_kc:
                    actor.state.misconception_rate = .95
                latent_before = actor.state.mastery
                if config.scenario_id == 'retention':
                    actor.elapse(2)
                # A help turn is always followed by an attempt, preventing infinite help loops.
                level = response['ui'].get('assistance', {}).get('hint_level', 0)
                assisted = bool(rows and rows[-1]['action'] in ('ASK_HINT', 'ASK_EXPLANATION'))
                move = actor.choose(answer_key=item.answer, hint_level=level,
                    force_answer=assisted or bool(rows and rows[-1]['action'] in ('SELF_CHECK', 'REFLECT')),
                    kc_id=item.kc_id)
                rendered = renderer.render(ExpressionDecision.model_validate(move.model_dump()),
                    VisibleContext(stem=item.stem, kc_id=item.kc_id, teaching_text=response['reply'][:4000]))
                move = rendered.decision
                if move.action == 'QUIT':
                    client.call('POST', f'/api/sessions/{sid}/messages',
                        {'text': move.text, 'attempt_id': f'sim:{turn}:quit'})
                    rows.append({'turn': turn, 'action': 'QUIT', 'passed': None})
                    actor.state.turn += 1
                    persist()
                    status = 'stopped'
                    break
                body = {'text': move.text, 'answer': move.answer, 'attempt_id': f'sim:{turn}'}
                response = client.call('POST', f'/api/sessions/{sid}/messages', body)
                if move.action == 'REFLECT':
                    client.call('POST', f'/api/sessions/{sid}/reflection',
                        {'attribution': 'effort_positive', 'predicted_score': actor.profile.confidence, 'strategy_keep': True})
                verdict = response['ui'].get('verdict')
                passed = verdict['status'] == 'passed' if verdict and move.action == 'ANSWER' else None
                if move.action == 'ANSWER':
                    evidence = client.call('GET', f'/api/learning/evidence/{client.learner_id}')
                    actual = next((e for e in reversed(evidence) if e['attempt_id'].endswith(f':sim:{turn}')), None)
                    if actual:
                        level, exposed = actual['hint_level'], actual['answer_exposed']
                    else:
                        raise ValueError('answer_missing_evidence')
                    actor.observe(passed=bool(passed), hint_level=level, answer_exposed=exposed,
                                  zero_gain=config.scenario_id == 'zero_gain')
                else:
                    exposed = False
                    actor.state.turn += 1
                concept_corrected = actor.receive_teaching(response.get('actions', []), kc_id=item.kc_id,
                    zero_gain=config.scenario_id == 'zero_gain')
                after = client.call('GET', f'/api/learning/state/{client.learner_id}')
                posterior = next((s['p_mastery'] for s in after['mastery'] if s['kc_id'] == item.kc_id), .2)
                row = dict(turn=turn, action=move.action, text=move.text, answer=move.answer,
                    expression_source=rendered.source,
                    teaching_actions=[{'type':a.get('type'), 'params':a.get('params'),
                        'policy_provenance':a.get('policy_provenance')} for a in response.get('actions', [])],
                    misconception_corrected=concept_corrected,
                    item_id=item.item_id, kc_id=item.kc_id, passed=passed, hint_level=level,
                    answer_exposed=exposed, latent_mastery_before=latent_before,
                    latent_mastery_after=actor.state.mastery, estimated_mastery_after=posterior,
                    predicted_pass=estimate * .9 + (1 - estimate) * .2,
                    denial=response.get('denial'), reply=response['reply'])
                rows.append(row)
                needs_advance = bool(passed)
                persist()
                # Retry the same receipt, and test a persisted reopen without private state access.
                if turn == 2:
                    duplicate = client.call('POST', f'/api/sessions/{sid}/messages', body)
                    if duplicate != response:
                        raise ValueError('unstable_receipt')
                    prior_latencies.extend(client.latencies)
                    prior_requests.extend(client.requests)
                    client.close()
                    client = SimulationClient(directory, policy_factory=policy_factory,
                        catalog_path=str(directory / 'catalog.json'), clock=simulation_clock,
                        bank_factory=lambda: frozen_bank)
                    client.draft_id = json.loads(checkpoint.read_text(encoding='utf-8'))['draft_id']
                    client.call('GET', f'/api/sessions/{sid}')
                if passed:
                    advance()
            final = status in ('completed', 'unsupported')
            if final:
                # Reflection and plan confirmation are client acts, never an AI plan write.
                client.call('POST', f'/api/sessions/{sid}/reflection',
                    {'attribution': 'effort_positive', 'predicted_score': .5, 'strategy_keep': True})
                client.call('POST', f'/api/plans/{client.draft_id}/sign', {'learner_id': client.learner_id})
                path = client.call('GET', '/api/path/recommend')
                save(directory / 'path.json', path)
                save(directory / 'evidence.json', {'source_kind': 'synthetic_ai_generated',
                    'run_id': directory.name, 'evidence': client.call('GET', f'/api/learning/evidence/{client.learner_id}')})
                # Current teaching consent withdrawal must block future reads.
                client.call('DELETE', f'/api/learning/consent/{client.learner_id}')
                if client.client.get(f'/api/learning/state/{client.learner_id}').status_code != 403:
                    raise ValueError('withdrawal_not_enforced')
        except Exception as exc:
            status = 'failed'
            errors.append(type(exc).__name__ + ':' + str(exc)[:200])
        finally:
            latencies = [*prior_latencies, *(client.latencies if client else [])]
            api_calls = len(prior_requests) + (len(client.requests) if client else 0)
            if client:
                client.close()
            self._lock.release()
        (directory / 'transcript.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        save(directory / 'latent.json', actor.state.model_dump())
        summary = metrics(rows)
        if engine_fingerprint() != frozen_engine:
            status, resumable = 'failed', False
            errors.append('simulation_engine_changed_during_run')
        report = SimulationReport(run_id=directory.name, config=config, status=status,
            course_sha256=course_fingerprint(directory), engine_sha256=frozen_engine, turns=len(rows), resumable=resumable,
            learning_gain=(sum(state.mastery-actor.profile.mastery for key,state in actor.state.knowledge.items() if key!='default') /
                max(1,len([key for key in actor.state.knowledge if key!='default']))), hint_dependency=actor.state.hint_dependency,
            per_kc={key:state.model_dump() for key,state in actor.state.knowledge.items() if key != 'default'},
            action_counts={action:sum(row['action'] == action for row in rows) for action in
                ('ANSWER','ASK_HINT','ASK_EXPLANATION','SELF_CHECK','REFLECT','QUIT')},
            elapsed_seconds=time.monotonic() - started, errors=errors,
            policy_version='simulation-autonomy-fading-candidate-v1' if policy_factory else 'policy_v1',
            api_calls=api_calls, latency_p50_ms=percentile(latencies, .5),
            latency_p95_ms=percentile(latencies, .95), **summary).model_dump(mode='json')
        save(directory / 'report.json', report)
        save(directory / 'precalibration.json', precalibrate(rows))
        return {'run_id': directory.name, 'report': report}


def main():
    parser = argparse.ArgumentParser(description='Isolated synthetic learner experiments')
    parser.add_argument('--profile', choices=list(PROFILES), default='weak_foundation')
    parser.add_argument('--scenario', choices=['baseline', 'fading', 'zero_gain', 'retention', 'misconception'], default='baseline')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--turns', type=int, default=20)
    parser.add_argument('--suite', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2] / 'data' / 'simulation'
    runner = SimulationRunner(root)
    if args.suite:
        results = [runner.run(profile, scenario, args.seed, args.turns)['report'] for profile in PROFILES
                   for scenario in ('baseline', 'fading', 'zero_gain', 'retention', 'misconception')]
        print(json.dumps(compare_reports(results), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(runner.run(args.profile, args.scenario, args.seed, args.turns), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
