import asyncio
import uuid

from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from deeptutor.learning import classroom as cr
from deeptutor.learning.models import KnowledgePoint, LearningModule, LearningProgress
from deeptutor.learning.storage import LearningStore


@pytest.fixture
def room(tmp_path, monkeypatch):
    original = cr.connection
    schema = "classroom_test_" + uuid.uuid4().hex
    with original() as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect():
        c = original()
        c.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
        c.commit()
        return c

    monkeypatch.setattr(cr, "connection", connect)
    store = LearningStore(tmp_path / "learning")
    store.save(
        LearningProgress(
            book_id="p",
            name="课堂验收",
            modules=[
                LearningModule(
                    id="m",
                    name="方程",
                    order=0,
                    knowledge_points=[
                        KnowledgePoint(id="k", name="定义", type="memory", module_id="m")
                    ],
                )
            ],
        )
    )
    course = cr.create(store, "p", [], 10)
    yield store, course
    with original() as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def ready(store, cid):
    with cr.locked(store, "p", cid) as (c, d):
        d.update(
            state="ready",
            outline=[{"title": "测验", "objective_id": "k", "goal": "判断", "kind": "quiz"}],
            pages=[
                {
                    "id": "scene-0",
                    "title": "判断",
                    "objective_id": "k",
                    "kind": "quiz",
                    "goal": "判断",
                    "content": "检查定义",
                    "question": "哪个正确",
                    "options": ["甲", "乙"],
                    "answer_index": 1,
                    "explanation": "乙符合条件",
                    "steps": [],
                    "narration": "",
                    "source_ids": [],
                }
            ],
        )
        cr.put(c, d)


def sections():
    return [
        cr.Section(title=kind, objective_id="k", goal="理解", kind=kind)
        for kind in ("slide", "whiteboard", "quiz")
    ]


def test_outline_confirmation_and_owner_project_boundary(room):
    store, course = room
    with pytest.raises(ValueError):
        cr.confirm(store, "p", course["id"], sections())
    with cr.locked(store, "p", course["id"]) as (c, d):
        d["state"] = "awaiting_confirmation"
        cr.put(c, d)
    with pytest.raises(ValueError):
        cr.confirm(
            store, "p", course["id"], [cr.Section(title="页", objective_id="other", goal="理解")]
        )
    result = cr.confirm(store, "p", course["id"], sections())
    assert result["state"] == "queued_pages"
    other = LearningStore(store.db_path.parent / "other")
    other.save(LearningProgress(book_id="p"))
    with pytest.raises(LookupError):
        cr.load(other, "p", course["id"])


def test_hidden_answer_idempotent_assisted_evidence_and_resume(room):
    store, course = room
    cid = course["id"]
    ready(store, cid)
    result = cr.load(store, "p", cid)
    assert "answer_index" not in result["pages"][0] and "explanation" not in result["pages"][0]
    assert "input" not in result and "owner" not in result
    cr.answer(store, "p", cid, 0, 1)
    cr.answer(store, "p", cid, 0, 0)
    p = store.load("p")
    assert len(p.learning_evidence) == 1
    e = p.learning_evidence[0]
    assert e.result == "correct" and e.diagnostic_context.assistance == "assisted"
    assert not p.pending_question
    assert cr.load(store, "p", cid)["answers"]["scene-0"]["choice"] == 1
    from deeptutor.learning.diagnostic_datasets import catalogue

    assert catalogue(store, "p")["quality"]["eligible"] == 0
    assert catalogue(store, "p")["rows"][0]["classroom_origin"]["classroom_id"] == cid
    assert cr.control(store, "p", cid, "finish")["ended_at"] > 0


def test_cancel_revokes_worker_token(room):
    store, course = room
    with cr.locked(store, "p", course["id"]) as (c, d):
        d.update(state="generating", token="old")
        cr.put(c, d)
    cr.control(store, "p", course["id"], "cancel")
    with cr.locked(store, "p", course["id"]) as (_, d):
        assert d["token"] is None


