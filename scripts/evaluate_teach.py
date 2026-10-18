"""Bounded synthetic teach evaluation. Default Fake; --live explicitly calls DeepSeek."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.evaluation.teach import TeachHarness, load_corpus, run_evaluation, markdown_report
from app.evaluation.runner import atomic_json, exclusive_lock
from app.infrastructure.db.database import Database
from app.infrastructure.runtime.model_tasks import BUSINESS_TIMEZONE, model_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    mode = 'live' if args.live else 'fake'
    corpus, digest = load_corpus()
    if args.check_only:
        ready = not args.live or bool(model_key())
        print(json.dumps({'status': 'ready' if ready else 'key_missing', 'calls_made': 0,
            'planned': len(corpus.cases) + len(corpus.repeat_ids), 'daily_call_limits': 'unlimited', 'mode': mode}))
        return 0 if ready else 2
    if args.live and not model_key():
        parser.error('DeepSeek key unavailable')
    root = (ROOT / '.local/evaluation').resolve()
    folder = args.resume.resolve() if args.resume else root / ('teach-' + mode + '-' + datetime.now(BUSINESS_TIMEZONE).strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
    if not folder.is_relative_to(root):
        parser.error('Resume must belong to .local/evaluation')
    db = Database('sqlite:///' + (root / ('probability-' + mode + '.sqlite3')).as_posix())
    try:
        with exclusive_lock(root / (mode + '.lock')):
            db.migrate()
            report = run_evaluation(TeachHarness(db, mode), corpus, digest, folder, resume=bool(args.resume))
            atomic_json(folder / 'report.json', report)
            (folder / 'report.md').write_text(markdown_report(report), encoding='utf-8')
            print(json.dumps({'folder': str(folder.relative_to(ROOT)), 'summary': report['summary']}, ensure_ascii=False))
    except Exception:
        print('Evaluation interrupted; raw errors and credentials hidden. Resume: ' + str(folder.relative_to(ROOT)), file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
