"""Run the frozen developer corpus, default Fake; live mode requires an explicit flag.

Only a dedicated .local evaluation DB is used. Never load production database URLs.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.evaluation.probability import load_corpus, markdown_report
from app.evaluation.runner import exclusive_lock, run_evaluation, atomic_json, EvaluationHarness
from app.infrastructure.db.database import Database
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE, model_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly allow DeepSeek calls on self-authored synthetic cases only')
    parser.add_argument('--max-calls', type=int, default=30, choices=range(1, 31))
    parser.add_argument('--resume', type=Path, help='Resume saved inputs/results without reissuing leased calls')
    parser.add_argument('--publish-prefix', type=Path, help='Write a new .json and .md under docs/评测; never overwrite')
    parser.add_argument('--limited-daily', action='store_true', help='Restore account30/site100 daily limits; default unlimited development calls')
    args = parser.parse_args()
    mode = 'live' if args.live else 'fake'
    root = (ROOT / '.local/evaluation').resolve()
    root.mkdir(parents=True, exist_ok=True)
    corpus, corpus_hash = load_corpus()
    if args.live and not model_key():
        parser.error('DeepSeek key is not configured; use the existing local maintenance command')
    if args.resume:
        folder = args.resume.resolve()
        if not folder.is_relative_to(root):
            parser.error('Resume directory must belong to .local/evaluation')
    else:
        stamp = datetime.now(BUSINESS_TIMEZONE).strftime('%Y%m%d-%H%M%S')
        folder = root / (mode + '-' + stamp + '-' + uuid4().hex[:8])
    outputs = []
    if args.publish_prefix:
        prefix = args.publish_prefix.resolve()
        if not prefix.is_relative_to((ROOT / 'docs/评测').resolve()):
            parser.error('Reports must be written under docs/评测')
        outputs = [prefix.with_suffix('.json'), prefix.with_suffix('.md')]
        if any(path.exists() for path in outputs):
            parser.error('Refusing to overwrite an existing report')
    # Fixed per-mode DB/account means rerunning the CLI cannot silently reset daily allowances.
    db = Database('sqlite:///' + (root / ('probability-' + mode + '.sqlite3')).as_posix())
    try:
        with exclusive_lock(root / (mode + '.lock')):
            db.migrate()
            report = run_evaluation(EvaluationHarness(db, mode, unlimited=not args.limited_daily), corpus, corpus_hash, folder,
                                    max_calls=args.max_calls, resume=bool(args.resume))
            atomic_json(folder / 'report.json', report)
            (folder / 'report.md').write_text(markdown_report(report), encoding='utf-8')
            for path in outputs:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x', encoding='utf-8') as handle:
                    handle.write(json.dumps(report, ensure_ascii=False, indent=2) if path.suffix == '.json' else markdown_report(report))
            print(json.dumps({'mode': mode, 'run_directory': str(folder.relative_to(ROOT)), 'summary': report['summary']}, ensure_ascii=False))
    except Exception:
        print('Evaluation interrupted; configuration, credentials and raw errors are hidden. Resume directory: ' + str(folder.relative_to(ROOT)), file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        db.close()


if __name__ == '__main__':
    main()