def test_worker_stops_for_confirmation_and_resumes_pages(room, monkeypatch):
    store, course = room

    def fake(d):
        if not d["outline"]:
            return {"sections": [s.model_dump() for s in sections()]}
        return {
            "title": "定义",
            "content": "两个未知数",
            "steps": [],
            "narration": "讲解",
            "question": "",
            "options": [],
            "answer_index": None,
            "explanation": "",
            "source_ids": [],
        }

    monkeypatch.setattr(cr, "execute", fake)

    async def run_until(state):
        stop = asyncio.Event()
        task = asyncio.create_task(cr.worker(stop))
        try:
            for _ in range(40):
                await asyncio.sleep(0.05)
                if cr.load(store, "p", course["id"])["state"] == state:
                    return
            pytest.fail("worker did not reach expected checkpoint")
        finally:
            stop.set()
            await task

    asyncio.run(run_until("awaiting_confirmation"))
    assert cr.load(store, "p", course["id"])["pages"] == []
    cr.confirm(store, "p", course["id"], sections())
    asyncio.run(run_until("ready"))
    assert len(cr.load(store, "p", course["id"])["pages"]) == 3


def test_cancel_resume_preserves_confirmation_gate(room):
    store, c = room
    cid = c["id"]
    cr.control(store, "p", cid, "cancel")
    assert cr.control(store, "p", cid, "resume")["state"] == "queued_outline"
    with cr.locked(store, "p", cid) as (conn, d):
        d.update(state="awaiting_confirmation", outline=[s.model_dump() for s in sections()])
        cr.put(conn, d)
    cr.control(store, "p", cid, "cancel")
    assert cr.control(store, "p", cid, "resume")["state"] == "awaiting_confirmation"
    cr.confirm(store, "p", cid, sections())
    cr.control(store, "p", cid, "cancel")
    assert cr.control(store, "p", cid, "resume")["state"] == "queued_pages"


def test_skipped_question_not_counted_as_answered_or_complete(room):
    store, c = room
    ready(store, c["id"])
    cr.control(store, "p", c["id"], "view", 0)
    cr.control(store, "p", c["id"], "skip")
    result = cr.control(store, "p", c["id"], "finish")
    assert result["summary"]["answered"] == 0 and result["summary"]["skipped"] == 1
    assert not result["summary"]["complete"] and "completed_at" not in result


def test_whiteboard_checkpoint_survives_reload(room):
    store, c = room
    cid = c["id"]
    ready(store, cid)
    with cr.locked(store, "p", cid) as (conn, d):
        d["pages"][0].update(kind="whiteboard", steps=["一步", "二步", "三步"])
        cr.put(conn, d)
    cr.control(store, "p", cid, "step", 1)
    assert cr.load(store, "p", cid)["whiteboard_steps"]["scene-0"] == 1
    assert cr.load(store, "p", cid)["summary"]["viewed"] == 0
    cr.control(store, "p", cid, "step", 2)
    assert cr.load(store, "p", cid)["summary"]["viewed"] == 1


def test_coverage_validation_and_followup_context(room, monkeypatch):
    store, c = room
    cid = c["id"]
    ready(store, cid)
    with pytest.raises(ValueError):
        cr.validate_outline(cr.Outline(sections=sections()[:1]))
    captures = []

    async def fake(self, **kw):
        import json

        captures.append(json.loads(kw["prompt"]))
        return "有界解释"

    monkeypatch.setattr("deeptutor.services.llm.client.LLMClient.complete", fake)
    asyncio.run(cr.ask(store, "p", cid, 0, "解释定义", "first"))
    asyncio.run(cr.ask(store, "p", cid, 0, "刚才的再举个例子", "second"))
    assert captures[-1]["history"][0]["response"] == "有界解释"
    assert "answer_index" not in captures[-1]["page"]
    asyncio.run(cr.ask(store, "p", cid, 0, "刚才的再举个例子", "second"))
    assert len(captures) == 2


def test_whiteboard_generation_rejects_empty_steps(room, monkeypatch):
    import json

    store, c = room
    with cr.locked(store, "p", c["id"]) as (_, d):
        d["outline"] = [s.model_dump() for s in sections() if s.kind == "whiteboard"]

    async def fake(self, **kw):
        return json.dumps({"title": "白板", "content": "定义", "steps": []})

    monkeypatch.setattr("deeptutor.services.llm.client.LLMClient.complete", fake)
    with pytest.raises(ValueError, match="白板缺少有效步骤"):
        asyncio.run(cr.generate(d))


