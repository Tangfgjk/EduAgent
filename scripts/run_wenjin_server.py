"""Production launchers for the native Ubuntu/systemd deployment."""
import argparse
import os
from pathlib import Path
import shutil
import sys


def command(kind, root, env):
    root = Path(root).resolve()
    if kind == "backend":
        port = int(env.get("BACKEND_PORT", "8001"))
        args = [sys.executable, "-m", "uvicorn", "deeptutor.api.main:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1"]
    else:
        port = int(env.get("FRONTEND_PORT", "3782"))
        node = shutil.which("node")
        if not node: raise ValueError("Node.js 22 is required")
        server = root / "web/.next/standalone/server.js"
        if not server.is_file(): raise ValueError("Run npm ci --legacy-peer-deps and npm run build in web first")
        env.update(HOSTNAME="127.0.0.1", PORT=str(port), NODE_ENV="production")
        args = [node, str(server)]
    if not 1024 <= port <= 65535: raise ValueError("Choose an unprivileged port between 1024 and 65535")
    env.setdefault("DEEPTUTOR_HOME", str(root))
    env.setdefault("DEEPTUTOR_API_BASE_URL", "http://127.0.0.1:" + env.get("BACKEND_PORT", "8001"))
    return args


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["backend", "frontend"])
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ)
    cmd=command(args.kind,root,env)
    os.chdir(root)
    os.execvpe(cmd[0],cmd,env)


if __name__ == "__main__": main()
