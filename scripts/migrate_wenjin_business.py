"""Create native PostgreSQL business schemas. Explicit cutover is separate."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


from deeptutor.services.storage.postgres_migration import migrate, migrate_accounts

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--accounts", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            (migrate_accounts if args.accounts else migrate)(Path(args.source).resolve()),
            ensure_ascii=False,
        )
    )
