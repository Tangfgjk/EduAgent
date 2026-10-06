"""Frozen draft assessment battery through public APIs, not a human effect study."""
import argparse
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from app.learning.course_draft import build_equation_course_draft
from app.learning.measurement_admission import MeasurementRecord, admit_measurements
from app.simulation.client import SimulationClient
from app.simulation.learner import PROFILES, SyntheticLearner
from app.simulation.runner import save
from app.simulation.schema import RunConfig


def assessment_battery(root: Path, profile='autonomous', seed=17):
    package = build_equation_course_draft()
    config = RunConfig(profile_id=profile, seed=seed)
    actor = SyntheticLearner(PROFILES[profile], seed)
    directory = Path(root).resolve() / uuid4().hex
    directory.mkdir(parents=True)
    (directory / 'simulation.marker').write_text('synthetic-only-v1', encoding='ascii')
    save(directory / 'course.json', package.model_dump(mode='json'))
    save(directory / 'catalog.json', package.catalog.model_dump(mode='json'))
    save(directory / 'manifest.json', dict(source_kind='synthetic_ai_generated',
        purpose='simulation_precalibration', package_sha256=package.content_hash,
        profile=actor.profile.model_dump(), config=config.model_dump(mode='json')))
    records, events, deferred = [], [], []
    anchor = config.start_at
    phase_days = {'pretest':-30/1440, 'practice':0, 'posttest':.5, 'transfer':1.5, 'delayed':7.5}
    client = SimulationClient(directory, catalog_path=str(directory / 'catalog.json'))
    previous_day = phase_days['pretest']
    try:
        client.call('POST', f'/api/learning/consent/{client.learner_id}',
            {'scopes':['teaching'], 'version':'simulation-battery-v1', 'source':'simulation_fixture'})
        for phase, days in phase_days.items():
            actor.elapse(days - previous_day)
            previous_day = days
            assets = sorted((a for a in package.catalog.assessments if a.kind == phase), key=lambda a:a.ref.key)
            if phase == 'delayed':
                assets.sort(key=lambda a:(a.ref.asset_id.rsplit('-',1)[-1],a.ref.key))
            for index, asset in enumerate(assets):
                if phase == 'delayed' and index == 2:
                    actor.elapse(6)
                at = anchor + timedelta(days=days + (6 if phase == 'delayed' and index >= 2 else 0), seconds=index)
                actor.activate(asset.kc_refs[0].asset_id)
                move = actor.choose(answer_key=asset.answer, force_answer=True)
                body = dict(assessment_id=asset.ref.asset_id, assessment_version=asset.ref.version,
                    attempt_id=f'battery:{phase}:{index}', answer=move.answer, occurred_at=at.isoformat())
                endpoint = 'assessment'
                if phase == 'delayed':
                    # The route recomputes the current due task; each KC has one live review.
                    tasks = client.client.get(f'/api/learning/reviews/{client.learner_id}',
                        params={'as_of':at.isoformat()}).json()['tasks']
                    task = next((t for t in tasks if t['kc_id']==asset.kc_refs[0].asset_id), None)
                    if task is None:
                        deferred.append(dict(assessment_ref=asset.ref.key, reason='no_current_due_task'))
                        continue
                    body['task_id'] = task['task_id']
                    endpoint = 'reviews'
                response = client.call('POST', f'/api/learning/{endpoint}/{client.learner_id}/submit', body)
                duplicate = client.call('POST', f'/api/learning/{endpoint}/{client.learner_id}/submit', body)
                if duplicate != response:
                    raise ValueError('assessment_receipt_unstable')
                evidence = client.call('GET', f'/api/learning/evidence/{client.learner_id}')
                actual = next(e for e in evidence if e['attempt_id']==body['attempt_id'])
                actor.observe(passed=actual['verdict_status']=='passed', zero_gain=phase != 'practice')
                events.append(dict(phase=phase, assessment_ref=asset.ref.key, evidence_ref=actual['evidence_id'],
                    passed=actual['verdict_status']=='passed', latent_mastery=actor.state.mastery,
                    latent_retention=actor.state.retention))
                if phase != 'practice':
                    records.append(MeasurementRecord(evidence_ref=actual['evidence_id'], learner_ref=client.learner_id,
                        assessment_ref=asset.ref, package_sha256=package.content_hash, phase=phase,
                        source_kind='synthetic_ai_generated', purpose='simulation_precalibration',
                        occurred_at=at, attempt=1, verified=actual['verdict_status'] in ('passed','failed'), score=actual['score']))
        admission = admit_measurements(tuple(records), package, purpose='simulation_precalibration',
            lesson_anchor=anchor, as_of=anchor+timedelta(days=14))
        result = dict(run_id=directory.name, status='completed', source_kind='synthetic_ai_generated',
            evaluation_kind='simulation_precalibration', package_sha256=package.content_hash,
            events=events, deferred=deferred, admission=admission.model_dump(mode='json'),
            api_calls=len(client.requests), educational_effect_claim=False, automatic_promotion_enabled=False,
            limitations=['constructed_model_not_human_effect', 'near_transfer_hypothesis_not_teacher_gold',
                         'one_due_review_per_kc_may_leave_measurement_cells_missing', 'numeric_answers_not_reasoning_rubric'])
        save(directory / 'report.json', result)
        save(directory / 'evidence.json', dict(source_kind='synthetic_ai_generated',
            evidence=client.call('GET', f'/api/learning/evidence/{client.learner_id}')))
        client.call('DELETE', f'/api/learning/consent/{client.learner_id}')
        return result
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=list(PROFILES), default='autonomous')
    parser.add_argument('--seed', type=int, default=17)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2] / 'data' / 'simulation-assessment'
    result = assessment_battery(root, args.profile, args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
