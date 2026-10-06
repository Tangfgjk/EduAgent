"""Exact local curriculum/template binding for the normal teaching execution path.

Local authored and synthetic course assets are trusted for software use, not
teacher approval or education truth. Retrieved/free-model text cannot borrow
this trust class merely by claiming a known KC.
"""
from __future__ import annotations

import hashlib
import json

from app.core.actions import ActionEnvelope, ActionType, ScaffoldType
from app.core.schema import QuestionItem
from app.governance.provenance import action_digest
from app.learning.assets import AssetCatalog
from app.orchestration.policy import BuiltInPolicyV1, _generic_hint


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class RuntimeSources:
    def __init__(self, bank: list[QuestionItem], catalog: AssetCatalog):
        self.bank = {item.item_id: item.model_copy(deep=True) for item in bank}
        if len(self.bank) != len(bank) or not bank:
            raise ValueError("Nonempty unique canonical runtime bank required")
        self.bank_hash = content_hash([item.model_dump(mode="json") for item in bank])
        self.catalog_hash = content_hash(catalog.model_dump(mode="json"))
        self.catalog = catalog
        self.kcs = {item.ref.asset_id for item in catalog.knowledge}
        self._allowed: dict[str, str] = {}
        self._rejected: dict[str, str] = {}

    def propose(self, action, **kwargs):
        reason = self.bind(action, **kwargs)
        if reason:
            self._rejected[action.action_id] = reason
        return reason

    def assert_bank(self, bank):
        if content_hash([item.model_dump(mode="json") for item in bank]) != self.bank_hash:
            raise PermissionError("Persisted runtime bank differs from configured frozen source")

    def _bank_item(self, item):
        return item is not None and item.item_id in self.bank and self.bank[item.item_id] == item

    def bind(self, action: ActionEnvelope, *, context=None, expected_action=None, system_template=False):
        if system_template:
            kind = "server_system_template"
        else:
            refs = action.policy_provenance.get("grounded_to_kg")
            if not isinstance(refs, list) or len(refs) != 1 or refs[0] not in self.kcs:
                return "runtime_unknown_or_ambiguous_kc"
            kind = "frozen_course_asset"
            if action.policy_provenance.get("source_refs") or action.policy_provenance.get("grounding_receipt"):
                return "runtime_external_reference_requires_separate_review"
            if action.type in (ActionType.TASK, ActionType.QUESTION):
                item = self.bank.get(action.params.get("item_id"))
                if not item or item.kc_id != refs[0] or action.params.get("stem") != item.stem:
                    return "runtime_task_source_mismatch"
                assets = [asset for asset in self.catalog.assessments if asset.ref.asset_id == item.item_id]
                if not any(asset.stem == item.stem and asset.answer == item.answer
                           and any(kc.asset_id == item.kc_id for kc in asset.kc_refs) for asset in assets):
                    return "runtime_assessment_catalog_mismatch"
                if action.type == ActionType.TASK:
                    allowed = {"kind", "item_id", "stem", "difficulty", "assessment_kind"}
                    if (set(action.params) - allowed or action.params.get("difficulty") != item.difficulty
                            or action.params.get("kind") not in {"practice", "inquiry"}
                            or action.params.get("assessment_kind") not in {None, "diagnostic"}):
                        return "runtime_task_parameters_mismatch"
                elif set(action.params) != {"target", "item_id", "stem", "intent"}:
                    return "runtime_question_parameters_mismatch"
            elif action.type == ActionType.HINT:
                item = context.item if context is not None else None
                level = action.params.get("ladder_level")
                if not self._bank_item(item) or item.kc_id != refs[0] or type(level) is not int or not 0 <= level <= 3:
                    return "runtime_hint_source_mismatch"
                expected_text = item.hint_text(level) or _generic_hint(level)
                expected_form = ("nudge", "directive", "worked_partial", "worked_full")[level]
                if action.params.get("text") != expected_text or action.params.get("form") != expected_form:
                    return "runtime_hint_content_mismatch"
                if (set(action.params) != {"ladder_level", "form", "text", "target", "proactive", "scaffold_type"}
                        or type(action.params.get("proactive")) is not bool
                        or action.params.get("target") != "practice"
                        or action.params.get("scaffold_type") not in {entry.value for entry in ScaffoldType}):
                    return "runtime_hint_parameters_mismatch"
            elif action.type in (ActionType.FEEDBACK, ActionType.EXPLAIN):
                if context is None:
                    return "runtime_template_context_required"
                if expected_action is None and not hasattr(context, "snapshot"):
                    return "runtime_trusted_template_required"
                expected = expected_action or BuiltInPolicyV1().choose(context)
                if action.type != expected.type or action.params != expected.params or refs != expected.policy_provenance.get("grounded_to_kg"):
                    return "runtime_untrusted_free_text"
                kind = "server_policy_template"
            else:
                return "runtime_action_type_not_bound"
        action.policy_provenance.update(runtime_source_kind=kind,
            runtime_bank_sha256=self.bank_hash, runtime_catalog_sha256=self.catalog_hash,
            runtime_source_status="local_software_asset_not_teacher_approved",
            runtime_rendering="exact_template_no_model_rewrite")
        self._allowed[action.action_id] = action_digest(action)
        return None

    def validate(self, action: ActionEnvelope):
        if action.action_id in self._rejected:
            return self._rejected[action.action_id]
        if self._allowed.get(action.action_id) != action_digest(action):
            return "runtime_action_not_bound_or_changed"
        return None
