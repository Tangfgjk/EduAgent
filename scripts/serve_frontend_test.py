"""只用于浏览器验收的隔离服务；固定凭据仅写入 .local 中新建的测试库。"""
import tempfile
import threading
from pathlib import Path

import uvicorn

from app.config import Settings
from app.gateway.v1 import create_app
from app.infrastructure.db.database import Database
from app.modules.identity.service import Identity
from app.worker import Worker


def main():
    root = Path(__file__).resolve().parent.parent
    if not (root / "frontend/dist/student/index.html").exists():
        raise SystemExit("请先在 frontend 中执行 npm run build")
    folder = root / ".local" / "e2e"
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=folder) as temp:
        database = Database("sqlite:///" + (Path(temp) / "test.sqlite3").as_posix())
        database.migrate()
        identity = Identity(database)
        identity.bootstrap("s03.admin", "S03-test-only-password!", "验收管理员")
        _, token, csrf = identity.login("s03.admin", "S03-test-only-password!", "test-setup")
        with database.transaction() as session:
            actor = identity.authorize(session, token, csrf, "admin", True)
            for name in ("s03.one", "s03.two"):
                identity.create_student(session, actor, name, "S03-test-only-password!", "验收学生" + name[-3:])
        settings = Settings(allowed_origins="http://127.0.0.1:8781", data_mode="synthetic")
        app = create_app(settings, database)
        stopped = threading.Event()
        def work():
            worker = Worker(database)
            while not stopped.is_set():
                worker.step()
                stopped.wait(0.1)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        try:
            uvicorn.run(app, host="127.0.0.1", port=8781, log_level="warning", proxy_headers=False)
        finally:
            stopped.set()
            thread.join(timeout=10)
            database.close()


if __name__ == "__main__":
    main()
