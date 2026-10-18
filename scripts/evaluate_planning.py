"""Three-call synthetic planning evaluation. Fake by default; --live explicitly invokes DeepSeek."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.evaluation.planning import run, PlanningHarness, markdown_report
from app.evaluation.runner import exclusive_lock, atomic_json
from app.infrastructure.db.database import Database
from app.infrastructure.runtime.model_tasks import model_key, BUSINESS_TIMEZONE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    mode = 'live' if args.live else 'fake'
    if args.check_only:
        print(json.dumps({'mode': mode, 'planned_calls': 3, 'calls_made': 0, 'key_ready': not args.live or bool(model_key())}))
        return 0
    if args.live and not model_key():
        parser.error('DeepSeek key unavailable')
    root = (ROOT / '.local/evaluation').resolve()
    folder = args.resume.resolve() if args.resume else root / ('planning-'   + mode + '-' + datetime.now(BUSINESS_TIMEZONE).strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
    if not folder.is_relative_to(root):
        parser.error('Resume must belong to .local/evaluation')
    db = Database('sqlite:///' + (root / ('probability-' + mode + '.sqlite3')).as_posix())
    try:
        with exclusive_lock(root / (mode + '.lock')):
            db.migrate()
            report = run(PlanningHarness(db, mode), folder, resume=bool(args.resume))
            atomic_json(folder / 'report.json', report)
            (folder / 'report.md').write_text(markdown_report(report), encoding='utf-8')
            print(json.dumps({'folder': str(folder.relative_to(ROOT)), 'summary': report['summary']}, ensure_ascii=False))
    except Exception:
        print('Evaluation interrupted; credentials and raw errors hidden. Resume: ' + str(folder.relative_to(ROOT)), file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
