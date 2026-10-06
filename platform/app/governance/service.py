"""Persistent human review and privacy lifecycle with fail-closed deletion."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable
from uuid import uuid4

from app.core.actions import ActionEnvelope
from app.core.schema import utcnow
from app.governance.authority import AuthorizationDenied, Principal
from app.governance.provenance import action_digest


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class DeletionPolicy:
    policy_id: str = "unreviewed"
    approved: bool = False
    isolated_instance: bool = False


class GovernanceService:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.conn.executescript("""
                CREATE TABLE IF NOT EXISTS governance_privacy(
                    learner_id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS governance_reviews(
                    review_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS governance_source_reviews(
                    learner_id TEXT NOT NULL, source_id TEXT NOT NULL, source_version TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(learner_id,source_id,source_version));
            """)
            store.conn.commit()

    def privacy(self, principal: Principal, learner_id: str):
        principal.require(learner_id, "read")
        with self.store.lock:
            row = self.store.conn.execute("SELECT payload FROM governance_privacy WHERE learner_id=?", (learner_id,)).fetchone()
            return json.loads(row[0]) if row else dict(learner_id=learner_id, state="active")

    def blocked(self, learner_id: str) -> bool:
        with self.store.lock:
            row = self.store.conn.execute("SELECT state FROM governance_privacy WHERE learner_id=?", (learner_id,)).fetchone()
            return row is not None and row[0] != "active"

    def change_privacy(self, principal: Principal, learner_id: str, operation: str, *, reason_code: str):
        expected = {"withdraw": "withdrawn", "quarantine": "quarantined", "request_deletion": "deletion_requested", "restore": "active"}
        if operation not in expected or not reason_code or len(reason_code) > 120:
            raise ValueError("Invalid lifecycle operation or reason code")
        principal.require(learner_id, operation)
        with self.store.lock:
            conn = self.store.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                old = conn.execute("SELECT state FROM governance_privacy WHERE learner_id=?", (learner_id,)).fetchone()
                if old and old[0] == "deleted":
                    raise AuthorizationDenied("Deleted identity cannot be restored")
                if operation == "restore" and old and old[0] == "deletion_requested":
                    raise AuthorizationDenied("Deletion request requires policy resolution, not silent restore")
                record = dict(learner_id=learner_id, state=expected[operation], operation=operation,
                    reason_code=reason_code, actor_ref=principal.subject_id, at=utcnow().isoformat(),
                    retained_records_not_erased=True, backup_erasure_verified=False)
                conn.execute("INSERT INTO governance_privacy VALUES (?,?,?) ON CONFLICT(learner_id) DO UPDATE SET state=excluded.state,payload=excluded.payload", (learner_id, record["state"], _json(record)))
                # Restoration restores access only: new consent must be explicitly collected.
                if operation != "restore":
                    version = "governance-" + uuid4().hex
                    consent = dict(learner_id=learner_id, scopes=[], version=version,
                        source="governance:" + principal.subject_id, at=record["at"])
                    conn.execute("INSERT INTO consent_records VALUES (?,?,?)", (learner_id, version, _json(consent)))
                conn.commit()
                return record
            except Exception:
                conn.rollback()
                raise

    def enqueue_safety_review(self, learner_id: str, action: ActionEnvelope, reason_code: str):
        # No raw student text or model output is copied into the review record.
        review_id = hashlib.sha256(f"{learner_id}:{action.action_id}:{action_digest(action)}:{reason_code}".encode()).hexdigest()[:32]
        with self.store.lock:
            previous = self.store.conn.execute("SELECT payload FROM governance_reviews WHERE review_id=?", (review_id,)).fetchone()
            if previous:
                return json.loads(previous[0])
            record = dict(review_id=review_id, learner_id=learner_id, action_id=action.action_id,
                action_sha256=action_digest(action), action_type=action.type.value,
                reason_code=reason_code, status="pending", at=utcnow().isoformat(),
                candidate_only=True, may_execute=False)
            self.store.conn.execute("INSERT INTO governance_reviews VALUES (?,?,?,?)", (review_id, learner_id, "pending", _json(record)))
            self.store.conn.commit()
            return record

    def reviews(self, principal: Principal, learner_id: str):
        principal.require(learner_id, "review")
        with self.store.lock:
            return [json.loads(row[0]) for row in self.store.conn.execute("SELECT payload FROM governance_reviews WHERE learner_id=? ORDER BY rowid", (learner_id,))]

    def resolve_review(self, principal: Principal, learner_id: str, review_id: str, *, decision: str, rationale_code: str):
        principal.require(learner_id, "review")
        if decision not in {"reject", "request_revision", "approve_candidate"} or not rationale_code or len(rationale_code) > 120:
            raise ValueError("Invalid review decision or rationale code")
        with self.store.lock:
            row = self.store.conn.execute("SELECT payload FROM governance_reviews WHERE learner_id=? AND review_id=?", (learner_id, review_id)).fetchone()
            if row is None:
                raise KeyError("Unknown or unauthorized safety review")
            record = json.loads(row[0])
            if record["status"] != "pending":
                if record.get("decision") == decision and record.get("rationale_code") == rationale_code and record.get("reviewer_ref") == principal.subject_id:
                    return record
                raise ValueError("Review resolution is immutable")
            record.update(status="resolved", decision=decision, rationale_code=rationale_code,
                reviewer_ref=principal.subject_id, reviewed_at=utcnow().isoformat(), may_execute=False)
            self.store.conn.execute("UPDATE governance_reviews SET status=?,payload=? WHERE review_id=?", ("resolved", _json(record), review_id))
            self.store.conn.commit()
            return record

    def review_source(self, principal: Principal, learner_id: str, source_id: str, source_version: str,
                      *, kc_refs: list[str], decision: str, rationale_code: str):
        principal.require(learner_id, "review")
        if decision not in {"approve", "reject"} or not rationale_code or len(rationale_code) > 120:
            raise ValueError("Invalid source review")
        with self.store.lock:
            record = dict(learner_id=learner_id, source_id=source_id, source_version=source_version,
                status=decision, kc_refs=sorted(set(kc_refs)), rationale_code=rationale_code, reviewer_ref=principal.subject_id,
                at=utcnow().isoformat(), semantic_truth_verified=False)
            self.store.conn.execute("INSERT INTO governance_source_reviews VALUES (?,?,?,?,?) ON CONFLICT(learner_id,source_id,source_version) DO UPDATE SET status=excluded.status,payload=excluded.payload", (learner_id, source_id, source_version, decision, _json(record)))
            self.store.conn.commit()
            return record

    def reviewed_sources(self, learner_id: str):
        with self.store.lock:
            return {(row[0], row[1]) for row in self.store.conn.execute("SELECT source_id,source_version FROM governance_source_reviews WHERE learner_id=? AND status='approve'", (learner_id,))}

    def reviewed_source_kcs(self, learner_id: str):
        with self.store.lock:
            rows = self.store.conn.execute("SELECT source_id,source_version,payload FROM governance_source_reviews WHERE learner_id=? AND status='approve'", (learner_id,)).fetchall()
            return {(row[0], row[1]): frozenset(json.loads(row[2]).get("kc_refs", [])) for row in rows}

    def _deletion_inventory(self, learner_id: str):
        # Static schema inventory prevents a new data-bearing table being silently omitted.
        direct = {"learners", "goal_contracts", "sessions", "snapshots", "learning_evidence",
            "learning_states", "evidence_consumption", "learning_transitions", "learning_rebuilds",
            "consent_records", "learning_audit", "learning_api_receipts", "assessment_deliveries", "correction_jobs",
            "learning_appeals", "runtime_sessions", "trigger_state", "derived_daily_budget", "qualitative_artifacts",
            "memory_views", "memory_annotations", "workspace_records", "governance_reviews", "governance_source_reviews"}
        derived = {"events", "verdicts", "plan_versions", "gateway_receipts"}
        excluded = {"sqlite_sequence", "schema_migrations", "event_clock", "knowledge_sources", "knowledge_chunks", "governance_privacy", "digests"}
        tables = {row[0] for row in self.store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        unknown = sorted(tables - direct - derived - excluded)
        predicates = {name: ("learner_id=?", (learner_id,)) for name in sorted(tables & direct)}
        if "events" in tables:
            predicates["events"] = ("learner_pseudo_id=?", (learner_id,))
        if "verdicts" in tables:
            predicates["verdicts"] = ("session_id IN (SELECT session_id FROM sessions WHERE learner_id=?)", (learner_id,))
        if "gateway_receipts" in tables:
            predicates["gateway_receipts"] = ("session_id IN (SELECT session_id FROM sessions WHERE learner_id=?)", (learner_id,))
        if "trigger_state" in tables:
            predicates["trigger_state"] = ("learner_id=?", (learner_id,))
        if "plan_versions" in tables:
            predicates["plan_versions"] = ("goal_contract_id IN (SELECT goal_contract_id FROM goal_contracts WHERE learner_id=?)", (learner_id,))
        counts = {name: self.store.conn.execute(f'SELECT COUNT(*) FROM "{name}" WHERE {where}', args).fetchone()[0]
                  for name, (where, args) in predicates.items()}
        blockers = ["unknown_table:" + name for name in unknown]
        if "digests" in tables and self.store.conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0]:
            blockers.append("global_digest_retention_unresolved")
        return predicates, counts, blockers

    def deletion_plan(self, principal: Principal, learner_id: str):
        principal.require(learner_id, "read")
        with self.store.lock:
            _, counts, blockers = self._deletion_inventory(learner_id)
            return dict(learner_id=learner_id, row_counts=counts, blockers=blockers,
                policy_approval_required=True, dry_run=True, filesystem_erasure=False,
                backups_archives_external_providers_not_erased=True)

    def execute_isolated_deletion(self, principal: Principal, learner_id: str, *, policy: DeletionPolicy,
                                  fault: Callable[[], None] | None = None):
        """Trusted offline port only. Never deletes files, shared DBs or backups."""
        principal.require(learner_id, "delete")
        if not policy.approved or not policy.isolated_instance or policy.policy_id == "unreviewed":
            raise AuthorizationDenied("Approved isolated-instance retention policy required")
        with self.store.lock:
            conn = self.store.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                state = conn.execute("SELECT state FROM governance_privacy WHERE learner_id=?", (learner_id,)).fetchone()
                if state is None or state[0] != "deletion_requested":
                    raise AuthorizationDenied("Explicit deletion request required")
                other = conn.execute("SELECT COUNT(*) FROM learners WHERE learner_id<>?", (learner_id,)).fetchone()[0]
                if other:
                    raise AuthorizationDenied("Shared learner database cannot be purged by this port")
                predicates, counts, blockers = self._deletion_inventory(learner_id)
                for table in predicates:
                    if conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] != counts[table]:
                        raise AuthorizationDenied("Foreign or unattributed data in isolated-instance inventory")
                if blockers:
                    raise AuthorizationDenied("Deletion inventory unresolved: " + ",".join(blockers))
                for table in ("verdicts", "gateway_receipts", "plan_versions"):
                    if table in predicates:
                        where, args = predicates.pop(table)
                        conn.execute(f'DELETE FROM "{table}" WHERE {where}', args)
                for table, (where, args) in predicates.items():
                    conn.execute(f'DELETE FROM "{table}" WHERE {where}', args)
                if fault is not None:
                    fault()
                receipt = dict(learner_id=learner_id, state="deleted", policy_id=policy.policy_id,
                    deleted_row_count=sum(counts.values()), at=utcnow().isoformat(),
                    minimal_pseudonymous_tombstone_retained=True, backup_erasure_verified=False,
                    filesystem_erasure=False, compliance_claim=False)
                conn.execute("UPDATE governance_privacy SET state='deleted',payload=? WHERE learner_id=?", (_json(receipt), learner_id))
                conn.commit()
                return receipt
            except Exception:
                conn.rollback()
                raise
