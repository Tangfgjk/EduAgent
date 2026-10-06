"""Learning domain service running unchanged algorithms on a real repository.

Neither backend depends on SessionRuntime/SQLite backup. PostgreSQL writes live
facts/projections inside one transaction, not exported SQLite archives.
"""
from __future__ import annotations

import hashlib
from uuid import uuid4

from app.core.schema import KCMastery, MentalStateSnapshot, utcnow
from app.learning.schema import LearningEvidence, LearningStateTransition, MasteryState, RetentionState
from app.learning.service import ConsentDenied, EvidenceConflict, _hash


class RepositoryLearningService:
    def __init__(self, repository):
        self.repository = repository

    def set_consent(self, learner_id, scopes, version, source, at):
        if not learner_id or not version or not source or any(s not in {"teaching", "research", "evolution"} for s in scopes):
            raise ValueError("invalid consent record")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("timezone-aware consent required")
        record = dict(learner_id=learner_id, scopes=sorted(set(scopes)), version=version, source=source, at=at.isoformat())
        with self.repository.transaction(learner_id) as tx:
            old = tx.consent_version(learner_id, version)
            if old:
                if any(old[key] != record[key] for key in ("learner_id", "scopes", "version", "source")):
                    raise EvidenceConflict("consent version is immutable")
                return old
            tx.append("consent_records", dict(learner_id=learner_id, version=version, payload=record))
        return record

    def consent(self, learner_id):
        with self.repository.transaction(learner_id) as tx:
            return tx.current_consent(learner_id)

    def _authorize(self, learner_id, purpose="teaching", evidence=None):
        with self.repository.transaction(learner_id) as tx:
            tx.authorize(learner_id, purpose, evidence)

    def _allowed(self, evidence, purpose):
        try:
            self._authorize(evidence.learner_id, purpose, evidence)
            return True
        except ConsentDenied:
            return False

    def _authorized_history(self, tx, learner_id, purpose="teaching"):
        tx.authorize(learner_id, purpose)
        history = []
        for payload in tx.evidence_history(learner_id):
            evidence = LearningEvidence.model_validate(payload)
            try:
                tx.authorize(learner_id, purpose, evidence)
                history.append(evidence)
            except ConsentDenied:
                continue
        return history

    def evidences(self, learner_id, purpose="teaching"):
        with self.repository.transaction(learner_id) as tx:
            return self._authorized_history(tx, learner_id, purpose)

    def _states(self, learner_id, consumer, model):
        with self.repository.transaction(learner_id) as tx:
            tx.authorize(learner_id)
            return [model.model_validate(row) for row in tx.states(learner_id, consumer)]

    def masteries(self, learner_id):
        return self._states(learner_id, "mastery", MasteryState)

    def retentions(self, learner_id):
        return self._states(learner_id, "retention", RetentionState)

    def transitions(self, learner_id):
        with self.repository.transaction(learner_id) as tx:
            tx.authorize(learner_id)
            return tx.transitions(learner_id)

    def receipt(self, learner_id, attempt_id, fingerprint):
        with self.repository.transaction(learner_id) as tx:
            tx.authorize(learner_id)
            previous = tx.receipt(learner_id, attempt_id)
            if previous is None:
                return None
            if previous[0] != fingerprint:
                raise EvidenceConflict("attempt receipt content conflict")
            return previous[1]

    def rebuild(self, learner_id, *, as_of):
        from app.learning.replay import replay
        result = replay(self.evidences(learner_id), learner_id, as_of)
        return dict(mastery=[s.model_dump(mode="json") for s in result.mastery.values()],
            retention=[s.model_dump(mode="json") for s in result.retention.values()],
            evidence_refs=result.evidence_refs, as_of=as_of.isoformat())

    def consume(self, evidence, *, expected_versions=None, fault=None, receipt=None):
        from app.learning.mastery_port import update_mastery
        from app.learning.review_port import update_retention
        from app.learning.replay import replay
        evidence = LearningEvidence.model_validate(evidence.model_dump())
        expected_versions = expected_versions or {}
        checkpoint = fault or (lambda _: None)
        with self.repository.transaction(evidence.learner_id) as tx:
            tx.authorize(evidence.learner_id, evidence=evidence)
            if receipt:
                key, fingerprint, builder = receipt
                prior_receipt = tx.receipt(evidence.learner_id, key)
                if prior_receipt and prior_receipt[0] != fingerprint:
                    raise EvidenceConflict("attempt receipt content conflict")
            old = tx.evidence_identity(evidence.evidence_id)
            if old:
                if old[0] != _hash(evidence):
                    raise EvidenceConflict("same evidence ID has different content")
                result = tx.consumed(evidence.learner_id, evidence.evidence_id)
                if result is None:
                    raise EvidenceConflict("evidence consumption receipt missing")
                return result
            history = self._authorized_history(tx, evidence.learner_id)
            if evidence.supersedes:
                original = tx.evidence_identity(evidence.supersedes)
                if original is None or original[1]["learner_id"] != evidence.learner_id:
                    raise EvidenceConflict("correction must reference this learner's evidence")
                original_evidence = LearningEvidence.model_validate(original[1])
                if original_evidence.kc_refs != evidence.kc_refs or original_evidence.attempt_id != evidence.attempt_id:
                    raise EvidenceConflict("correction cannot change attempt or KC identity")
                if any(row.supersedes == evidence.supersedes for row in history):
                    raise EvidenceConflict("correction must reference current effective evidence")
            elif any(row.attempt_id == evidence.attempt_id for row in history):
                raise EvidenceConflict("attempt already has an evidence identity")
            saved = evidence.model_copy(update={"event_seq": tx.next_sequence(), "ingested_at": utcnow()})
            tx.append("learning_evidence", dict(evidence_id=saved.evidence_id, learner_id=saved.learner_id,
                event_seq=saved.event_seq, content_hash=_hash(saved), payload=saved.model_dump(mode="json")))
            checkpoint("evidence")
            previous, rebuild_needed = {}, bool(saved.supersedes)
            for kc in saved.kc_refs:
                for consumer, model in (("mastery", MasteryState), ("retention", RetentionState)):
                    value = tx.state(saved.learner_id, kc, consumer)
                    state = model.model_validate(value) if value else model(learner_id=saved.learner_id, kc_id=kc)
                    previous[kc, consumer] = state
                    expected = expected_versions.get(f"{kc}:{consumer}")
                    if expected is not None and expected != state.state_version:
                        raise EvidenceConflict("prior state CAS conflict")
                    if consumer == "retention" and state.last_review_at and saved.occurred_at < state.last_review_at:
                        rebuild_needed = True
            history.append(saved)
            replayed = replay(history, saved.learner_id, max(saved.ingested_at, max(row.occurred_at for row in history))) if rebuild_needed else None
            transitions = []
            for consumer, update, model in (("mastery", update_mastery, MasteryState), ("retention", update_retention, RetentionState)):
                for kc in saved.kc_refs:
                    prior = previous[kc, consumer]
                    if replayed is None:
                        state, reason = update(prior, saved)
                    else:
                        state = getattr(replayed, consumer).get(kc, model(learner_id=saved.learner_id, kc_id=kc))
                        state = state.model_copy(update={"state_version": prior.state_version + 1})
                        reason = "versioned_rebuild"
                    tx.write_state(state, consumer, prior.state_version)
                    checkpoint(consumer)
                    transition = LearningStateTransition(transition_id=uuid4().hex, learner_id=saved.learner_id,
                        kc_id=kc, evidence_ids=state.evidence_refs if replayed else [saved.evidence_id], consumer=consumer,
                        prior_state_version=prior.state_version, new_state_version=state.state_version,
                        algorithm_version=state.algorithm_version if consumer == "mastery" else state.scheduler_version,
                        parameter_set_id=state.parameter_set_id, event_seq=saved.event_seq,
                        created_at=saved.ingested_at, skip_reason=reason)
                    payload = transition.model_dump(mode="json")
                    transitions.append(payload)
                    tx.append("learning_transitions", dict(transition_id=transition.transition_id,
                        learner_id=saved.learner_id, event_seq=saved.event_seq, payload=payload))
            checkpoint("transition")
            result = dict(evidence_id=saved.evidence_id, event_seq=saved.event_seq, transitions=transitions)
            for consumer in ("mastery", "retention"):
                tx.append("evidence_consumption", dict(learner_id=saved.learner_id, evidence_id=saved.evidence_id,
                    consumer=consumer, payload=result))
            previous_snapshot = tx.latest_snapshot(saved.learner_id)
            snapshot = MentalStateSnapshot.model_validate(previous_snapshot) if previous_snapshot else MentalStateSnapshot(learner_id=saved.learner_id)
            snapshot.snapshot_id, snapshot.created_at = uuid4().hex, utcnow()
            snapshot.knowledge_state.kc_masteries = [KCMastery(kc_id=state["kc_id"], p_mastery=state["p_mastery"],
                ci95=(max(0,state["p_mastery"]-(1-state["confidence"])/2), min(1,state["p_mastery"]+(1-state["confidence"])/2)),
                evidence_refs=state["evidence_refs"]) for state in tx.states(saved.learner_id, "mastery")]
            tx.append("snapshots", dict(snapshot_id=snapshot.snapshot_id, learner_id=saved.learner_id,
                ts=snapshot.created_at.isoformat(), payload=snapshot.model_dump(mode="json")))
            checkpoint("snapshot")
            if replayed:
                tx.append("learning_rebuilds", dict(rebuild_id=uuid4().hex, learner_id=saved.learner_id,
                    payload=dict(as_of=saved.ingested_at.isoformat(), evidence_refs=replayed.evidence_refs,
                        mastery={kc: state.model_dump(mode="json") for kc, state in replayed.mastery.items()},
                        retention={kc: state.model_dump(mode="json") for kc, state in replayed.retention.items()})))
            if saved.supersedes:
                job_id = "recovery:" + hashlib.sha256(saved.evidence_id.encode()).hexdigest()
                job = dict(job_id=job_id, learner_id=saved.learner_id, correction_id=saved.evidence_id,
                    source_ref=saved.supersedes, status="pending", attempts=0,
                    as_of=max(saved.ingested_at, saved.occurred_at).isoformat())
                tx.append("correction_jobs", dict(job_id=job_id, learner_id=saved.learner_id, status="pending", payload=job))
                checkpoint("recovery_queue")
            if receipt:
                if prior_receipt:
                    raise EvidenceConflict("receipt already belongs to another evidence")
                tx.append("learning_api_receipts", dict(learner_id=saved.learner_id, attempt_id=key,
                    content_hash=fingerprint, payload=builder(result)))
                checkpoint("receipt")
            tx.append("learning_audit", dict(learner_id=saved.learner_id, payload=dict(kind="repository_evidence_consumed",
                evidence_id=saved.evidence_id, backend=self.repository.backend, event_seq=saved.event_seq)))
            checkpoint("audit")
            return result
