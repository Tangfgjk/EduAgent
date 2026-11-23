"""Relocate existing filesystem metadata without rewriting user messages or identifiers."""
import argparse
import json
import os
from pathlib import Path
import re

import psycopg
from psycopg import sql
from psycopg.types.json import Json, Jsonb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise RuntimeError("Use --apply on the verified restored target only")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    source = manifest["source_root"].replace("\\", "/").rstrip("/")
    root = Path(os.environ.get("DEEPTUTOR_HOME", Path(__file__).resolve().parents[1])).resolve()
    if str(root).replace("\\", "/").lower() == source.lower():
        raise RuntimeError("Refusing to relocate the source installation")
    missing = set()
    path_keys = re.compile(r"(?:^|_)(?:path|paths|directory|dir|root|file|files)$", re.I)

    def rewrite(value, key=""):
        if isinstance(value, dict):
            return {k: rewrite(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(item, key) for item in value]
        if isinstance(value, str) and path_keys.search(key):
            normalized = value.replace("\\", "/")
            if normalized.lower().startswith(source.lower() + "/"):
                relative = normalized[len(source) + 1:]
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise RuntimeError("Metadata escapes runtime home")
                if target.exists():
                    return str(target)
                missing.add(relative)
        return value

    json_files = 0
    for name in manifest["files"]:
        path = root / name
        if path.suffix.lower() != ".json" or not path.is_file():
            continue
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            continue
        updated = rewrite(original)
        if updated != original:
            path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            json_files += 1
    business = root / "data/system/postgres/business.json"
    data = json.loads(business.read_text())
    resources = data["resources"]
    for name in resources:
        if not (root / name).resolve().is_relative_to(root / "data"):
            raise RuntimeError("Invalid resource mapping")
    data["databases"] = [str(root / name) for name in resources]
    business.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    config = json.loads((root / "data/system/postgres/connection.json").read_text())
    changes = {}
    with psycopg.connect(**config) as connection:
        columns = connection.execute(
            "SELECT table_schema,table_name,column_name,udt_name FROM information_schema.columns "
            "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
            "AND table_schema NOT LIKE 'pg_toast%' AND udt_name IN ('text','varchar','json','jsonb') "
            "ORDER BY 1,2,3"
        ).fetchall()
        for n, (schema, table, column, kind) in enumerate(columns):
            query = sql.SQL(
                "SELECT ctid::text, {} FROM {}.{} WHERE strpos({}::text,%s)>0"
            ).format(sql.Identifier(column), sql.Identifier(schema), sql.Identifier(table), sql.Identifier(column))
            with connection.cursor(name="relocate_" + str(n)) as cursor:
                cursor.itersize = 128
                cursor.execute(query, (Path(source).name,))
                for ctid, value in cursor:
                    original = value
                    parsed = False
                    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                        try:
                            value = json.loads(value)
                            parsed = True
                        except ValueError:
                            pass
                    updated = rewrite(value, column)
                    if updated == value:
                        continue
                    if kind == "jsonb":
                        updated = Jsonb(updated)
                    elif kind == "json":
                        updated = Json(updated)
                    elif parsed:
                        updated = json.dumps(updated, ensure_ascii=False)
                    connection.execute(
                        sql.SQL("UPDATE {}.{} SET {}=%s WHERE ctid=%s::tid").format(
                            sql.Identifier(schema), sql.Identifier(table), sql.Identifier(column)
                        ), (updated, ctid)
                    )
                    label = schema + "." + table
                    changes[label] = changes.get(label, 0) + 1
    print(json.dumps({
        "updated_json_files": json_files, "database_updates": changes,
        "stable_resources": len(resources), "missing_path_count": len(missing),
        "missing_paths": sorted(missing)[:20],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
