"""S01：根目录资源、流式协议和磁盘事实恢复的行为基线。"""
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.llm.client import FakeLLM
from app.orchestration.session import TutorSession, load_bank
from app.storage.db import Store


def test_source_resources_and_request_body_contract():
    store = Store(":memory:")
    try:
        with TestClient(create_app(Settings(), FakeLLM(), store)) as client:
            page = client.get("/")
            assert page.status_code == 200
            assert "text/html" in page.headers["content-type"]
            schema = client.get("/openapi.json").json()
            operation = schema["paths"]["/api/contracts"]["post"]
            assert "application/json" in operation["requestBody"]["content"]
            assert client.post("/api/contracts", json={}).status_code == 422
            assert client.post("/api/sessions/missing/messages", json={}).status_code == 404
            assert load_bank(), "迁移后默认题库应仍可读取"
    finally:
        store.conn.close()


def test_sse_reply_contains_verified_result():
    store = Store(":memory:")
    try:
        with TestClient(create_app(Settings(), FakeLLM(), store)) as client:
            sid = client.post("/api/sessions", json={"session_type": "explore"}).json()["session_id"]
            response = client.post(f"/api/sessions/{sid}/messages/stream", json={"answer": "3"})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = [frame.splitlines() for frame in response.text.strip().split("\n\n")]
            assert [frame[0] for frame in frames] == ["event: status", "event: status", "event: reply"]
            payload = json.loads(frames[-1][1].removeprefix("data: "))
            assert payload["ui"]["verdict"]["status"] == "passed"
    finally:
        store.conn.close()


def test_learning_facts_survive_store_reopen(tmp_path):
    path = str(tmp_path / "learning.sqlite3")
    store = Store(path)
    session = TutorSession(store, FakeLLM(), "baseline-student", session_type="explore")
    session.start()
    session.handle_turn(answer="3")
    event_ids = [event.event_id for event in store.events_for_learner("baseline-student")]
    snapshot_id = store.latest_snapshot("baseline-student").snapshot_id
    store.conn.close()

    reopened = Store(path)
    try:
        assert event_ids
        assert [event.event_id for event in reopened.events_for_learner("baseline-student")] == event_ids
        assert reopened.latest_snapshot("baseline-student").snapshot_id == snapshot_id
    finally:
        reopened.conn.close()
