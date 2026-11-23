import copy

import pytest

from deeptutor.learning.diagnosis import _digest, assessment_context
from deeptutor.learning.diagnostic_datasets import catalogue, create_snapshot, snapshots
from deeptutor.learning.models import (
    KnowledgePoint,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    PendingQuestion,
)
from deeptutor.learning.storage import LearningStore


def fixture(store, *, data_origin="learner"):
    p = LearningProgress(
        book_id="p",
        name="方程",
        data_origin=data_origin,
        wenjin_enabled=True,
        modules=[
            LearningModule(
                id="m",
                name="方程",
                order=0,
                knowledge_points=[
                    KnowledgePoint(id="k", name="代入", type="procedure", module_id="m")
                ],
            )
        ],
    )
    q = PendingQuestion(question_id="q", knowledge_point_id="k", prompt="2x=4", expected_answer="2")
    p.learning_evidence = [
        LearningEvidence(
            evidence_id="e",
            question_id="q",
            knowledge_point_id="k",
            learner_answer="2",
            result="correct",
            session_id="s",
            turn_id="t",
            diagnostic_context=assessment_context(p, q, "2", "short"),
        )
    ]
    return store.save(p)


def test_immutable_snapshot_and_export_provenance(tmp_path):
    st = LearningStore(tmp_path)
    fixture(st)
    data = catalogue(st, "p")
    assert data["quality"]["eligible"] == 1
    saved = create_snapshot(st, "p", data["source_revision"])
    original = copy.deepcopy(saved)
    p = st.load("p")
    p.learning_evidence[0].result = "incorrect"
    st.save(p)
    assert snapshots(st, "p", saved["id"]) == original
    assert saved["rows"][0]["correct"] == 1
    assert saved["provenance"][0]["turn_id"] == "t"
    digest = saved.pop("digest")
    assert digest == _digest(saved)
    assert len(snapshots(st, "p")) == 1


def test_stale_and_manual_and_synthetic_exclusions(tmp_path):
    st = LearningStore(tmp_path)
    fixture(st)
    rev = st.load("p").version
    with pytest.raises(RuntimeError):
        create_snapshot(st, "p", rev - 1)
    with pytest.raises(ValueError):
        create_snapshot(st, "p", rev, ["unknown"])
    with pytest.raises(ValueError):
        create_snapshot(st, "p", rev, ["receipt:e"])
    with pytest.raises(ValueError):
        create_snapshot(st, "p", rev, synthetic=True)
    assert snapshots(st, "p") == []


@pytest.mark.parametrize("kind", ["assisted", "semantic", "source", "simulation", "scope"])
def test_ineligible_training_data(tmp_path, kind):
    st = LearningStore(tmp_path)
    fixture(st, data_origin="synthetic" if kind == "simulation" else "learner")
    p = st.load("p")
    e = p.learning_evidence[0]
    if kind == "assisted":
        e.diagnostic_context.assistance = "assisted"
    elif kind == "semantic":
        e.diagnostic_context.grading_method = "semantic"
    elif kind == "source":
        e.session_id = ""
    elif kind == "simulation":
        p.name = "正常名称也不会洗掉模拟来源"
    else:
        e.diagnostic_context.objective_scope = "changed"
    st.save(p)
    assert catalogue(st, "p")["quality"]["eligible"] == 0


def test_snapshot_is_project_scoped(tmp_path):
    st = LearningStore(tmp_path)
    fixture(st)
    saved = create_snapshot(st, "p", st.load("p").version)
    st.save(LearningProgress(book_id="other"))
    with pytest.raises(LookupError):
        snapshots(st, "other", saved["id"])


@pytest.mark.asyncio
async def test_source_resolves_exact_turn_and_hides_nonpublic_messages(tmp_path, monkeypatch):
    from deeptutor.api.routers import mastery_path, sessions
    from deeptutor.services import session

    st = LearningStore(tmp_path)
    fixture(st)
    monkeypatch.setattr(mastery_path, "LearningStore", lambda: st)

    async def get_session(sid):
        assert sid == "s"
        return {
            "title": "来源对话",
            "messages": [
                {"id": 1, "role": "user", "content": "2", "metadata": {"turn_id": "t"}},
                {"id": 2, "role": "assistant", "content": "正确"},
                {"id": 3, "role": "tool", "content": "private"},
                {"id": 4, "role": "assistant", "content": "另一轮"},
            ],
        }

    class SessionStore:
        async def get_turn(self, tid):
            return {"session_id": "s", "assistant_message_id": 2}

    monkeypatch.setattr(sessions, "get_session", get_session)
    monkeypatch.setattr(session, "get_session_store", lambda: SessionStore())
    result = await mastery_path.diagnostic_source("p", "receipt:e")
    assert result["located"]
    assert [m["id"] for m in result["messages"] if m["matched"]] == [1, 2]
    assert all(m["role"] != "tool" for m in result["messages"])


