"""Rebuildable teaching memory with immutable source references, never a second KT model."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from datetime import datetime
from uuid import uuid4
from functools import wraps

from app.learning.review_port import require_aware
from app.learning.service import LearningService


def source_snapshot(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.store.lock:
            owns_transaction=not self.store.conn.in_transaction
            if owns_transaction:
                self.store.conn.execute("BEGIN IMMEDIATE")
            try:
                result=method(self,*args,**kwargs)
                if owns_transaction:
                    self.store.conn.commit()
                return result
            except Exception:
                if owns_transaction:
                    self.store.conn.rollback()
                raise
    return guarded


class MemoryService:
    version = "source-linked-memory-v1"

    def __init__(self, store):
        self.store = store
        self.learning = LearningService(store)
        with store.lock:
            store.conn.execute("CREATE TABLE IF NOT EXISTS memory_views(view_id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(learner_id,fingerprint))")
            store.conn.commit()

    @source_snapshot
    def sources(self, learner_id: str, as_of: datetime):
        require_aware(as_of)
        with self.store.lock:
            evidence = [e for e in self.learning.evidences(learner_id) if e.occurred_at <= as_of]
            replaced = {e.supersedes for e in evidence if e.supersedes}
            evidence = [e for e in evidence if e.evidence_id not in replaced]
            events = []
            for row in self.store.conn.execute("SELECT payload FROM events WHERE learner_pseudo_id=? ORDER BY event_seq", (learner_id,)):
                event = json.loads(row[0])
                if datetime.fromisoformat(event["ts"]) > as_of or event.get("consent_scope") != "teaching":
                    continue
                consent = self.store.conn.execute("SELECT payload FROM consent_records WHERE learner_id=? AND version=?", (learner_id,event.get("consent_version"))).fetchone()
                collected = json.loads(consent[0]) if consent else {}
                if "teaching" in collected.get("scopes",[]) and collected.get("source") == event.get("authorization_source"):
                    events.append(event)
            return evidence, events

    @source_snapshot
    def rebuild(self, learner_id: str, as_of: datetime) -> dict:
        with self.store.lock:
            evidence, events = self.sources(learner_id, as_of)
            l2 = []
            sessions = sorted({e.session_id for e in evidence} | {e["session_id"] for e in events})
            for session_id in sessions:
                attempts = [e for e in evidence if e.session_id == session_id]
                observations = [e for e in events if e["session_id"] == session_id]
                l2.append(dict(session_id=session_id, derived=True,
                    verdict_counts=dict(Counter(e.verdict_status for e in attempts)),
                    observation_counts=dict(Counter(e["observation"]["kind"] for e in observations)),
                    evidence_refs=[e.evidence_id for e in attempts],
                    event_refs=[e["event_id"] for e in observations]))
            # Profile stores descriptive behavior and source refs, never p_mastery/retention.
            counts = Counter(e["observation"]["kind"] for e in events)
            l3 = dict(derived=True, behavior_counts=dict(counts),
                evidence_refs=[e.evidence_id for e in evidence], event_refs=[e["event_id"] for e in events],
                state_refs=[])
            if evidence:
                rebuilt = self.learning.rebuild(learner_id, as_of=as_of)
                l3["state_refs"] = [dict(kc_id=s["kc_id"],state_version=s["state_version"],consumer="mastery",replayed=True)
                                    for s in rebuilt["mastery"]]
            else:
                l3["state_refs"] = []
            body = dict(learner_id=learner_id, as_of=as_of.isoformat(), version=self.version,
                purpose="teaching", l1=dict(evidence_refs=[e.evidence_id for e in evidence],
                    event_refs=[e["event_id"] for e in events]), l2=l2, l3=l3)
            fingerprint = hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            previous = self.store.conn.execute("SELECT payload FROM memory_views WHERE learner_id=? AND fingerprint=?",(learner_id,fingerprint)).fetchone()
            if previous:
                return json.loads(previous[0])
            result = dict(view_id=uuid4().hex, fingerprint=fingerprint, **body)
            self.store.conn.execute("INSERT INTO memory_views VALUES (?,?,?,?)",(result["view_id"],learner_id,fingerprint,json.dumps(result,ensure_ascii=False)))
            self.store.conn.execute("INSERT INTO learning_audit(learner_id,payload) VALUES (?,?)",(learner_id,json.dumps(dict(kind="memory_rebuilt",view_id=result["view_id"],version=self.version,source_refs=body["l1"],as_of=body["as_of"]))))
            return result

    @source_snapshot
    def get(self, learner_id: str, view_id: str) -> dict:
        with self.store.lock:
            self.learning._authorize(learner_id)
            row = self.store.conn.execute("SELECT payload FROM memory_views WHERE learner_id=? AND view_id=?",(learner_id,view_id)).fetchone()
            if row is None:
                raise KeyError("Unknown memory view")
            result = json.loads(row[0])
            evidence, events = self.sources(learner_id,datetime.fromisoformat(result["as_of"]))
            if set(result["l1"]["evidence_refs"]) != {e.evidence_id for e in evidence} or set(result["l1"]["event_refs"]) != {e["event_id"] for e in events}:
                raise ValueError("Memory source revisions changed; rebuild required")
            return result
