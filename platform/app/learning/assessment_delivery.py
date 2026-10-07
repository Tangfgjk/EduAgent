"""Server-owned delivery history. This attests on-platform help, not human independence."""
from __future__ import annotations

from datetime import datetime, timedelta
from contextlib import contextmanager
import hashlib
import json
import secrets

from app.learning.service import EvidenceConflict


def assessment_hash(asset):
    return hashlib.sha256(json.dumps(asset.model_dump(mode="json"), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class AssessmentDelivery:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.conn.execute("""CREATE TABLE IF NOT EXISTS assessment_deliveries (
                delivery_ref TEXT PRIMARY KEY, learner_id TEXT NOT NULL,
                issuance_id TEXT NOT NULL, content_hash TEXT NOT NULL,
                assessment_id TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(learner_id,issuance_id))""")
            store.conn.commit()

    def issue(self, learner_id, asset, issuance_id, requested_at, now, consent_version, project_id=None):
        identity = [asset.ref.key, assessment_hash(asset),
                    requested_at.isoformat() if requested_at else None, consent_version]
        if project_id is not None:
            identity.append(project_id)
        fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        with self._write(learner_id, consent_version):
            row = self.store.conn.execute("SELECT content_hash,payload FROM assessment_deliveries WHERE learner_id=? AND issuance_id=?",
                (learner_id, issuance_id)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise EvidenceConflict("issuance_id content conflict")
                return json.loads(row[1])
            prior = self.store.conn.execute("SELECT delivery_ref FROM assessment_deliveries WHERE learner_id=? AND assessment_id=?",
                (learner_id, asset.ref.asset_id)).fetchall()
            # Include legacy, runtime, corrected and all-phase evidence, not only measurement subsets.
            from app.learning.service import LearningService
            prior_evidence = [e.evidence_id for e in LearningService(self.store).evidences(learner_id)
                if e.assessment_id == asset.ref.asset_id]
            payload = dict(delivery_ref="delivery:" + secrets.token_hex(24), assessment_ref=asset.ref.key,
                assessment_sha256=assessment_hash(asset),
                learner_id=learner_id, project_id=project_id, issued_at=now.isoformat(),
                expires_at=(now + timedelta(hours=24)).isoformat(),
                consent_version=consent_version, first_exposure=not prior and not prior_evidence,
                attempt_number=max(len(prior), len(prior_evidence)) + 1,
                previous_delivery_refs=[r[0] for r in prior], previous_evidence_refs=prior_evidence,
                hint_level=0, answer_exposed=False, submitted_attempt=None, evidence_ref=None,
                history_scope="server_observed_only_not_off_platform_independence")
            self.store.conn.execute("INSERT INTO assessment_deliveries VALUES (?,?,?,?,?,?)",
                (payload["delivery_ref"], learner_id, issuance_id, fingerprint, asset.ref.asset_id, json.dumps(payload)))
            return payload

    def get(self, learner_id, delivery_ref, asset, at, consent_version):
        row = self.store.conn.execute("SELECT payload FROM assessment_deliveries WHERE delivery_ref=? AND learner_id=?",
            (delivery_ref, learner_id)).fetchone()
        if not row:
            raise EvidenceConflict("Unknown or foreign assessment delivery")
        payload = json.loads(row[0])
        if payload["assessment_ref"] != asset.ref.key or payload.get("assessment_sha256") != assessment_hash(asset) or payload["consent_version"] != consent_version:
            raise EvidenceConflict("Delivery asset or consent version mismatch")
        if not datetime.fromisoformat(payload["issued_at"]) <= at <= datetime.fromisoformat(payload["expires_at"]):
            raise EvidenceConflict("Delivery outside validity window")
        return payload

    def hint(self, learner_id, delivery_ref, asset, at, consent_version, level, project_id=None):
        with self._write(learner_id, consent_version):
            payload = self.get(learner_id, delivery_ref, asset, at, consent_version)
            if payload.get("project_id") != project_id:
                raise EvidenceConflict("Delivery belongs to another project")
            if payload["submitted_attempt"]:
                raise EvidenceConflict("Submitted delivery cannot receive new help")
            payload["hint_level"] = max(payload["hint_level"], level)
            self._save(payload)
            # Deliberately no answer exposure API. Template hints cannot reveal the stored solution.
            return dict(hint_level=payload["hint_level"], hint={1: "先圈出已知条件与需要求解的量。",
                2: "把关系写成等式，并检查等式两边是否做了相同的运算。"}[payload["hint_level"]])

    def observation(self, learner_id, body, asset, consent_version, *, required):
        if not body.delivery_ref:
            if required:
                raise EvidenceConflict("Server-issued delivery_ref required")
            return dict(independence_verified=False, delivery_history_verified=False,
                history_scope="legacy_client_report_unverified"), body.hint_level, body.assistance_mode, body.answer_exposed
        payload = self.get(learner_id, body.delivery_ref, asset, body.occurred_at, consent_version)
        if payload.get("project_id") != getattr(body, "project_id", None):
            raise EvidenceConflict("Delivery belongs to another project")
        if payload["submitted_attempt"] not in {None, body.attempt_id}:
            raise EvidenceConflict("Delivery already consumed by a different attempt")
        hint = max(payload["hint_level"], body.hint_level)
        exposed = payload["answer_exposed"] or body.answer_exposed
        mode = "answer" if exposed or body.assistance_mode == "answer" else "hint" if hint or body.assistance_mode == "hint" else "none"
        details = {key: payload[key] for key in ("delivery_ref", "assessment_sha256", "first_exposure", "attempt_number",
            "previous_delivery_refs", "previous_evidence_refs", "history_scope")}
        details.update(delivery_history_verified=True, independence_verified=payload["first_exposure"] and not hint and mode == "none" and not exposed)
        return details, hint, mode, exposed

    def consume_in_transaction(self, learner_id, body, asset, consent_version, evidence_ref, expected_observation=None):
        """Called inside LearningService's receipt checkpoint; rollback covers both facts."""
        if body.delivery_ref:
            self._authorize(learner_id, consent_version)
            if expected_observation is not None and self.observation(learner_id, body, asset, consent_version, required=True) != expected_observation:
                raise EvidenceConflict("Delivery help/history changed during verification; retry")
            payload = self.get(learner_id, body.delivery_ref, asset, body.occurred_at, consent_version)
            if payload["submitted_attempt"] not in {None, body.attempt_id}:
                raise EvidenceConflict("Delivery consumption conflict")
            payload.update(submitted_attempt=body.attempt_id, evidence_ref=evidence_ref)
            self._save(payload)

    def _authorize(self, learner_id, consent_version):
        from app.learning.service import LearningService
        service = LearningService(self.store)
        service._authorize(learner_id)
        if service.consent(learner_id)["version"] != consent_version:
            raise EvidenceConflict("Current consent changed during delivery operation")

    @contextmanager
    def _write(self, learner_id, consent_version):
        with self.store.lock:
            self.store.conn.execute("BEGIN IMMEDIATE")
            try:
                self._authorize(learner_id, consent_version)
                yield
                self.store.conn.commit()
            except Exception:
                self.store.conn.rollback()
                raise

    def _save(self, payload):
        self.store.conn.execute("UPDATE assessment_deliveries SET payload=? WHERE delivery_ref=?",
            (json.dumps(payload), payload["delivery_ref"]))
