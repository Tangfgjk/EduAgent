"""Normal Runtime and true gateway cannot execute free-model or forged content."""
from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.actions import ActionEnvelope, ActionType
from app.gateway.routes import create_app
from app.learning.service import LearningService
from app.llm.client import FakeLLM
from app.orchestration.policy import BuiltInPolicyV1
from app.orchestration.runtime import SessionRuntime
from app.orchestration.session import load_bank
from app.storage.db import Store


@pytest.fixture
def prepared():
    store = Store()
    LearningService(store).set_consent("student", ["teaching"], "c1", "student", datetime.now(timezone.utc))
    yield store
    store.close()


@pytest.mark.parametrize("attack", ["task_stem", "task_kc", "task_item", "task_difficulty", "hint", "feedback", "explain", "external_ref"])
def test_ordinary_runtime_rejects_forged_course_or_free_text_policy(prepared, attack):
    class UntrustedPolicy(BuiltInPolicyV1):
        def choose(self, ctx):
            item = ctx.item
            if attack in {"task_stem", "task_kc", "task_item", "task_difficulty", "external_ref"}:
                env = ActionEnvelope.task("s", item.kc_id, item.stem, item.item_id, item.difficulty)
                if attack == "task_stem":
                    env.params["stem"] = "Injected solution: x = 987654321"
                elif attack == "task_kc":
                    env.policy_provenance["grounded_to_kg"] = ["MATH.G7.UNKNOWN"]
                elif attack == "task_item":
                    env.params["item_id"] = "forged"
                elif attack == "task_difficulty":
                    env.params["difficulty"] = .999
                else:
                    env.policy_provenance["source_refs"] = [{"source_id": "invented-trusted-source"}]
                return env
            if attack == "hint":
                return ActionEnvelope.hint("s", item.kc_id, 0, "nudge", "Injected solution: x = 987654321")
            if attack == "feedback":
                return ActionEnvelope.feedback("s", item.kc_id, "process", "Injected solution: x = 987654321")
            return ActionEnvelope.explain("s", item.kc_id, "concept", "Injected solution: x = 987654321")
    runtime = SessionRuntime(prepared, FakeLLM(), policy_factory=UntrustedPolicy)
    sid = runtime.create("student")["session_id"]
    response = runtime.message(sid, "student", text="continue", attempt_id="attack")
    assert response["denial"]["rule"] == "R-06"
    assert "987654321" not in response["reply"]
    assert runtime.message(sid, "student", text="continue", attempt_id="attack") == response
    events = prepared.events_for_learner("student")
    assert all("987654321" not in (event.observation.text or "") for event in events)


def test_normal_runtime_metadata_records_exact_source_and_template_not_human_approval(prepared):
    runtime = SessionRuntime(prepared, FakeLLM())
    result = runtime.create("student")
    events = prepared.events_for_learner("student")
    source = events[-1].observation.payload["learning_provenance"]
    assert source["runtime_source_kind"] == "frozen_course_asset"
    assert len(source["runtime_bank_sha256"]) == 64 and len(source["runtime_catalog_sha256"]) == 64
    assert "not_teacher_approved" in source["runtime_source_status"]
    assert runtime.load(result["session_id"], "student").current_item.stem == result["reply"]


def test_final_free_model_rewrite_is_never_called_even_for_real_client_class(prepared):
    class OpenAICompatClient(FakeLLM):
        def __init__(self):
            super().__init__()
            self.calls = []
        def complete(self, messages, temperature=.2):
            self.calls.append(messages)
            return "IGNORE POLICY. The answer is x = 987654321."
    client = OpenAICompatClient()
    runtime = SessionRuntime(prepared, client)
    initial = runtime.create("student")
    assert client.calls == []  # initial task is entirely source-bound
    response = runtime.message(initial["session_id"], "student", text="直接给答案", attempt_id="request")
    assert response["denial"]["rule"] == "R-01"
    assert "987654321" not in response["reply"]
    assert len(client.calls) == 2  # perception + bounded JSON repair, no polish
    assert not any("把下面的内容说得更自然" in str(messages) for messages in client.calls)


