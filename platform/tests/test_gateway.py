"""网关冒烟：API 全链路（契约→会话→反思→镜子→进化→治理）。"""
from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.llm.client import FakeLLM
from app.storage.db import Store


def make_client(tmp_path):
    settings = Settings(db_path=str(tmp_path / "t.sqlite3"), learner_id="stu1")
    app = create_app(settings=settings, llm=FakeLLM(), store=Store(":memory:"))
    client = TestClient(app)
    assert client.post("/api/learning/consent/stu1", json={"scopes": ["teaching"],
                       "version": "test-teaching-v1", "source": "learner:test"}).status_code == 200
    return client


def test_full_api_flow(tmp_path):
    c = make_client(tmp_path)

    contract = c.post("/api/contracts", json={"goal_text": "两周学会一元一次方程"}).json()
    assert contract["contract"]["status"] == "active"
    assert contract["contract"]["goal_statement"]["authored_by"] == "student"

    session = c.post("/api/sessions", json={"session_type": "explore"}).json()
    sid = session["session_id"]
    assert "3x + 5" in session["reply"]

    turn = c.post(f"/api/sessions/{sid}/messages", json={"answer": "x=8"}).json()
    assert turn["ui"]["verdict"]["status"] == "failed"

    deny = c.post(f"/api/sessions/{sid}/messages", json={"text": "直接告诉我答案"}).json()
    assert deny["denial"]["rule"] == "R-01"

    reflection = c.post(f"/api/sessions/{sid}/reflection",
                        json={"attribution": "effort_positive", "predicted_score": 0.7}).json()
    assert reflection["status"] == "reflection_saved"

    mirror = c.get("/api/mirror/stu1").json()
    assert mirror["mastery"] and mirror["metacognition"]["calibration"]

    assert c.post("/api/evolution/digest").status_code == 403
    c.post("/api/learning/consent/stu1",json={"scopes":["teaching","evolution"],"version":"explicit-evolution-v1","source":"learner"})
    digest = c.post("/api/evolution/digest").json()
    assert "suggestions" in digest and "kpis" in digest
    assert digest["kpis"]["total_attempts"] == 0  # Earlier teaching-only collection is not retroactively authorized.

    gov = c.get("/api/governance/hard-rules").json()
    assert len(gov["rules"]) == 10 and gov["hash"]


def test_checkpoint_requires_contract(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/sessions", json={"session_type": "checkpoint"})
    assert r.status_code == 400