@pytest.mark.asyncio
async def test_source_preserves_session_access_denial(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from deeptutor.api.routers import mastery_path, sessions

    st = LearningStore(tmp_path)
    fixture(st)
    monkeypatch.setattr(mastery_path, "LearningStore", lambda: st)

    async def denied(sid):
        raise HTTPException(status_code=404, detail="Session not found")

    monkeypatch.setattr(sessions, "get_session", denied)
    with pytest.raises(HTTPException) as result:
        await mastery_path.diagnostic_source("p", "receipt:e")
    assert result.value.status_code == 404


def test_zero_sample_training_check_does_not_export_or_invent_samples(tmp_path):
    store = LearningStore(tmp_path)
    store.save(LearningProgress(book_id="empty", name="验收空记录"))
    revision = store.load("empty").version
    with pytest.raises(ValueError, match="没有可纳入"):
        create_snapshot(store, "empty", revision)
    snapshot = create_snapshot(store, "empty", revision, allow_empty=True)
    assert snapshot["rows"] == []
    assert snapshot["quality"]["eligible"] == 0
    assert snapshots(store, "empty", snapshot["id"])["rows"] == []


def test_project_rename_does_not_change_training_eligibility(tmp_path):
    st = LearningStore(tmp_path)
    fixture(st)
    before = catalogue(st, "p")["quality"]["eligible"]
    p = st.load("p")
    p.name = "模拟考试知识学习"
    st.save(p)
    assert catalogue(st, "p")["quality"]["eligible"] == before == 1


def test_synthetic_origin_cannot_be_laundered_by_rename_or_save(tmp_path):
    st = LearningStore(tmp_path)
    p = LearningProgress(book_id="synthetic", name="演示", data_origin="synthetic")
    st.save(p)
    p = st.load("synthetic")
    p.name = "正常项目"
    p.data_origin = "learner"
    st.save(p)
    assert st.load("synthetic").data_origin == "synthetic"


def test_legacy_origin_stays_unknown_after_rename_and_transaction(tmp_path):
    import json

    st = LearningStore(tmp_path)
    fixture(st)
    with st._connect() as c:
        raw = json.loads(
            c.execute("SELECT state_json FROM mastery_paths WHERE path_id='p'").fetchone()[0]
        )
        raw.pop("data_origin")
        for e in raw.get("learning_evidence", []):
            e.pop("data_origin", None)
        c.execute("UPDATE mastery_paths SET state_json=? WHERE path_id='p'", (json.dumps(raw),))
    p = st.load("p")
    assert p.data_origin == "legacy_unknown"
    p.name = "renamed"
    st.save(p)

    def change(tx):
        tx.progress.data_origin = "learner"
        tx.emit("test.changed", {})

    st.mutate("p", change)
    assert st.load("p").data_origin == "legacy_unknown"
    assert catalogue(st, "p")["quality"]["eligible"] == 0


def test_new_evidence_in_legacy_project_can_train_without_reclassifying_old_data(tmp_path):
    from copy import deepcopy
    import json

    st = LearningStore(tmp_path)
    fixture(st)
    with st._connect() as c:
        raw = json.loads(
            c.execute("SELECT state_json FROM mastery_paths WHERE path_id='p'").fetchone()[0]
        )
        raw.pop("data_origin")
        raw["learning_evidence"][0].pop("data_origin")
        c.execute("UPDATE mastery_paths SET state_json=? WHERE path_id='p'", (json.dumps(raw),))
    p = st.load("p")
    assert p.data_origin == p.learning_evidence[0].data_origin == "legacy_unknown"
    fresh = deepcopy(p.learning_evidence[0])
    fresh.evidence_id, fresh.question_id = "fresh", "fresh-q"
    fresh.data_origin = "learner"
    p.learning_evidence.append(fresh)
    st.save(p)
    data = catalogue(st, "p")
    assert (
        next(r for r in data["rows"] if r["observation_id"] == "receipt:e")["eligibility"]
        == "excluded"
    )
    assert (
        next(r for r in data["rows"] if r["observation_id"] == "receipt:fresh")["eligibility"]
        == "eligible"
    )
    p = st.load("p")
    p.learning_evidence[0].data_origin = "learner"
    st.save(p)
    assert st.load("p").learning_evidence[0].data_origin == "legacy_unknown"