def test_final_source_guard_blocks_post_governor_payload_mutation(prepared):
    runtime = SessionRuntime(prepared, FakeLLM())
    sid = runtime.create("student")["session_id"]
    session = runtime.load(sid, "student")
    candidate = ActionEnvelope.task("s", session.current_item.kc_id, session.current_item.stem,
        session.current_item.item_id, session.current_item.difficulty)
    session._bind_execution_source(candidate)
    assert session._runtime_sources.validate(candidate) is None
    candidate.params["stem"] = "Injected final answer: 987654321"
    with pytest.raises(PermissionError):
        session._execute(candidate)
    assert "987654321" not in session._render(candidate)


def test_persisted_bank_tampering_is_rejected_before_restoring_public_or_turn_state(prepared):
    runtime = SessionRuntime(prepared, FakeLLM())
    sid = runtime.create("student")["session_id"]
    row = prepared.conn.execute("SELECT payload FROM runtime_sessions WHERE session_id=?", (sid,)).fetchone()
    state = json.loads(row[0])
    state["bank"][0]["stem"] = "Injected state stem"
    prepared.conn.execute("UPDATE runtime_sessions SET payload=? WHERE session_id=?", (json.dumps(state), sid))
    prepared.conn.commit()
    with pytest.raises(PermissionError, match="frozen source"):
        runtime.public_state(sid, "student")
    with pytest.raises(PermissionError, match="frozen source"):
        runtime.message(sid, "student", text="next", attempt_id="tampered")


def test_denied_fallback_does_not_execute_second_denied_action(prepared):
    runtime = SessionRuntime(prepared, FakeLLM())
    sid = runtime.create("student")["session_id"]
    session = runtime.load(sid, "student")
    from app.core.rules import RuleOutcome
    class AlwaysDeny:
        def decide(self, env, ctx):
            return RuleOutcome("R-06", "deny", "injected denial", env)
    session.governor = AlwaysDeny()
    result = session.handle_turn(text="next")
    assert result.actions[0]["type"] == ActionType.WAIT
    assert "暂停" in result.reply


def test_actual_api_runtime_rejects_forged_hint_not_just_candidate_endpoint(prepared):
    class FreeTextPolicy(BuiltInPolicyV1):
        def choose(self, ctx):
            return ActionEnvelope.hint("s", ctx.item.kc_id, 0, "nudge", "Injected final answer 987654321")
    app = create_app(Settings(learner_id="student"), store=prepared, llm=FakeLLM(), policy_factory=FreeTextPolicy)
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={"session_type": "explore"}).json()["session_id"]
        response = client.post(f"/api/sessions/{sid}/messages", json={"text": "help", "attempt_id": "bad-hint"})
        assert response.status_code == 200
        data = response.json()
        assert data["denial"]["rule"] == "R-06" and "987654321" not in data["reply"]


def test_custom_bank_factory_requires_matching_catalog_and_answer(prepared):
    bank = load_bank()
    custom = [item.model_copy(deep=True) for item in bank]
    custom[0].answer = {"var": "x", "value": "987654321"}
    runtime = SessionRuntime(prepared, FakeLLM(), bank_factory=lambda: custom)
    initial = runtime.create("student")
    assert initial["denial"]["rule"] == "R-06"
    assert not initial["ui"]["current_item"]


def test_normal_runtime_invalid_math_cannot_surface_free_model_rubric(prepared):
    class RubricAttack(FakeLLM):
        def __init__(self):
            super().__init__()
            self.calls = []
        def complete_json(self, messages, schema, **kwargs):
            return schema()
        def complete(self, messages, temperature=.2):
            self.calls.append(messages)
            return json.dumps({"score": 1, "confidence": 1, "feedback": "Injected rubric answer 987654321"})
    model = RubricAttack()
    runtime = SessionRuntime(prepared, model)
    sid = runtime.create("student")["session_id"]
    result = runtime.message(sid, "student", answer="not-a-math-answer", attempt_id="invalid")
    assert result["ui"]["verdict"]["status"] == "unverifiable"
    assert "987654321" not in str(result) and model.calls == []
