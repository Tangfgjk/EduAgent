"""只用于浏览器验收的隔离服务；固定凭据仅写入 .local 中新建的测试库。"""
import tempfile
import threading
import json
from pathlib import Path

import uvicorn

from app.config import Settings
from app.gateway.v1 import create_app
from app.infrastructure.db.database import Database
from app.modules.identity.service import Identity
from app.worker import Worker, run_loop
from app.infrastructure.runtime.model_tasks import ModelTaskRunner
from app.llm.provider import FakeProvider, ProviderError


def test_provider(config, snapshot):
    """Test server never calls real providers; failure markers only exist in this script."""
    if config.vendor != 'fake':
        raise ProviderError('test_real_model_forbidden')
    if '[开发故障：超时]' in snapshot.get('student_text', ''):
        return FakeProvider((ProviderError('timeout'),))
    if snapshot.get('purpose') == 'planning':
        from app.modules.planning.model import fake_proposal
        return FakeProvider((json.dumps(fake_proposal(snapshot), ensure_ascii=False),))
    if snapshot.get('purpose') == 'roundtable':
        from app.modules.learning.roundtable_coaching import fake_proposal
        return FakeProvider((json.dumps(fake_proposal(snapshot['student_text']), ensure_ascii=False),))
    proposal = {'quote': snapshot['student_text'][:80], 'focus': 'clarify'}
    if snapshot.get('purpose') == 'teach':
        proposal['question'] = '为什么在你的讲解中把颜色作为一个基本结果？'
    return FakeProvider((json.dumps(proposal, ensure_ascii=False),))


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
            worker = Worker(database, model_runner=ModelTaskRunner(database, provider_factory=test_provider))
            run_loop(worker, stop=stopped, poll_seconds=0.1, lane='test-fake')
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
