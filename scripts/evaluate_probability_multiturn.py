"""Run bounded self-authored dialogues. Default Fake; --live explicitly allows DeepSeek."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.evaluation.multiturn import DEFAULT_CORPUS, HOLDOUT_CORPUS, REGRESSION_CORPUS, MultiHarness, load_corpus, markdown_report, run_multiturn
from app.evaluation.runner import atomic_json, exclusive_lock
from app.infrastructure.db.database import Database
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE, model_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--publish-prefix', type=Path)
    parser.add_argument('--corpus', choices=('baseline', 'holdout', 'regression'), default='baseline')
    parser.add_argument('--check-only', action='store_true', help='Inspect quota and configuration only; no calls or publishing')
    parser.add_argument('--limited-daily', action='store_true', help='Restore account30/site100 daily limits; default unlimited development calls')
    parser.add_argument('--prompt-version', choices=('probability.followup.v2', 'probability.followup.v3'), default='probability.followup.v2')
    args = parser.parse_args()
    mode = 'live' if args.live else 'fake'
    root = (ROOT / '.local/evaluation').resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.live and not args.check_only and not model_key():
        parser.error('DeepSeek key is not configured')
    folder = args.resume.resolve() if args.resume else root / ('multi-' + mode + '-' + datetime.now(BUSINESS_TIMEZONE).strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
    if not folder.is_relative_to(root):
        parser.error('Resume must belong to .local/evaluation')
    outputs = []
    if args.publish_prefix:
        prefix = args.publish_prefix.resolve()
        if not prefix.is_relative_to((ROOT / 'docs/评测').resolve()):
            parser.error('Reports must belong to docs/评测')
        outputs = [prefix.with_suffix('.json'), prefix.with_suffix('.md')]
        if any(p.exists() for p in outputs):
            parser.error('Refusing to overwrite existing report')
    # Same fixed DB and OS lock as the single-turn evaluator. No alternative account/DB flag.
    db = Database('sqlite:///' + (root / ('probability-' + mode + '.sqlite3')).as_posix())
    try:
        with exclusive_lock(root / (mode + '.lock')):
            db.migrate()
            corpus, digest = load_corpus({'baseline': DEFAULT_CORPUS, 'holdout': HOLDOUT_CORPUS, 'regression': REGRESSION_CORPUS}[args.corpus])
            harness = MultiHarness(db, mode, unlimited=not args.limited_daily, prompt_version=args.prompt_version)
            required = sum(len(d.turns) for d in corpus.dialogues)
            if args.check_only:
                check = {'status': 'ready' if harness.remaining() is None or harness.remaining() >= required else 'budget_exhausted',
                    'mode': mode, 'prompt_version': args.prompt_version, 'budget_day': datetime.now(BUSINESS_TIMEZONE).strftime('%Y-%m-%d'),
                    'remaining': harness.remaining(), 'daily_call_limits': 'unlimited' if harness.unlimited else 'account30_site100', 'required': required, 'corpus': corpus.version,
                    'corpus_sha256': digest, 'key_configured': bool(model_key()) if args.live else None,
                    'calls_made': 0}
                if args.live and not check['key_configured']:
                    check['status'] = 'key_missing'
                print(json.dumps(check, ensure_ascii=False))
                return 0 if check['status'] == 'ready' else 2
            if not args.resume and harness.remaining() is not None and harness.remaining() < required:
                print(json.dumps({'status': 'budget_exhausted', 'remaining': harness.remaining(), 'required': required,
                    'message': 'Wait for daily reset; existing account and counters preserved.'}))
                return 2
            report = run_multiturn(harness, corpus, digest, folder, resume=bool(args.resume))
            atomic_json(folder / 'report.json', report)
            markdown = markdown_report(report)
            (folder / 'report.md').write_text(markdown, encoding='utf-8')
            for path in outputs:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x', encoding='utf-8') as handle:
                    handle.write(json.dumps(report, ensure_ascii=False, indent=2) if path.suffix == '.json' else markdown)
            print(json.dumps({'mode': mode, 'folder': str(folder.relative_to(ROOT)), 'summary': report['summary']}, ensure_ascii=False))
    except Exception:
        print('Evaluation interrupted. Credentials and raw exceptions hidden. Resume: ' + str(folder.relative_to(ROOT)), file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