def test_concurrent_duplicate_question_calls_model_once(room, monkeypatch):
    store, c = room
    ready(store, c["id"])
    calls = []

    async def fake(self, **kw):
        calls.append(kw)
        await asyncio.sleep(0.1)
        return "解释"

    monkeypatch.setattr("deeptutor.services.llm.client.LLMClient.complete", fake)

    async def run():
        a = asyncio.create_task(cr.ask(store, "p", c["id"], 0, "同一问题", "same"))
        await asyncio.sleep(0.02)
        with pytest.raises(RuntimeError, match="正在回答"):
            await cr.ask(store, "p", c["id"], 0, "同一问题", "same")
        await a

    asyncio.run(run())
    assert len(calls) == 1


def test_delete_revokes_assets_preserves_project_and_allows_fresh_generation(room):
    from deeptutor.learning import classroom_media as media

    store, _ = room
    course = cr.create(store, "p", [], 15, True)
    cid = course["id"]
    before = store.load("p").model_dump()
    with cr.locked(store, "p", cid) as (c, d):
        d.update(state="generating", token="stale-worker", lease=9999999999)
        cr.put(c, d)
        media.initialize(c)
        c.execute(
            "INSERT INTO wenjin_classroom_assets VALUES(%s,%s,%s,%s,%s,%s)",
            (
                cr.scope(store),
                "p",
                cid,
                "asset",
                Jsonb({"state": "running", "token": "stale-media", "lease": 9999999999}),
                0,
            ),
        )
    other = LearningStore(store.db_path.parent / "another-owner")
    other.save(LearningProgress(book_id="p"))
    with pytest.raises(LookupError):
        cr.remove(other, "p", cid)
    assert cr.remove(store, "p", cid) == {"id": cid, "deleted": True}
    assert all(x["id"] != cid for x in cr.listing(store, "p"))
    with pytest.raises(LookupError):
        cr.load(store, "p", cid)
    with pytest.raises(LookupError):
        cr.control(store, "p", cid, "resume")
    with cr.connection() as c:
        deleted = c.execute("SELECT body FROM wenjin_classrooms WHERE id=%s", (cid,)).fetchone()[0]
        asset = c.execute("SELECT body FROM wenjin_classroom_assets WHERE key='asset'").fetchone()[
            0
        ]
    assert deleted["state"] == "deleted" and deleted["token"] is None
    assert asset["state"] == "cancelled" and asset["token"] is None
    assert store.load("p").model_dump() == before
    replacement = cr.create(store, "p", [], 15, True)
    assert replacement["id"] != cid and replacement["pages"] == []
    assert replacement["curriculum"] == course["curriculum"]


def test_late_worker_cannot_restore_deleted_classroom(room, monkeypatch):
    import threading

    store, course = room
    started, released = threading.Event(), threading.Event()

    def execute(d):
        started.set()
        assert released.wait(5)
        return {"sections": [s.model_dump() for s in sections()]}

    monkeypatch.setattr(cr, "execute", execute)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(cr.worker(stop))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            await asyncio.to_thread(cr.remove, store, "p", course["id"])
        finally:
            stop.set()
            released.set()
            await asyncio.wait_for(task, 5)

    asyncio.run(run())
    with pytest.raises(LookupError):
        cr.load(store, "p", course["id"])
    assert cr.listing(store, "p") == []


def test_failed_worker_reports_stopped_not_still_retrying(room, monkeypatch):
    store, course = room

    async def run():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        calls = []

        def execute(d):
            calls.append(d["attempts"])
            if len(calls) == 2:
                loop.call_soon_threadsafe(stop.set)
            raise ValueError("页面文字超出版式，请分成更多页")

        monkeypatch.setattr(cr, "execute", execute)
        await asyncio.wait_for(cr.worker(stop), 10)
        return calls

    assert asyncio.run(run()) == [1, 2]
    result = cr.load(store, "p", course["id"])
    assert result["state"] == "failed"
    assert "自动重试已结束" in result["error"] and "正在" not in result["error"]
    assert result["generation_issue"]["retrying"] is False
    assert result["retry_feedback"]


def test_answer_write_failure_does_not_leave_orphan_evidence(room, monkeypatch):
    store, course = room
    cid = course["id"]
    ready(store, cid)
    original = cr.put
    monkeypatch.setattr(
        cr, "put", lambda *args: (_ for _ in ()).throw(RuntimeError("disk failure"))
    )
    with pytest.raises(RuntimeError, match="disk failure"):
        cr.answer(store, "p", cid, 0, 0)
    assert store.load("p").learning_evidence == []
    monkeypatch.setattr(cr, "put", original)
    result = cr.answer(store, "p", cid, 0, 1)
    assert result["answers"]["scene-0"]["correct"]
    assert store.load("p").learning_evidence[0].result == "correct"


