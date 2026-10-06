"""Atomic evidence ingestion and state ownership, independent of UI/runtime."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Callable
from uuid import uuid4

from app.core.schema import KCMastery, MentalStateSnapshot, utcnow
from app.learning.schema import LearningEvidence, LearningStateTransition, MasteryState, RetentionState
from app.storage.db import Store


class EvidenceConflict(ValueError):
    pass


class ConsentDenied(PermissionError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(evidence):
    return hashlib.sha256(_json(evidence.model_dump(mode="json", exclude={"event_seq", "ingested_at"})).encode()).hexdigest()


class LearningService:
    def __init__(self, store: Store):
        self.store = store

    def set_consent(self, learner_id, scopes, version, source, at):
        if not version or not source or any(s not in {"teaching", "research", "evolution"} for s in scopes):
            raise ValueError("invalid consent record")
        record = dict(learner_id=learner_id, scopes=sorted(set(scopes)), version=version, source=source, at=at.isoformat())
        with self.store.lock:
            old = self.store.conn.execute("SELECT payload FROM consent_records WHERE learner_id=? AND version=?", (learner_id, version)).fetchone()
            if old:
                previous = json.loads(old[0])
                if any(previous[key] != record[key] for key in ("learner_id","scopes","version","source")):
                    raise EvidenceConflict("consent version is immutable")
                return previous
            self.store.conn.execute("INSERT OR IGNORE INTO consent_records VALUES (?,?,?)", (learner_id, version, _json(record)))
            self.store.conn.commit()
        return record

    def consent(self, learner_id):
        with self.store.lock:
            row = self.store.conn.execute("SELECT payload FROM consent_records WHERE learner_id=? ORDER BY rowid DESC LIMIT 1", (learner_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def _authorize(self, learner_id, purpose="teaching", evidence=None):
        current = self.consent(learner_id)
        if current is None or purpose not in current["scopes"]:
            raise ConsentDenied(f"{purpose} consent required")
        if evidence is not None:
            row = self.store.conn.execute("SELECT payload FROM consent_records WHERE learner_id=? AND version=?", (learner_id, evidence.consent_version)).fetchone()
            collected = json.loads(row[0]) if row else None
            if collected is None or purpose not in collected["scopes"] or purpose not in evidence.consent_scope or collected["source"] != evidence.authorization_source:
                raise ConsentDenied("collection consent mismatch")

    def evidences(self, learner_id, purpose="teaching"):
        with self.store.lock:
            self._authorize(learner_id, purpose)
            rows = self.store.conn.execute("SELECT payload FROM learning_evidence WHERE learner_id=? ORDER BY event_seq", (learner_id,)).fetchall()
            result = [LearningEvidence.model_validate_json(r[0]) for r in rows]
            return [e for e in result if self._allowed(e, purpose)]

    def _allowed(self, evidence, purpose):
        try:
            self._authorize(evidence.learner_id, purpose, evidence)
            return True
        except ConsentDenied:
            return False

    def _states(self, learner_id, consumer, model):
        with self.store.lock:
            self._authorize(learner_id)
            rows = self.store.conn.execute("SELECT payload FROM learning_states WHERE learner_id=? AND consumer=? ORDER BY kc_id", (learner_id, consumer)).fetchall()
            return [model.model_validate_json(r[0]) for r in rows]

    def masteries(self, learner_id):
        return self._states(learner_id, "mastery", MasteryState)

    def retentions(self, learner_id):
        return self._states(learner_id, "retention", RetentionState)

    def transitions(self, learner_id):
        with self.store.lock:
            self._authorize(learner_id)
            rows = self.store.conn.execute("SELECT payload FROM learning_transitions WHERE learner_id=? ORDER BY event_seq,rowid", (learner_id,)).fetchall()
            return [json.loads(r[0]) for r in rows]

    def _audit(self, learner_id, kind, detail):
        self.store.conn.execute("INSERT INTO learning_audit(learner_id,payload) VALUES (?,?)", (learner_id, _json(dict(kind=kind, detail=detail, at=utcnow().isoformat()))))

    def consume(self, evidence: LearningEvidence, *, expected_versions=None, fault: Callable | None = None, receipt=None):
        from app.learning.mastery_port import update_mastery
        from app.learning.review_port import update_retention
        expected_versions = expected_versions or {}
        checkpoint = fault or (lambda _: None)
        conn = self.store.conn
        with self.store.lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._authorize(evidence.learner_id, evidence=evidence)
                old = conn.execute("SELECT content_hash FROM learning_evidence WHERE evidence_id=?", (evidence.evidence_id,)).fetchone()
                if old:
                    if old[0] != _hash(evidence):
                        raise EvidenceConflict("same evidence ID has different content")
                    row = conn.execute("SELECT payload FROM evidence_consumption WHERE learner_id=? AND evidence_id=? AND consumer='mastery'", (evidence.learner_id, evidence.evidence_id)).fetchone()
                    if row is None:
                        raise EvidenceConflict("Persisted evidence is missing its consumption receipt; rebuild required")
                    conn.commit()
                    return json.loads(row[0])
                if evidence.supersedes:
                    replaced = conn.execute("SELECT learner_id,payload FROM learning_evidence WHERE evidence_id=?", (evidence.supersedes,)).fetchone()
                    if replaced is None or replaced[0] != evidence.learner_id:
                        raise EvidenceConflict("correction must reference this learner's evidence")
                    original = LearningEvidence.model_validate_json(replaced[1])
                    if original.kc_refs != evidence.kc_refs or original.attempt_id != evidence.attempt_id:
                        raise EvidenceConflict("correction cannot change attempt or KC identity")
                    superseding = conn.execute("SELECT evidence_id FROM learning_evidence WHERE learner_id=? AND json_extract(payload,'$.supersedes')=?",(evidence.learner_id,evidence.supersedes)).fetchone()
                    if superseding:
                        raise EvidenceConflict("correction must reference the current effective evidence")
                else:
                    attempt = conn.execute("SELECT evidence_id FROM learning_evidence WHERE learner_id=? AND json_extract(payload,'$.attempt_id')=?",(evidence.learner_id,evidence.attempt_id)).fetchone()
                    if attempt:
                        raise EvidenceConflict("attempt already has an evidence identity")
                seq = int(conn.execute("INSERT INTO event_clock DEFAULT VALUES").lastrowid)
                saved = evidence.model_copy(update={"event_seq": seq, "ingested_at": utcnow()})
                conn.execute("INSERT INTO learning_evidence VALUES (?,?,?,?,?)", (saved.evidence_id, saved.learner_id, seq, _hash(saved), saved.model_dump_json()))
                checkpoint("evidence")
                rebuild_needed = bool(saved.supersedes)
                previous = {}
                for kc in saved.kc_refs:
                    for consumer, model in [("mastery", MasteryState), ("retention", RetentionState)]:
                        row = conn.execute("SELECT payload FROM learning_states WHERE learner_id=? AND kc_id=? AND consumer=?", (saved.learner_id,kc,consumer)).fetchone()
                        state = model.model_validate_json(row[0]) if row else model(learner_id=saved.learner_id,kc_id=kc)
                        previous[(kc, consumer)] = state
                        expected = expected_versions.get(f"{kc}:{consumer}")
                        if expected is not None and expected != state.state_version:
                            raise EvidenceConflict("prior state CAS conflict")
                        if consumer == "retention" and state.last_review_at and saved.occurred_at < state.last_review_at:
                            rebuild_needed = True
                replayed = None
                if rebuild_needed:
                    from app.learning.replay import replay
                    replayed = replay(self.evidences(saved.learner_id), saved.learner_id, max(saved.ingested_at, max(e.occurred_at for e in self.evidences(saved.learner_id))))
                transitions = []
                for consumer, update, model in [("mastery", update_mastery, MasteryState), ("retention", update_retention, RetentionState)]:
                    for kc in saved.kc_refs:
                        prior = previous[(kc,consumer)]
                        if replayed is None:
                            state, reason = update(prior, saved)
                        else:
                            states = getattr(replayed, consumer)
                            state = states.get(kc, model(learner_id=saved.learner_id,kc_id=kc))
                            state = state.model_copy(update={"state_version": prior.state_version + 1})
                            reason = "versioned_rebuild"
                        row = conn.execute("SELECT version FROM learning_states WHERE learner_id=? AND kc_id=? AND consumer=?", (saved.learner_id,kc,consumer)).fetchone()
                        if row and row[0] != prior.state_version:
                            raise EvidenceConflict("state CAS conflict")
                        conn.execute("INSERT INTO learning_states VALUES (?,?,?,?,?) ON CONFLICT(learner_id,kc_id,consumer) DO UPDATE SET version=excluded.version,payload=excluded.payload", (saved.learner_id,kc,consumer,state.state_version,state.model_dump_json()))
                        checkpoint(consumer)
                        transition = LearningStateTransition(transition_id=uuid4().hex,learner_id=saved.learner_id,kc_id=kc,evidence_ids=state.evidence_refs if replayed else [saved.evidence_id],consumer=consumer,prior_state_version=prior.state_version,new_state_version=state.state_version,algorithm_version=state.algorithm_version if consumer=="mastery" else state.scheduler_version,parameter_set_id=state.parameter_set_id,event_seq=seq,created_at=saved.ingested_at,skip_reason=reason)
                        transitions.append(transition.model_dump(mode="json"))
                        conn.execute("INSERT INTO learning_transitions VALUES (?,?,?,?)", (transition.transition_id,saved.learner_id,seq,transition.model_dump_json()))
                checkpoint("transition")
                result = dict(evidence_id=saved.evidence_id,event_seq=seq,transitions=transitions)
                for consumer in ("mastery","retention"):
                    conn.execute("INSERT INTO evidence_consumption VALUES (?,?,?,?)", (saved.learner_id,saved.evidence_id,consumer,_json(result)))
                self._projection(saved.learner_id)
                checkpoint("snapshot")
                if replayed:
                    self._save_rebuild(saved.learner_id,replayed,saved.ingested_at)
                if saved.supersedes:
                    from app.learning.recovery import queue_correction
                    queue_correction(conn, saved, as_of=max(saved.ingested_at, saved.occurred_at))
                    checkpoint("recovery_queue")
                if receipt:
                    key,fingerprint,builder = receipt
                    conn.execute("INSERT INTO learning_api_receipts VALUES (?,?,?,?)",(saved.learner_id,key,fingerprint,_json(builder(result))))
                    checkpoint("receipt")
                conn.commit()
                return result
            except Exception as exc:
                conn.rollback()
                if isinstance(exc, (EvidenceConflict,ConsentDenied)):
                    self._audit(evidence.learner_id,type(exc).__name__,str(exc))
                    conn.commit()
                raise

    def _projection(self, learner_id):
        # Legacy mental-state view is a read projection; only this service updates cognition.
        base = self.store.latest_snapshot(learner_id)
        snapshot = base.model_copy(deep=True) if base else MentalStateSnapshot(learner_id=learner_id)
        snapshot.snapshot_id = uuid4().hex
        snapshot.created_at = utcnow()
        snapshot.knowledge_state.kc_masteries = [KCMastery(kc_id=s.kc_id,p_mastery=s.p_mastery,ci95=(max(0,s.p_mastery-(1-s.confidence)/2),min(1,s.p_mastery+(1-s.confidence)/2)),evidence_refs=s.evidence_refs) for s in self.masteries(learner_id)]
        self.store.conn.execute("INSERT INTO snapshots VALUES (?,?,?,?)", (snapshot.snapshot_id,learner_id,snapshot.created_at.isoformat(),snapshot.model_dump_json()))

    def _save_rebuild(self, learner_id, result, as_of):
        payload = dict(as_of=as_of.isoformat(), evidence_refs=result.evidence_refs, mastery={k:v.model_dump(mode="json") for k,v in result.mastery.items()}, retention={k:v.model_dump(mode="json") for k,v in result.retention.items()})
        self.store.conn.execute("INSERT INTO learning_rebuilds VALUES (?,?,?)", (uuid4().hex,learner_id,_json(payload)))

    def rebuild(self, learner_id, *, as_of):
        from app.learning.replay import replay
        result = replay(self.evidences(learner_id), learner_id, as_of)
        return dict(mastery=[s.model_dump(mode="json") for s in result.mastery.values()],retention=[s.model_dump(mode="json") for s in result.retention.values()],evidence_refs=result.evidence_refs,as_of=as_of.isoformat())
