"""Offline summaries only; candidates cannot activate production parameters."""
import math
import hashlib
import random
from statistics import mean


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def metrics(rows):
    answers = [r for r in rows if r['action'] == 'ANSWER' and r.get('passed') is not None]
    independent = first_independent(rows)
    predicted = [r['predicted_pass'] for r in independent]
    observed = [float(r['passed']) for r in independent]
    return dict(attempts=len(answers), independent_attempts=len(independent),
        independent_passes=sum(observed),
        assisted_attempts=sum(bool(r['hint_level'] or r['answer_exposed']) for r in answers),
        repeated_attempts=len(answers) - len({r['item_id'] for r in answers}),
        mastery_mae=mean(abs(r['estimated_mastery_after'] - r['latent_mastery_after']) for r in answers) if answers else None,
        predictive_brier=mean((p - y)**2 for p, y in zip(predicted, observed)) if independent else None,
        predicted_pass_mean=mean(predicted) if independent else None,
        observed_pass_rate=mean(observed) if independent else None,
        prediction_error=abs(mean(predicted) - mean(observed)) if independent else None,
        independent_pass_interval=wilson_interval(sum(observed), len(observed)))


def wilson_interval(passed, count):
    if not count:
        return None
    z = 1.959963984540054
    p = passed / count
    center = (p + z*z/(2*count)) / (1 + z*z/count)
    radius = z * math.sqrt(p*(1-p)/count + z*z/(4*count*count)) / (1 + z*z/count)
    return {'lower': max(0, center-radius), 'upper': min(1, center+radius),
            'count': count, 'method': 'wilson95_descriptive_not_population_inference'}


def compare_reports(reports):
    return {'source_kind': 'synthetic_ai_generated', 'evaluation_kind': 'simulation_sensitivity',
        'educational_effect_claim': False, 'automatic_promotion_enabled': False,
        'candidates_only': True, 'runs': reports, 'paired_comparison': paired_comparison(reports),
        'limitations': ['constructed_models', 'no_population_or_causal_claim', 'not_shared_backend_load']}


def precalibrate(rows, *, candidate_id='offline-guess-slip-grid-v1'):
    independent = first_independent(rows)
    train = [r for r in independent if r['turn'] % 2 == 0]
    held_out = [r for r in independent if r['turn'] % 2 != 0]
    def score(items, guess, slip):
        if not items:
            return None
        return mean((r['latent_mastery_before'] * (1 - slip) + (1 - r['latent_mastery_before']) * guess - float(r['passed']))**2 for r in items)
    candidates = [(score(train, g, s), g, s) for g in (.05, .15, .25) for s in (.05, .15, .25)] if train else []
    best = min(candidates) if candidates else None
    return {'candidate_id': candidate_id, 'source_kind': 'synthetic_ai_generated',
        'evaluation_kind': 'simulation_precalibration', 'automatic_promotion_enabled': False,
        'changes_applied': False, 'educational_effect_claim': False,
        'train_count': len(train), 'held_out_count': len(held_out),
        'parameters': {'guess': best[1], 'slip': best[2]} if best else None,
        'held_out_brier': score(held_out, best[1], best[2]) if best else None,
        'limitations': ['within_trajectory_exploratory_split_not_real_validation', 'latent_oracle_used_only_offline']}


def first_independent(rows):
    seen, result = set(), []
    for row in rows:
        item = row.get('item_id')
        if item is None:
            continue
        first = item not in seen
        seen.add(item)
        if first and row['action'] == 'ANSWER' and row.get('passed') is not None and not row['hint_level'] and not row['answer_exposed']:
            result.append(row)
    return result


def cross_seed_precalibration(training, validation):
    def score(rows, guess, slip):
        return mean((r['latent_mastery_before'] * (1 - slip) + (1 - r['latent_mastery_before']) * guess
                     - float(r['passed']))**2 for r in rows) if rows else None
    candidates = [(score(training, g, s), g, s) for g in (.05, .15, .25) for s in (.05, .15, .25)] if training else []
    best = min(candidates) if candidates else None
    return dict(source_kind='synthetic_ai_generated', evaluation_kind='simulation_precalibration',
        split='disjoint_run_seed', train_count=len(training), held_out_count=len(validation),
        parameters={'guess': best[1], 'slip': best[2]} if best else None,
        held_out_brier=score(validation, best[1], best[2]) if best else None,
        automatic_promotion_enabled=False, changes_applied=False, educational_effect_claim=False,
        limitations=['shared_course_items_not_item_holdout', 'latent_oracle_not_human_truth'])


def paired_comparison(reports, candidate='fading'):
    pairs = {}
    failures = sum(report.get('status') not in ('completed', 'stopped') for report in reports)
    for report in reports:
        cfg = report['config']
        key = (cfg['profile_id'], cfg['seed'], report['course_sha256'], report.get('engine_sha256'),
               cfg.get('max_turns'), report.get('simulator_version'), report.get('profile_version'))
        pairs.setdefault(key, {})[cfg['scenario_id']] = report
    deltas, missing = [], 0
    for pair in pairs.values():
        base, alternative = pair.get('baseline'), pair.get(candidate)
        if not base or not alternative or any(not r.get('engine_sha256') or r['status'] != 'completed' or r.get('observed_pass_rate') is None for r in (base, alternative)):
            missing += 1
            continue
        deltas.append(alternative['observed_pass_rate'] - base['observed_pass_rate'])
    interval = None
    if len(deltas) >= 2:
        rng = random.Random(8128)
        samples = sorted(mean(rng.choices(deltas, k=len(deltas))) for _ in range(2000))
        interval = [samples[49], samples[1949]]
    return dict(candidate=candidate, metric='first_independent_pass_rate_delta',
        matched_pairs=len(deltas), missing_or_ineligible_pairs=missing, failed_or_unsupported_runs=failures,
        mean_delta=mean(deltas) if deltas else None, bootstrap95=interval,
        uncertainty='insufficient_pairs' if interval is None else 'descriptive_constructed_profile_seed_bootstrap',
        sample_unit='matched_profile_seed_frozen_course', educational_effect_claim=False,
        automatic_promotion_enabled=False)


def item_seed_precalibration(rows, *, training_seeds, held_out_seeds):
    """Double holdout: validation seeds AND item IDs are disjoint from training."""
    training_seeds, held_out_seeds = set(training_seeds), set(held_out_seeds)
    if training_seeds & held_out_seeds or not training_seeds or not held_out_seeds:
        raise ValueError('seed_split_must_be_nonempty_and_disjoint')
    def item_split(item):
        return int.from_bytes(hashlib.sha256(item.encode()).digest()[:2], 'big') % 2
    independent = []
    by_run = {}
    for row in rows:
        by_run.setdefault((row['seed'], row.get('profile_id'), row.get('run_id')), []).append(row)
    for trajectory in by_run.values():
        independent.extend(first_independent(trajectory))
    training = [row for row in independent if row['seed'] in training_seeds and item_split(row['item_id']) == 0]
    validation = [row for row in independent if row['seed'] in held_out_seeds and item_split(row['item_id']) == 1]
    result = cross_seed_precalibration(training, validation)
    result.update(split='disjoint_seed_and_item_hash_partition', training_seeds=sorted(training_seeds),
        held_out_seeds=sorted(held_out_seeds), training_items=sorted({r['item_id'] for r in training}),
        held_out_items=sorted({r['item_id'] for r in validation}),
        excluded_count=len(independent)-len(training)-len(validation),
        limitations=['synthetic_latent_oracle_not_human_calibration', 'small_course_item_partition_may_be_empty'])
    return result
