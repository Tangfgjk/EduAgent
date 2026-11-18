"""Read-only production preflight. Never prints connection secrets."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    os.environ.setdefault("DEEPTUTOR_HOME",str(root))
    sys.path.insert(0,str(root))
    failures=[]
    def check(name, action):
        try:
            action(); print("PASS:",name)
        except Exception as exc:
            failures.append(name); print("FAIL:",name,"("+type(exc).__name__+")")
    def require(condition):
        if not condition: raise ValueError("requirement not met")
    check("Python >=3.11,<3.15",lambda: require((3,11)<=sys.version_info[:2]<(3,15)))
    def node():
        value=subprocess.check_output([shutil.which("node") or "node","--version"],text=True).strip()
        require(int(value.lstrip("v").split(".")[0])>=22)
    check("Node >=22",node)
    if not args.skip_build:
        check("Frontend standalone + assets",lambda:require(all((root/p).exists() for p in ["web/.next/standalone/server.js","web/.next/standalone/.next/static","web/.next/standalone/public"])))
    home=Path(os.environ["DEEPTUTOR_HOME"])
    check("Migrated resource map",lambda:require(bool(json.loads((home/"data/system/postgres/business.json").read_text())["resources"])))
    def database():
        from deeptutor.learning.personal_training import connection
        with connection() as c:
            require(c.execute("SELECT 1").fetchone()[0]==1)
            require(not c.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0])
    check("PostgreSQL application role",database)
    def formula():
        from deeptutor.learning.slide_visuals import formula_png
        require(bool(formula_png(r"\begin{cases}x+y=5\\2x-y=1\end{cases}")))
    check("Local formula renderer",formula)
    def media():
        from deeptutor.learning.classroom_av import ffmpeg
        require(Path(ffmpeg()).is_file())
        from deeptutor.learning.classroom_media import font_path
        require(Path(font_path()).is_file())
    check("FFmpeg + Chinese font",media)
    # Linux needs a configured TTS service. Inspect readiness without calling or billing it.
    def speech():
        from deeptutor.services.config.provider_runtime import resolve_tts_runtime_config
        config=resolve_tts_runtime_config()
        require(bool(config.model))
    if os.name != "nt":check("Server TTS configuration (no synthesis call)",speech)
    print("Preflight failures:",len(failures))
    return bool(failures)


if __name__ == "__main__":sys.exit(main())
