"""Learner-signed plan revisions, transactional audit, and compare-and-swap."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json

from app.core.schema import GoalContract, PlanVersion
from app.learning.review_port import require_aware
from app.storage.db import Store


class PlanConflict(ValueError):
    """The lifecycle or the current effective baseline has changed."""


def content_diff(before: dict, after: dict) -> dict:
    """An explicit before/after diff for each changed top-level plan field."""
    return {key: {"before": deepcopy(before.get(key)), "after": deepcopy(after.get(key))}
            for key in sorted(set(before) | set(after))
            if key not in before or key not in after or before[key] != after[key]}


class PlanService:
    """Ownership comes from GoalContract; only sign makes a draft effective.

    Every mutation and its audit records share one SQLite transaction. The
    baseline version captured on propose is checked after BEGIN IMMEDIATE,
    including across separate connections. No intermediate Store write commits.
    """

    def __init__(self, store: Store):
        self.store = store

    @contextmanager
    def _transaction(self, as_of: datetime):
        require_aware(as_of)
        with self.store.lock, self.store.conn:
            self.store.conn.execute("BEGIN IMMEDIATE")
            yield

    def _contract(self, learner_id: str, contract_id: str) -> GoalContract:
        contract = self.store.get_contract(contract_id)
        if contract is None:
            raise KeyError("Unknown goal contract")
        if contract.learner_id != learner_id:
            raise PermissionError("The learning plan belongs to another learner")
        return contract

    def _get(self, learner_id: str, version_id: str) -> PlanVersion:
        row = self.store.conn.execute("SELECT payload FROM plan_versions WHERE version_id=?", (version_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown plan version")
        plan = PlanVersion.model_validate_json(row["payload"])
        self._contract(learner_id, plan.goal_contract_id)
        return plan

    def _active(self, contract_id: str) -> PlanVersion | None:
        rows = self.store.conn.execute(
            "SELECT payload FROM plan_versions WHERE goal_contract_id=? AND status='confirmed'", (contract_id,)
        ).fetchall()
        if len(rows) > 1:
            raise PlanConflict("Multiple effective plans require reconciliation")
        return PlanVersion.model_validate_json(rows[0]["payload"]) if rows else None

    def _save(self, plan: PlanVersion) -> None:
        self.store.conn.execute(
            "INSERT INTO plan_versions VALUES (?,?,?,?) ON CONFLICT(version_id) "
            "DO UPDATE SET status=excluded.status,payload=excluded.payload",
            (plan.version_id, plan.goal_contract_id, plan.status, plan.model_dump_json()),
        )

    def _register(self, contract: GoalContract, plan: PlanVersion) -> None:
        if plan.version_id not in contract.plan_version_refs:
            updated = contract.model_copy(update={"plan_version_refs": [*contract.plan_version_refs, plan.version_id]})
            self.store.conn.execute("UPDATE goal_contracts SET payload=? WHERE goal_contract_id=?",
                                    (updated.model_dump_json(), contract.goal_contract_id))

    def _audit(self, learner_id: str, plan: PlanVersion, action: str, as_of: datetime,
               reason: str = "") -> None:
        payload = dict(action=action, actor=learner_id, version_id=plan.version_id,
                       actor_kind="companion" if action in ("plan_proposed", "plan_drafted") else "student",
                       prior_version_id=plan.prior_version_id, goal_contract_id=plan.goal_contract_id,
                       status=plan.status, as_of=as_of.isoformat(), reason=reason, diff=plan.diff)
        self.store.conn.execute("INSERT INTO learning_audit (learner_id,payload) VALUES (?,?)",
                                (learner_id, json.dumps(payload, ensure_ascii=False)))

    def get(self, learner_id: str, version_id: str) -> PlanVersion:
        with self.store.lock:
            return self._get(learner_id, version_id)

    def active(self, learner_id: str, contract_id: str) -> PlanVersion | None:
        with self.store.lock:
            self._contract(learner_id, contract_id)
            return self._active(contract_id)

    def propose(self, learner_id: str, contract_id: str, content: dict, as_of: datetime) -> PlanVersion:
        with self._transaction(as_of):
            contract = self._contract(learner_id, contract_id)
            baseline = self._active(contract_id)
            plan = PlanVersion(goal_contract_id=contract_id, project_id=contract.project_id or "default",
                status="proposed", content=deepcopy(content),
                prior_version_id=baseline.version_id if baseline else None,
                diff=content_diff(baseline.content if baseline else {}, content), change_reason="路径推荐提案")
            self._save(plan)
            self._register(contract, plan)
            self._audit(learner_id, plan, "plan_proposed", as_of)
            return plan

    def prepare_draft(self, learner_id: str, contract_id: str, content: dict, as_of: datetime) -> PlanVersion:
        """Prepare a companion draft without fabricating learner acceptance."""
        with self._transaction(as_of):
            contract = self._contract(learner_id, contract_id)
            baseline = self._active(contract_id)
            plan = PlanVersion(goal_contract_id=contract_id, project_id=contract.project_id or "default",
                status="draft", content=deepcopy(content),
                prior_version_id=baseline.version_id if baseline else None,
                diff=content_diff(baseline.content if baseline else {}, content), change_reason="学习计划草案")
            self._save(plan)
            self._register(contract, plan)
            self._audit(learner_id, plan, "plan_drafted", as_of)
            return plan

    def accept(self, learner_id: str, version_id: str, as_of: datetime) -> PlanVersion:
        with self._transaction(as_of):
            plan = self._get(learner_id, version_id)
            if plan.status == "draft":
                return plan
            if plan.status != "proposed":
                raise PlanConflict("Only a proposed plan can be accepted as a draft")
            # A system proposal is evidence of what was suggested.  Do not turn
            # it into a mutable learner draft in place: preserve it in history
            # and create a distinct learner-owned draft instead.
            draft = PlanVersion(goal_contract_id=plan.goal_contract_id, project_id=plan.project_id,
                                status="draft", change_reason="学习者采纳系统建议后形成草案",
                                content=deepcopy(plan.content), prior_version_id=plan.prior_version_id,
                                diff=deepcopy(plan.diff))
            superseded = plan.model_copy(update={"status": "superseded"})
            self._save(superseded)
            self._save(draft)
            self._register(self._contract(learner_id, plan.goal_contract_id), draft)
            self._audit(learner_id, superseded, "plan_superseded", as_of, "Proposal accepted into learner draft")
            self._audit(learner_id, draft, "plan_accepted", as_of)
            return draft

    def modify(self, learner_id: str, version_id: str, content: dict, reason: str,
               as_of: datetime) -> PlanVersion:
        with self._transaction(as_of):
            plan = self._get(learner_id, version_id)
            if plan.status not in ("draft", "confirmed"):
                raise PlanConflict("Only a draft or confirmed plan can be revised")
            # Confirmed versions are immutable.  Revising one starts a new
            # unsigned draft while the confirmed baseline remains active.
            baseline = (plan if plan.status == "confirmed" else
                        self._get(learner_id, plan.prior_version_id) if plan.prior_version_id else None)
            revision = PlanVersion(goal_contract_id=plan.goal_contract_id, project_id=plan.project_id,
                status="draft", change_reason=reason, content=deepcopy(content),
                prior_version_id=baseline.version_id if baseline else None,
                diff=content_diff(baseline.content if baseline else {}, content))
            if plan.status == "draft":
                superseded = plan.model_copy(update={"status": "superseded"})
                self._save(superseded)
                self._audit(learner_id, superseded, "plan_superseded", as_of,
                            "Draft replaced by learner revision")
            self._save(revision)
            self._register(self._contract(learner_id, plan.goal_contract_id), revision)
            self._audit(learner_id, revision, "plan_modified", as_of, reason)
            return revision

    def reject(self, learner_id: str, version_id: str, reason: str, as_of: datetime) -> PlanVersion:
        with self._transaction(as_of):
            plan = self._get(learner_id, version_id)
            if plan.status == "rejected":
                return plan
            if plan.status not in ("proposed", "draft"):
                raise PlanConflict("Only a proposal or draft can be rejected")
            if not reason.strip():
                raise PlanConflict("A learner rejection reason is required")
            rejected = plan.model_copy(update={"status": "rejected", "change_reason": reason})
            self._save(rejected)
            self._audit(learner_id, rejected, "plan_rejected", as_of, reason)
            return rejected

    def sign(self, learner_id: str, version_id: str, as_of: datetime) -> PlanVersion:
        with self._transaction(as_of):
            plan = self._get(learner_id, version_id)
            if plan.status == "confirmed" and plan.signed_by == learner_id:
                return plan
            if plan.status != "draft":
                raise PlanConflict("Only the learner's draft can be signed")
            current = self._active(plan.goal_contract_id)
            current_id = current.version_id if current else None
            if current_id != plan.prior_version_id:
                raise PlanConflict("The effective baseline changed; review and propose a new Diff")
            if current:
                superseded = current.model_copy(update={"status": "superseded"})
                self._save(superseded)
                self._audit(learner_id, superseded, "plan_superseded", as_of, "New learner-signed plan")
            signed = plan.model_copy(update={"status": "confirmed", "confirmed_at": as_of,
                                              "signed_by": learner_id})
            self._save(signed)
            self._audit(learner_id, signed, "plan_signed", as_of)
            return signed
