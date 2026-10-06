from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.learning.service import LearningService, EvidenceConflict, ConsentDenied
from app.storage.db import Store
from app.storage.learning_repository import PostgresLearningRepository, SQLiteLearningRepository
from tests.test_learning_storage import NOW, evidence
from tests.test_postgres_archive import configured_dsn


@pytest.fixture(params=["sqlite", "postgres"])
def repository(request, tmp_path):
    if request.param == "sqlite":
        store = Store(str(tmp_path / "repository.sqlite3"))
        yield SQLiteLearningRepository(store)
        store.close()
    else:
        dsn = configured_dsn()
        if not dsn:
            pytest.skip("Real PostgreSQL learning repository requires configured test DSN")
        schema = "eduagent_repo_test_" + uuid4().hex
        repo = PostgresLearningRepository(dsn, schema=schema)
        repo.bootstrap()
        yield repo
        # Generated isolated test schema only; never touch configured public/user schemas.
        assert schema.startswith("eduagent_repo_test_") and len(schema) == 51
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def service(repository):
    result = LearningService.for_repository(repository)
    result.set_consent("s1", ["teaching"], "c1", "student:local", NOW)
    return result


def test_live_learning_ingestion_has_no_archive_or_sqlite_dependency(repository):
    learning = service(repository)
    result = learning.consume(evidence("first"))
    assert result["event_seq"] >= 1
    assert len(learning.evidences("s1")) == 1
    assert learning.masteries("s1")[0].state_version == 1
    assert learning.retentions("s1")[0].state_version == 1
    assert len(learning.transitions("s1")) == 2
    if repository.backend == "postgres":
        assert not hasattr(repository, "store")
        with repository.transaction("s1") as tx:
            assert tx._execute("SELECT version()").fetchone()[0].startswith("PostgreSQL 16")
            assert tx.latest_snapshot("s1")["knowledge_state"]["kc_masteries"]


def test_same_algorithms_as_legacy_sqlite_service(repository):
    baseline_store = Store()
    baseline = LearningService(baseline_store)
    baseline.set_consent("s1", ["teaching"], "c1", "student:local", NOW)
    learning = service(repository)
    try:
        inputs = [evidence("a"), evidence("b", occurred_at=NOW + timedelta(days=1), verdict_status="failed", score=0),
                  evidence("fix", supersedes="b", attempt_id="b", occurred_at=NOW + timedelta(days=1))]
        for row in inputs:
            learning.consume(row)
            baseline.consume(row)
        assert learning.masteries("s1") == baseline.masteries("s1")
        assert learning.retentions("s1") == baseline.retentions("s1")
        assert learning.rebuild("s1", as_of=NOW + timedelta(days=3)) == baseline.rebuild("s1", as_of=NOW + timedelta(days=3))
    finally:
        baseline_store.close()


@pytest.mark.parametrize("stage", ["evidence", "mastery", "retention", "transition", "snapshot", "receipt", "audit"])
def test_all_write_stages_rollback_without_partial_facts_state_or_receipt(repository, stage):
    learning = service(repository)
    def fault(actual):
        if actual == stage:
            raise RuntimeError("controlled-fault")
    with pytest.raises(RuntimeError, match="controlled-fault"):
        learning.consume(evidence("failed"), fault=fault, receipt=("key", "fingerprint", lambda result: result))
    assert learning.evidences("s1") == []
    assert learning.masteries("s1") == []
    assert learning.retentions("s1") == []
    assert learning.transitions("s1") == []
    assert learning.receipt("s1", "key", "fingerprint") is None
    with repository.transaction("s1") as tx:
        assert tx.latest_snapshot("s1") is None
    learning.consume(evidence("failed"))
    assert len(learning.evidences("s1")) == 1


def test_same_key_same_content_is_idempotent_and_conflict_does_not_overwrite(repository):
    learning = service(repository)
    row = evidence("fact")
    first = learning.consume(row, receipt=("key", "fp", lambda result: {"saved": result}))
    assert learning.consume(row) == first
    assert learning.receipt("s1", "key", "fp") == {"saved": first}
    with pytest.raises(EvidenceConflict):
        learning.consume(evidence("fact", score=0.2))
    with pytest.raises(EvidenceConflict):
        learning.receipt("s1", "key", "other")
    with pytest.raises(EvidenceConflict):
        learning.consume(evidence("second", attempt_id="fact"))
    assert learning.evidences("s1")[0].score == 1


def test_state_cas_conflict_rolls_back_evidence_and_retries_exactly_once(repository):
    learning = service(repository)
    learning.consume(evidence("initial"))
    with pytest.raises(EvidenceConflict, match="CAS"):
        learning.consume(evidence("second"), expected_versions={"MATH.G7.EQ.SOLVE:mastery": 0})
    assert len(learning.evidences("s1")) == 1
    learning.consume(evidence("second"), expected_versions={"MATH.G7.EQ.SOLVE:mastery": 1})
    assert learning.masteries("s1")[0].state_version == 2


def test_append_correction_outbox_atomic_and_legacy_schema_compatible(repository):
    learning = service(repository)
    learning.consume(evidence("original"))
    corrected = evidence("corrected", supersedes="original", attempt_id="original", score=0, verdict_status="failed")
    def fault(stage):
        if stage == "recovery_queue":
            raise RuntimeError("controlled-fault")
    with pytest.raises(RuntimeError):
        learning.consume(corrected, fault=fault)
    assert len(learning.evidences("s1")) == 1
    learning.consume(corrected)
    with repository.transaction("s1") as tx:
        jobs = tx._payloads("SELECT payload FROM correction_jobs WHERE learner_id=?", ("s1",))
        assert len(jobs) == 1
        assert jobs[0]["correction_id"] == "corrected"
        assert jobs[0]["source_ref"] == "original"
    assert learning.rebuild("s1", as_of=NOW)["evidence_refs"] == ["corrected"]
    with pytest.raises(EvidenceConflict, match="effective"):
        learning.consume(evidence("fork", supersedes="original", attempt_id="original"))


