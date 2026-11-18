"""Create a PostgreSQL custom-format backup without exposing credentials."""

from datetime import datetime, timezone
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
folder = root / "data" / "backups" / "postgres"
folder.mkdir(parents=True, exist_ok=True)
name = datetime.now(timezone.utc).strftime("wenjin-%Y%m%dT%H%M%S%fZ.dump")
target = folder / name
partial = target.with_suffix(".partial")
command = [
    "docker",
    "compose",
    "-f",
    str(root / "compose.wenjin-postgres.yaml"),
    "exec",
    "-T",
    "--user",
    "postgres",
    "postgres",
    "pg_dump",
    "--dbname=wenjin",
    "--format=custom",
    "--no-owner",
    "--no-acl",
]
try:
    with partial.open("xb") as output:
        subprocess.run(command, stdout=output, check=True, cwd=root)
    partial.rename(target)
except BaseException:
    partial.unlink(missing_ok=True)
    raise
print(target)
