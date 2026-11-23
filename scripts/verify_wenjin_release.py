"""Verify restored PostgreSQL rows and runtime files before starting the application."""
import argparse
import json
import os
from pathlib import Path

import psycopg
from backup_wenjin_release import inventory, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    root = Path(os.environ.get("DEEPTUTOR_HOME", Path(__file__).resolve().parents[1])).resolve()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    config = json.loads((root / "data/system/postgres/connection.json").read_text())
    with psycopg.connect(**config) as connection:
        actual = inventory(connection)
    if actual != manifest["tables"]:
        different = sorted(k for k in set(actual) | set(manifest["tables"]) if actual.get(k) != manifest["tables"].get(k))
        raise RuntimeError("Restored database differs: " + ", ".join(different))
    overrides = {"data/system/postgres/connection.json"}
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root / "data"):
            raise RuntimeError("Invalid manifest path")
        if name not in overrides and sha256(path) != digest:
            raise RuntimeError("Restored file differs: " + name)
    print(json.dumps({
        "verified": True, "tables": len(actual),
        "rows": sum(x["count"] for x in actual.values()),
        "files": len(manifest["files"]), "configuration_overrides": sorted(overrides),
    }))


if __name__ == "__main__":
    main()
