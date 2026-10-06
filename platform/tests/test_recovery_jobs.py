import pytest
from app.learning.recovery import RecoveryJobs
from app.core.schema import GoalContract,GoalStatement
from tests.test_learning_storage import service,evidence,NOW


def workspace():
    s=service(); s.store.save_contract(GoalContract(learner_id="s1",goal_statement=GoalStatement(text="自主解题")))
    s.consume(evidence("original"))
    s.consume(evidence("corrected",attempt_id="original",supersedes="original",verdict_status="failed",score=0))
    return s,RecoveryJobs(s.store)


def test_appeal_has_owner_and_content_idempotency():
    s,j=workspace(); a=j.appeal("s1","appeal","original","请复核")
    assert j.appeal("s1","appeal","original","请复核")==a
    with pytest.raises(ValueError): j.appeal("s1","appeal","original","不同原因")
    with pytest.raises(PermissionError): j.appeal("foreign","x","original","reason")


def test_job_retry_recovers_after_fault_without_duplicate_proposal():
    s,j=workspace(); job=j.enqueue("s1","corrected",as_of=NOW)
    def fail(stage):
        if stage=="proposal": raise RuntimeError("fault")
    with pytest.raises(RuntimeError): j.run("s1",job["job_id"],{"MATH.G7.EQ.SOLVE":[]},fail)
    assert j.get("s1",job["job_id"])["status"]=="failed"
    assert s.store.conn.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0]==0
    result=RecoveryJobs(s.store).run("s1",job["job_id"],{"MATH.G7.EQ.SOLVE":[]})
    assert result["status"]=="completed" and result["proposed_plan"]["status"]=="proposed"
    assert result["state_comparison"]["before"]!=result["state_comparison"]["after"]
    assert j.run("s1",job["job_id"],{})==result
    assert s.store.conn.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0]==1


def test_outbox_is_atomic_and_recovers_without_browser_and_chained_corrections():
    s=service(); s.consume(evidence("original")); jobs=RecoveryJobs(s.store)
    corrected=evidence("first",attempt_id="original",supersedes="original",verdict_status="failed")
    def fail(stage):
        if stage=="recovery_queue": raise RuntimeError("fault")
    with pytest.raises(RuntimeError): s.consume(corrected,fault=fail)
    assert jobs.pending("s1")==[] and len(s.evidences("s1"))==1
    s.consume(corrected)
    assert len(jobs.pending("s1"))==1
    s.consume(evidence("second",attempt_id="original",supersedes="first"))
    assert len(jobs.reconcile("s1"))==2
    first=jobs.pending("s1")[0]
    assert jobs.run("s1",first["job_id"],{"MATH.G7.EQ.SOLVE":[]})["status"]=="completed"
    with pytest.raises(ValueError): jobs.enqueue("s1","first",as_of=NOW.replace(year=2025))


def test_job_rollback_does_not_persist_dangling_plan_or_double_attempt():
    s,j=workspace(); job=j.pending("s1")[0]
    def fail(stage):
        if stage=="job": raise RuntimeError("fault")
    with pytest.raises(RuntimeError): j.run("s1",job["job_id"],{"MATH.G7.EQ.SOLVE":[]},fail)
    failed=j.get("s1",job["job_id"])
    assert failed["attempts"]==1 and "proposed_plan" not in failed
    assert s.store.conn.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0]==0


def test_separate_store_concurrent_jobs_create_one_plan(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.storage.db import Store
    path=str(tmp_path/"jobs.sqlite")
    first=Store(path); second=Store(path)
    s=service(first)
    first.save_contract(GoalContract(learner_id="s1",goal_statement=GoalStatement(text="自主解题")))
    s.consume(evidence("original"));s.consume(evidence("corrected",attempt_id="original",supersedes="original",verdict_status="failed"))
    jobs=[RecoveryJobs(first),RecoveryJobs(second)]
    job=jobs[0].pending("s1")[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda j:j.run("s1",job["job_id"],{"MATH.G7.EQ.SOLVE":[]}),jobs))
    assert results[0]==results[1]
    assert first.conn.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0]==1
    first.close();second.close()
