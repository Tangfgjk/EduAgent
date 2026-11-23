import asyncio
import time

import pytest

from deeptutor.learning import teach_ai as teach
from deeptutor.learning.models import (
    KnowledgePoint,
    LearningModule,
    LearningProgress,
    PendingQuestion,
)
from deeptutor.learning.storage import LearningStore


@pytest.fixture
def store(tmp_path):
    store = LearningStore(tmp_path / "learning")
    store.save(
        LearningProgress(
            book_id="p",
            name="我教AI验收",
            wenjin_enabled=True,
            modules=[
                LearningModule(
                    id="m",
                    name="方程",
                    order=0,
                    knowledge_points=[
                        KnowledgePoint(
                            id="k", name="二元一次方程的条件", module_id="m", type="concept"
                        )
                    ],
                )
            ],
        )
    )
    return store


def review(payload, *, correction=False):
    return {
        "claims": [
            {
                "dimension": "concept",
                "status": "needs_clarification" if correction else "supported",
                "observation": "需要区分一次与二次"
                if correction
                else "说明了两个未知数以及次数条件",
                "human_turn_ids": [payload["current_human_id"]],
                "source_ids": [],
            }
        ],
        "follow_up_goal": "为什么xy不符合一次条件？",
        "needs_tutor": correction,
        "clarification": "xy的总次数是2，请区分未知数个数与项的次数。" if correction else "",
    }


@pytest.fixture
def model(monkeypatch):
    calls = []

    async def respond(role, payload):
        calls.append((role, payload))
        return (
            review(payload)
            if role == "reviewer"
            else {
                "message": "那xy为什么不满足条件呢？"
                if role == "student"
                else "可以先观察每一项中未知数次数之和。"
            }
        ), "test-model"

    monkeypatch.setattr(teach, "model_reply", respond)
    return calls


def test_full_teaching_report_cites_only_human_and_never_grants_mastery(store, model):
    s = teach.create(store, "p", "k", request_id="create")
    result = asyncio.run(
        teach.turn(store, "p", s["id"], "reply1", "explain", "有两个未知数，含未知数项的次数是1。")
    )
    assert [c[0] for c in model] == ["reviewer", "student"]
    student_input = model[1][1]
    assert not {"claims", "materials", "reference_answer", "clarification"}.intersection(
        student_input
    )
    assert [m["role"] for m in result["messages"]] == ["ai_student", "human", "ai_student"]
    report = teach.control(store, "p", s["id"], "finish")["report"]
    humans = {m["id"] for m in result["messages"] if m["role"] == "human"}
    assert report["human_turn_count"] == 1 and set(report["claims"][0]["human_turn_ids"]) <= humans
    p = store.load("p")
    assert p.learning_evidence == [] and p.quiz_attempts == [] and p.qualitative_mastery == {}
    assert teach.handoff(p)[0]["human_excerpts"][0]["text"].startswith("有两个")
    assert "requests" not in result and "reviews" not in result and "job_token" not in result
    assert (
        teach.load(LearningStore(store.db_path.parent), "p", s["id"])["messages"]
        == result["messages"]
    )
    assert teach.control(store, "p", s["id"], "finish")["report"] == report
    with pytest.raises(ValueError):
        asyncio.run(teach.turn(store, "p", s["id"], "reply2", "explain", "继续"))


def test_failed_student_call_retries_cached_review_without_duplicate_human(store, monkeypatch):
    s = teach.create(store, "p", "k")
    roles = []
    fail = [True]

    async def respond(role, payload):
        roles.append(role)
        if role == "student" and fail[0]:
            fail[0] = False
            raise OSError("private provider exception")
        return (review(payload) if role == "reviewer" else {"message": "能举个例子吗？"}), "test"

    monkeypatch.setattr(teach, "model_reply", respond)
    with pytest.raises(teach.ModelUnavailable, match="原话已保存"):
        asyncio.run(teach.turn(store, "p", s["id"], "req", "explain", "二元与一次都有条件。"))
    failed = teach.load(store, "p", s["id"])
    assert failed["latest_request"]["retryable"] and "private" not in str(failed)
    with pytest.raises(RuntimeError):
        asyncio.run(teach.turn(store, "p", s["id"], "other", "explain", "不能跳过失败请求"))
    done = asyncio.run(teach.turn(store, "p", s["id"], "req", "explain", "二元与一次都有条件。"))
    assert roles == ["reviewer", "student", "student"]
    assert sum(m["role"] == "human" for m in done["messages"]) == 1
    assert (
        asyncio.run(teach.turn(store, "p", s["id"], "req", "explain", "二元与一次都有条件。"))[
            "messages"
        ]
        == done["messages"]
    )
    with pytest.raises(ValueError):
        asyncio.run(teach.turn(store, "p", s["id"], "req", "explain", "different"))