def test_cross_subject_correction_and_current_withdrawal_block_all_reads(repository):
    learning = service(repository)
    learning.consume(evidence("source"))
    learning.set_consent("s2", ["teaching"], "c1", "student:local", NOW)
    with pytest.raises(EvidenceConflict):
        learning.consume(evidence("wrong-owner", learner_id="s2", supersedes="source", attempt_id="source"))
    learning.set_consent("s1", [], "withdraw", "student:local", NOW)
    for action in (lambda: learning.evidences("s1"), lambda: learning.masteries("s1"),
                   lambda: learning.retentions("s1"), lambda: learning.consume(evidence("after"))):
        with pytest.raises(ConsentDenied):
            action()


def test_consent_version_is_immutable_and_collection_source_cannot_be_forged(repository):
    learning = service(repository)
    assert learning.set_consent("s1", ["teaching"], "c1", "student:local", NOW + timedelta(minutes=1))["at"] == NOW.isoformat()
    with pytest.raises(EvidenceConflict):
        learning.set_consent("s1", [], "c1", "student:local", NOW)
    with pytest.raises(ConsentDenied):
        learning.consume(evidence("forged", authorization_source="teacher:fake"))


def test_competing_connections_consume_one_fact_and_one_receipt(repository):
    learning = service(repository)
    def worker(number):
        if repository.backend == "sqlite":
            store = Store(repository.store.conn.execute("PRAGMA database_list").fetchone()[2])
            local = SQLiteLearningRepository(store)
        else:
            store = None
            local = PostgresLearningRepository(repository.dsn, schema=repository.schema)
        try:
            return LearningService.for_repository(local).consume(evidence("concurrent"))
        finally:
            if store:
                store.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(8)))
    assert all(row == results[0] for row in results)
    assert len(learning.evidences("s1")) == 1
    assert learning.masteries("s1")[0].state_version == 1


def test_sequence_can_have_gaps_but_never_duplicates_after_rollback(repository):
    learning = service(repository)
    def fault(stage):
        if stage == "evidence":
            raise RuntimeError("controlled-fault")
    with pytest.raises(RuntimeError):
        learning.consume(evidence("failed"), fault=fault)
    one = learning.consume(evidence("one"))
    two = learning.consume(evidence("two"))
    assert 0 < one["event_seq"] < two["event_seq"]


def test_repository_rejects_schema_identifier_and_non_allowlisted_table(repository):
    with pytest.raises(ValueError):
        PostgresLearningRepository("not-used", schema="public; DROP DATABASE postgres")
    with repository.transaction("s1") as tx:
        with pytest.raises(ValueError):
            tx.append("user_secrets", {"payload": {}})


def test_pg_bootstrap_is_idempotent_and_uses_real_versioned_tables(repository):
    if repository.backend == "postgres":
        repository.bootstrap()
        repository.bootstrap()
        with repository.transaction("fixture") as tx:
            assert tx._execute("SELECT version FROM repository_meta").fetchone()[0] == "learning-repository-v3.1"
            assert tx._execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='learning_evidence' AND column_name='payload'", (repository.schema,)).fetchone()[0] == "jsonb"


def test_research_purpose_filters_collection_scope_and_current_withdrawal(repository):
    learning = service(repository)
    learning.consume(evidence("teaching-only"))
    learning.set_consent("s1", ["teaching", "research"], "c2", "student:local", NOW)
    learning.consume(evidence("research-authorized", consent_scope=["teaching", "research"], consent_version="c2"))
    assert [row.evidence_id for row in learning.evidences("s1", "research")] == ["research-authorized"]
    learning.set_consent("s1", ["teaching"], "c3", "student:local", NOW)
    with pytest.raises(ConsentDenied):
        learning.evidences("s1", "research")


def test_privacy_quarantine_is_independent_of_fresh_consent(repository):
    learning = service(repository)
    if repository.backend == "postgres":
        with repository.transaction("s1") as tx:
            tx._execute("INSERT INTO governance_privacy VALUES (?, ?, CAST(? AS JSONB))", ("s1", "withdrawn", '{}'))
    else:
        with repository.store.lock:
            repository.store.conn.execute("CREATE TABLE governance_privacy(learner_id TEXT PRIMARY KEY,state TEXT,payload TEXT)")
            repository.store.conn.execute("INSERT INTO governance_privacy VALUES (?,?,?)", ("s1", "withdrawn", '{}'))
            repository.store.conn.commit()
    learning.set_consent("s1", ["teaching"], "fresh", "student:local", NOW)
    with pytest.raises(ConsentDenied):
        learning.consume(evidence("blocked", consent_version="fresh"))


def test_conflicting_concurrent_state_versions_have_one_winner(repository):
    learning = service(repository)
    learning.consume(evidence("initial"))
    def worker(index):
        if repository.backend == "sqlite":
            store = Store(repository.store.conn.execute("PRAGMA database_list").fetchone()[2])
            local = SQLiteLearningRepository(store)
        else:
            store = None
            local = PostgresLearningRepository(repository.dsn, schema=repository.schema)
        try:
            try:
                LearningService.for_repository(local).consume(evidence(f"competitor-{index}"),
                    expected_versions={"MATH.G7.EQ.SOLVE:mastery": 1})
                return "success"
            except EvidenceConflict:
                return "conflict"
        finally:
            if store:
                store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert sorted(results) == ["conflict", "success"]
    assert len(learning.evidences("s1")) == 2
    assert learning.masteries("s1")[0].state_version == 2
