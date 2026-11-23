"""Run the backend and standalone frontend in one small-server container."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    sys.path.insert(0, str(root))
    from deeptutor.services.config.runtime_settings import get_ws_max_size

    if os.environ.get("AUTH_ENABLED", "").lower() != "true":
        raise RuntimeError("Production container requires authentication")
    backend_port = os.environ.get("BACKEND_PORT", "8001")
    frontend_port = os.environ.get("FRONTEND_PORT", "3782")
    env = dict(os.environ, HOSTNAME="0.0.0.0", PORT=frontend_port)
    commands = [
        [sys.executable, "-m", "uvicorn", "deeptutor.api.main:app",
         "--host", "127.0.0.1", "--port", backend_port, "--workers", "1",
         "--ws-max-size", str(get_ws_max_size()), "--timeout-keep-alive", "65"],
        ["node", str(root / "web/.next/standalone/server.js")],
    ]
    processes = []
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, env=env, start_new_session=True))
        while not stopping:
            for process in processes:
                if process.poll() is not None:
                    return process.returncode or 1
            time.sleep(0.25)
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == "__main__":
    sys.exit(main())
