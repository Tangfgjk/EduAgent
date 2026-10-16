"""从仓库根目录验证正式入口；临时 S02 数据库、无模型调用、结束后关闭。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from app.infrastructure.db.database import Database


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="wenjin-start-") as temp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        database_url = "sqlite:///" + (Path(temp) / "startup.sqlite3").as_posix()
        database = Database(database_url)
        database.migrate()
        database.close()
        env = dict(os.environ, RSI_LLM_API_KEY="", RSI_PORT=str(port),
                   RSI_DATABASE_URL=database_url, PYTHONUTF8="1")
        log_path = Path(temp) / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "-m", "app.main"], cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=0.5) as client:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if server.poll() is not None:
                            raise RuntimeError("服务提前退出")
                        try:
                            response = client.get("/openapi.json")
                            response.raise_for_status()
                            break
                        except httpx.TransportError:
                            time.sleep(0.1)
                    else:
                        raise RuntimeError("服务启动超时")
                    assert client.get("/health/ready").json() == {"status": "ready"}
                    assert client.get("/api/v1/auth/me").status_code == 401
                    assert client.post("/api/contracts", json={"goal_text": "x"}).status_code == 404
                    operations = sum(method in {"get", "post", "put", "patch", "delete"}
                                     for path in response.json()["paths"].values() for method in path)
                    print(json.dumps({"startup": "passed", "model_calls": 0, "operations": operations, "legacy_routes": "not_mounted"}))
            except Exception:
                log.flush()
                print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                # Windows 的 venv python.exe 可能是启动器，必须关闭它启动的整棵服务进程树。
                if server.poll() is None and os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                elif server.poll() is None:
                    server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