def test_answer_receipt_survives_evidence_failure_and_retry_keeps_first_choice(room, monkeypatch):
    store, course = room
    cid = course["id"]
    ready(store, cid)
    original = store.mutate
    monkeypatch.setattr(
        store,
        "mutate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("evidence unavailable")),
    )
    with pytest.raises(RuntimeError, match="evidence unavailable"):
        cr.answer(store, "p", cid, 0, 0)
    with cr.locked(store, "p", cid) as (_, d):
        assert d["answers"]["scene-0"]["choice"] == 0
    monkeypatch.setattr(store, "mutate", original)
    result = cr.answer(store, "p", cid, 0, 1)
    assert result["answers"]["scene-0"]["choice"] == 0
    evidence = store.load("p").learning_evidence
    assert len(evidence) == 1 and evidence[0].result == "incorrect"
    assert result["answers"]["scene-0"]["evidence_status"] == "recorded"


def test_answer_ack_failure_replays_one_receipt_on_reload(room, monkeypatch):
    store, course = room
    cid = course["id"]
    ready(store, cid)
    original = cr.put
    calls = 0

    def fail_ack(c, d):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("ack failure")
        return original(c, d)

    monkeypatch.setattr(cr, "put", fail_ack)
    with pytest.raises(RuntimeError, match="ack failure"):
        cr.answer(store, "p", cid, 0, 0)
    assert len(store.load("p").learning_evidence) == 1
    monkeypatch.setattr(cr, "put", original)
    result = cr.load(store, "p", cid)
    assert result["answers"]["scene-0"]["evidence_status"] == "recorded"
    assert len(store.load("p").learning_evidence) == 1


@pytest.mark.asyncio
async def test_worker_recovers_durable_answer_without_browser_reload(room, monkeypatch):
    store, course = room
    cid = course["id"]
    ready(store, cid)
    original = store.mutate

    def unavailable(*args, **kwargs):
        raise RuntimeError("temporary projection failure")

    monkeypatch.setattr(store, "mutate", unavailable)
    with pytest.raises(RuntimeError):
        cr.answer(store, "p", cid, 0, 0)
    monkeypatch.setattr(store, "mutate", original)
    monkeypatch.setattr(
        cr, "recover_answers", lambda d: cr.flush_answers(store, d["project"], d["id"])
    )
    stop = asyncio.Event()
    task = asyncio.create_task(cr.worker(stop))
    try:
        for _ in range(60):
            if store.load("p").learning_evidence:
                break
            await asyncio.sleep(0.05)
        assert len(store.load("p").learning_evidence) == 1
        with cr.locked(store, "p", cid) as (_, d):
            assert not d["answer_outbox"]
            assert d["answers"]["scene-0"]["choice"] == 0
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)


def test_projection_retry_retains_answer_time_objective_snapshot(room, monkeypatch):
    store, course = room
    cid = course["id"]
    ready(store, cid)
    from deeptutor.learning import goal_contract as goals

    goals.confirm(
        store,
        "p",
        revision=store.load("p").version,
        level="understand",
        summary="Understand this lesson",
        request_id="classroom-goal-one",
    )
    original = store.mutate

    def unavailable(*args, **kwargs):
        raise RuntimeError("temporary evidence failure")

    monkeypatch.setattr(store, "mutate", unavailable)
    with pytest.raises(RuntimeError):
        cr.answer(store, "p", cid, 0, 1)
    monkeypatch.setattr(store, "mutate", original)
    goals.confirm(
        store,
        "p",
        revision=store.load("p").version,
        level="apply",
        summary="Apply this lesson",
        request_id="classroom-goal-two",
    )
    p = store.load("p")
    old_name = p.modules[0].knowledge_points[0].name
    p.modules[0].knowledge_points[0].name = "changed objective"
    store.save(p)
    monkeypatch.setattr(store, "mutate", original)
    cr.load(store, "p", cid)
    assert store.load("p").learning_evidence[0].diagnostic_context.objective_name == old_name

    assert store.load("p").learning_evidence[0].diagnostic_context.goal_version == 1
