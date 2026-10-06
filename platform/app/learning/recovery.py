"""Durable correction follow-up jobs. Recompute and propose; never silently sign."""
import hashlib
import json
from datetime import datetime

from app.learning.service import LearningService, EvidenceConflict, ConsentDenied
from app.learning.plans import PlanService
from app.learning.decisions import recommend
from app.core.schema import utcnow
from app.learning.review_port import require_aware


def queue_correction(conn, correction, *, as_of):
    """Append an outbox job inside the caller's evidence transaction, no commit."""
    require_aware(as_of)
    if as_of < correction.occurred_at:
        raise ValueError("Recovery cutoff must include the correction")
    job_id="recovery:"+hashlib.sha256(correction.evidence_id.encode()).hexdigest()
    old=conn.execute("SELECT payload FROM correction_jobs WHERE job_id=?",(job_id,)).fetchone()
    if old:
        return json.loads(old[0])
    body=dict(job_id=job_id,learner_id=correction.learner_id,correction_id=correction.evidence_id,
        status="pending",attempts=0,as_of=as_of.isoformat(),source_ref=correction.supersedes)
    conn.execute("INSERT INTO correction_jobs VALUES (?,?,?,?)",(job_id,correction.learner_id,"pending",json.dumps(body)))
    return body


class RecoveryJobs:
    def __init__(self, store):
        self.store=store; self.learning=LearningService(store)

    def appeal(self, learner_id, appeal_id, evidence_id, reason):
        if not reason.strip() or len(reason)>4000: raise ValueError("Appeal reason required, max 4000 chars")
        with self.store.lock:
            evidence=self.learning.evidences(learner_id)
            if evidence_id not in {e.evidence_id for e in evidence}: raise KeyError("Unknown evidence")
            body=dict(appeal_id=appeal_id,evidence_id=evidence_id,reason=reason,learner_id=learner_id,status="submitted")
            prior=self.store.conn.execute("SELECT payload FROM learning_appeals WHERE appeal_id=?",(appeal_id,)).fetchone()
            if prior:
                if json.loads(prior[0])!=body: raise EvidenceConflict("Appeal ID content conflict")
                return body
            self.store.conn.execute("INSERT INTO learning_appeals VALUES (?,?,?)",(appeal_id,learner_id,json.dumps(body,ensure_ascii=False)))
            self.store.conn.commit(); return body

    def enqueue(self, learner_id, correction_id, *, as_of=None):
        with self.store.lock:
            values=self.learning.evidences(learner_id)
            correction=next((e for e in values if e.evidence_id==correction_id and e.supersedes),None)
            if correction is None: raise KeyError("Correction evidence required")
            at=as_of or max(utcnow(),correction.occurred_at)
            require_aware(at)
            if at<correction.occurred_at: raise ValueError("Recovery cutoff must include the correction")
            body=queue_correction(self.store.conn,correction,as_of=at)
            self.store.conn.commit(); return body

    def get(self, learner_id, job_id):
        with self.store.lock:
            self.learning._authorize(learner_id)
            row=self.store.conn.execute("SELECT payload FROM correction_jobs WHERE learner_id=? AND job_id=?",(learner_id,job_id)).fetchone()
            if row is None: raise KeyError("Unknown recovery job")
            return json.loads(row[0])

    def pending(self, learner_id):
        with self.store.lock:
            self.learning._authorize(learner_id)
            return [json.loads(row[0]) for row in self.store.conn.execute(
                "SELECT payload FROM correction_jobs WHERE learner_id=? AND status IN ('pending','failed') ORDER BY rowid",(learner_id,))]

    def reconcile(self, learner_id):
        """Recover historical corrections created before atomic outbox support."""
        with self.store.lock:
            values=self.learning.evidences(learner_id)
            for correction in values:
                if correction.supersedes:
                    queue_correction(self.store.conn,correction,as_of=max(utcnow(),correction.occurred_at))
            self.store.conn.commit()
            return self.pending(learner_id)

    def run(self, learner_id, job_id, graph, fault=None):
        fault=fault or (lambda _:None)
        with self.store.lock:
            conn=self.store.conn
            # PlanService's public transaction methods cannot nest. Build the proposal
            # using its explicit writes in this one local job transaction.
            conn.execute("BEGIN IMMEDIATE")
            job=None
            try:
                job=self.get(learner_id,job_id)
                if job["status"]=="completed":
                    conn.commit()
                    return job
                pending=dict(job)
                self.learning._authorize(learner_id)
                at=max(datetime.fromisoformat(job["as_of"]),utcnow())
                rebuilt=self.learning.rebuild(learner_id,as_of=at)
                path=recommend(self.learning,learner_id,at,prerequisites=graph)
                contract=self.store.latest_contract(learner_id)
                proposal=None
                if contract:
                    from app.core.schema import PlanVersion
                    from app.learning.plans import content_diff
                    plans=PlanService(self.store); active=plans._active(contract.goal_contract_id)
                    content=dict(active.content if active else {},path=path)
                    proposal=PlanVersion(goal_contract_id=contract.goal_contract_id,status="proposed",
                        prior_version_id=active.version_id if active else None,content=content,
                        diff=content_diff(active.content if active else {},content),change_reason="Evidence correction recovery")
                    plans._save(proposal); plans._register(contract,proposal)
                    plans._audit(learner_id,proposal,"plan_proposed",at,"Correction recovery; requires learner review")
                fault("proposal")
                job.update(status="completed",attempts=job["attempts"]+1,rebuilt_state=rebuilt,
                    state_comparison=dict(source_ref=job["source_ref"],correction_ref=job["correction_id"],
                        before=self._before(learner_id,job["source_ref"],at),after=rebuilt),
                    proposed_plan=proposal.model_dump(mode="json") if proposal else None)
                conn.execute("UPDATE correction_jobs SET status=?,payload=? WHERE job_id=?",("completed",json.dumps(job,ensure_ascii=False),job_id))
                fault("job"); conn.commit(); return job
            except Exception as exc:
                conn.rollback()
                # Keep retry state without retaining the exception's potentially secret text.
                if job is not None and not isinstance(exc, ConsentDenied):
                    failed=dict(pending,status="failed",attempts=pending["attempts"]+1,error_code="recovery_failed_retryable")
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        self.learning._authorize(learner_id)
                        # Another worker may complete after this rollback. Never
                        # overwrite its committed result with stale failure metadata.
                        conn.execute("UPDATE correction_jobs SET status=?,payload=? WHERE job_id=? AND status!='completed' AND json_extract(payload,'$.attempts')=?",
                            ("failed",json.dumps(failed),job_id,pending["attempts"]))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                raise

    def _before(self,learner_id,source_ref,at):
        from app.learning.replay import replay
        values=self.learning.evidences(learner_id)
        removed=set()
        for e in sorted(values,key=lambda e:e.event_seq):
            if e.supersedes==source_ref or e.supersedes in removed:
                removed.add(e.evidence_id)
        selected=[e for e in values if e.evidence_id not in removed]
        result=replay(selected,learner_id,at)
        return dict(mastery=[s.model_dump(mode="json") for s in result.mastery.values()],
                    retention=[s.model_dump(mode="json") for s in result.retention.values()])