def test_help_request_is_not_help_delivery_until_success(store, monkeypatch, model):
    s = teach.create(store, "p", "k")
    responder = teach.model_reply

    async def fail(role, payload):
        raise RuntimeError("provider down")

    monkeypatch.setattr(teach, "model_reply", fail)
    with pytest.raises(teach.ModelUnavailable):
        asyncio.run(teach.turn(store, "p", s["id"], "h", "hint", "给点提示"))
    assert not teach.load(store, "p", s["id"])["help_delivered"]
    monkeypatch.setattr(teach, "model_reply", responder)
    done = asyncio.run(teach.turn(store, "p", s["id"], "h", "hint", "给点提示"))
    assert done["help_delivered"] and done["messages"][-1]["role"] == "tutor"
    done = asyncio.run(teach.turn(store, "p", s["id"], "a", "explain", "xy的次数是2。"))
    assert next(m for m in done["messages"] if m["role"] == "human")["assisted"]
    assert teach.control(store, "p", s["id"], "finish")["report"]["assisted"]


def test_wrong_explanation_can_trigger_tutor_clarification_not_unconditional_agreement(
    store, monkeypatch
):
    async def respond(role, payload):
        return (
            review(payload, correction=True)
            if role == "reviewer"
            else {"message": "为什么xy的次数不是1呢？"}
        ), "test"

    monkeypatch.setattr(teach, "model_reply", respond)
    s = teach.create(store, "p", "k", persona="confused")
    result = asyncio.run(
        teach.turn(store, "p", s["id"], "a", "explain", "xy也是一次，因为每个字母都是一次。")
    )
    assert [m["role"] for m in result["messages"]][-2:] == ["tutor", "ai_student"]
    assert result["help_delivered"]
    assert (
        teach.control(store, "p", s["id"], "finish")["report"]["claims"][0]["status"]
        == "needs_clarification"
    )


def test_invalid_ai_citation_is_rejected_and_preserves_human_text(store, monkeypatch):
    s = teach.create(store, "p", "k")

    async def respond(role, payload):
        r = review(payload)
        r["claims"][0]["human_turn_ids"] = [s["messages"][0]["id"]]
        return r, "test"

    monkeypatch.setattr(teach, "model_reply", respond)
    with pytest.raises(teach.ModelUnavailable):
        asyncio.run(teach.turn(store, "p", s["id"], "a", "explain", "我的原话"))
    result = teach.load(store, "p", s["id"])
    assert len(result["messages"]) == 2 and result["messages"][-1]["text"] == "我的原话"
    with pytest.raises(ValueError):
        teach.control(store, "p", s["id"], "finish")


def test_concurrent_turn_and_pause_revoke_late_model_result(store, monkeypatch):
    s = teach.create(store, "p", "k")

    async def run():
        started, released = asyncio.Event(), asyncio.Event()

        async def respond(role, payload):
            started.set()
            await released.wait()
            return review(payload), "test"

        monkeypatch.setattr(teach, "model_reply", respond)
        task = asyncio.create_task(teach.turn(store, "p", s["id"], "a", "explain", "我的解释"))
        await started.wait()
        with pytest.raises(RuntimeError):
            await teach.turn(store, "p", s["id"], "b", "explain", "重复请求")
        teach.control(store, "p", s["id"], "pause")
        released.set()
        result = await task
        assert result["state"] == "paused" and len(result["messages"]) == 2
        teach.control(store, "p", s["id"], "resume")
        assert teach.load(store, "p", s["id"])["latest_request"]["retryable"]

    asyncio.run(run())


def test_changed_objective_rejects_late_generation_and_marks_stale(store, monkeypatch):
    s = teach.create(store, "p", "k")

    async def respond(role, payload):
        def change(tx):
            tx.progress.modules[0].knowledge_points[0].name = "changed"
            tx.emit("test.change", {})

        store.mutate("p", change)
        return review(payload), "test"

    monkeypatch.setattr(teach, "model_reply", respond)
    with pytest.raises(teach.ModelUnavailable):
        asyncio.run(teach.turn(store, "p", s["id"], "a", "explain", "解释"))
    result = teach.load(store, "p", s["id"])
    assert result["stale"] and len(result["messages"]) == 2
    with pytest.raises(ValueError):
        asyncio.run(teach.turn(store, "p", s["id"], "a", "explain", "解释"))


def test_owner_isolation_unknown_objective_and_creation_retry(store, tmp_path):
    s = teach.create(store, "p", "k", request_id="create")
    assert teach.create(store, "p", "k", request_id="create")["id"] == s["id"]
    with pytest.raises(ValueError):
        teach.create(store, "p", "k", persona="confused", request_id="create")
    with pytest.raises(ValueError):
        teach.create(store, "p", "missing")
    other = LearningStore(tmp_path / "other")
    other.save(LearningProgress(book_id="p"))
    with pytest.raises(LookupError):
        teach.load(other, "p", s["id"])
    assert teach.listing(other, "p") == []
    with pytest.raises(LookupError):
        teach.control(other, "p", s["id"], "pause")
    with pytest.raises(ValueError):
        teach.control(store, "p", s["id"], "finish")


