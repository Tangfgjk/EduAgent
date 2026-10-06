"""Paired, bounded local experiment suites with disjoint seed/item holdout."""
import argparse
import json
from pathlib import Path

from app.simulation.evaluator import compare_reports, item_seed_precalibration
from app.simulation.learner import PROFILES
from app.simulation.runner import SimulationRunner, save


def experiment_suite(root: Path, *, seeds=(17, 29), max_turns=20, profiles=None):
    if not 2 <= len(seeds) <= 4 or len(set(seeds)) != len(seeds):
        raise ValueError('suite_requires_two_to_four_disjoint_seeds')
    selected = tuple(profiles or PROFILES)
    if not selected or any(profile not in PROFILES for profile in selected):
        raise ValueError('unknown_profile')
    runner = SimulationRunner(root)
    reports, rows = [], []
    for seed in seeds:
        for profile in selected:
            for scenario in ('baseline', 'fading', 'zero_gain', 'retention', 'misconception'):
                result = runner.run(profile, scenario, seed, max_turns)
                reports.append(result['report'])
                directory = Path(root) / result['run_id']
                for line in (directory / 'transcript.jsonl').read_text(encoding='utf-8').splitlines():
                    rows.append(json.loads(line) | {'seed':seed, 'profile_id':profile, 'run_id':result['run_id']})
    summary = compare_reports(reports)
    # Calibration baseline only; training a candidate on policy-dependent help
    # trajectories would hide deployment-policy shifts.
    baseline_ids = {report['run_id'] for report in reports if report['config']['scenario_id'] == 'baseline'}
    summary['double_holdout_precalibration'] = item_seed_precalibration(
        [row for row in rows if row['run_id'] in baseline_ids], training_seeds=seeds[:-1], held_out_seeds=seeds[-1:])
    summary['counts'] = {status:sum(report['status'] == status for report in reports)
        for status in ('completed', 'stopped', 'failed', 'unsupported', 'timed_out')}
    summary['total_runs'] = len(reports)
    summary['seeds'] = list(seeds)
    summary['budget'] = {'max_turns_per_run':max_turns, 'max_seconds_per_run':60, 'max_runs':120}
    save(Path(root) / 'suite-report.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--turns', type=int, default=20)
    parser.add_argument('--seeds', type=int, nargs='+', default=[17, 29])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2] / 'data' / 'simulation-expanded'
    result = experiment_suite(root, seeds=tuple(args.seeds), max_turns=args.turns)
    print(json.dumps({key:result[key] for key in ('total_runs','counts','paired_comparison','double_holdout_precalibration')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