def test_teaching_does_not_replace_pending_native_check(store, model):
    q = PendingQuestion(
        question_id="pending",
        knowledge_point_id="k",
        module_id="m",
        prompt="native question",
        expected_answer="a",
    )

    def pending(tx):
        tx.progress.pending_question = q
        tx.emit("test.pending", {})

    store.mutate("p", pending)
    s = teach.create(store, "p", "k")
    asyncio.run(teach.turn(store, "p", s["id"], "a", "explain", "这是我的解释"))
    assert store.load("p").pending_question.question_id == "pending"


def test_structured_model_call_retries_once_with_reasoning_budget_recovery(monkeypatch):
    from deeptutor.services.llm.config import LLMConfig
    from deeptutor.services.llm.reasoning_params import RETRY_REASONING_EFFORT

    config = LLMConfig(model="test", api_key="", reasoning_effort="high")
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: config)
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        return "" if len(calls) == 1 else '{"message":"能给个例子吗？"}'

    monkeypatch.setattr("deeptutor.services.llm.factory.complete_with_config", complete)
    raw, model = asyncio.run(teach.model_reply("student", {"objective": "方程"}))
    assert raw == {"message": "能给个例子吗？"} and model == "test"
    assert len(calls) == 2 and calls[1]["config"].reasoning_effort == RETRY_REASONING_EFFORT
    assert config.reasoning_effort == "high" and calls[0]["max_tokens"] >= 4096


def test_diagnostic_catalogue_exposes_teaching_only_as_non_training_activity(store, model):
    from deeptutor.learning.diagnostic_datasets import catalogue

    session = teach.create(store, "p", "k")
    asyncio.run(
        teach.turn(store, "p", session["id"], "a", "explain", "两个未知数且含未知数项的次数为1。")
    )
    teach.control(store, "p", session["id"], "finish")
    data = catalogue(store, "p")
    assert data["rows"] == [] and data["quality"]["eligible"] == 0
    assert data["teaching_activity"][0]["id"] == session["id"]
    assert data["teaching_activity"][0]["human_turn_count"] == 1


def test_teaching_report_roundtrip_in_postgres_without_writing_source_sqlite(store, model):
    from pathlib import Path
    import sqlite3
    from unittest.mock import patch

    from psycopg import sql

    from deeptutor.services.storage.postgres_adapter import connect_raw, schema_name
    from deeptutor.services.storage.postgres_migration import migrate

    migrate(store.db_path)
    try:
        with patch(
            "deeptutor.services.storage.postgres_adapter.enabled",
            side_effect=lambda p: Path(p).resolve() == store.db_path.resolve(),
        ):
            pg = LearningStore(store.db_path.parent)
            session = teach.create(pg, "p", "k")
            asyncio.run(teach.turn(pg, "p", session["id"], "a", "explain", "二元且一次。"))
            report = teach.control(pg, "p", session["id"], "finish")["report"]
            restored = teach.load(LearningStore(store.db_path.parent), "p", session["id"])
            assert restored["report"] == report and restored["state"] == "finished"
        with sqlite3.connect(store.db_path) as original:
            import json

            assert (
                json.loads(original.execute("SELECT state_json FROM mastery_paths").fetchone()[0])[
                    "teach_ai_sessions"
                ]
                == {}
            )
    finally:
        with connect_raw() as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name(store.db_path)))
            )


def test_each_claim_must_reference_current_human_not_only_a_past_explanation(
    store, model, monkeypatch
):
    session = teach.create(store, "p", "k")
    first = asyncio.run(teach.turn(store, "p", session["id"], "first", "explain", "先前讲解"))
    previous = next(m["id"] for m in first["messages"] if m["role"] == "human")

    async def respond(role, payload):
        raw = review(payload)
        raw["claims"].append({**raw["claims"][0], "human_turn_ids": [previous]})
        return raw, "test"

    monkeypatch.setattr(teach, "model_reply", respond)
    with pytest.raises(teach.ModelUnavailable):
        asyncio.run(teach.turn(store, "p", session["id"], "second", "explain", "已修正的讲解"))
    assert teach.load(store, "p", session["id"])["latest_request"]["retryable"]


def test_expired_generation_after_reopening_resumes_without_duplicate_explanation(store, model):
    session = teach.create(store, "p", "k")
    teach._reserve(store, "p", session["id"], "request", "explain", "重启前已保存的讲解")

    def expire(tx):
        tx.progress.teach_ai_sessions[session["id"]]["lease"] = 0
        tx.emit("test.restart", {})

    store.mutate("p", expire)
    restored = LearningStore(store.db_path.parent)
    assert teach.load(restored, "p", session["id"])["latest_request"]["retryable"]
    done = asyncio.run(
        teach.turn(restored, "p", session["id"], "request", "explain", "重启前已保存的讲解")
    )
    assert done["latest_request"]["state"] == "done"
    assert sum(m["role"] == "human" for m in done["messages"]) == 1
