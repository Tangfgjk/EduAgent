"""Server-held provenance receipts bind exact actions to reviewed source chunks.

This proves origin and action integrity, not truth or semantic entailment. A
caller-supplied KC list, source hash or receipt alone is never sufficient.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.core.actions import ActionEnvelope
from app.learning.retrieval import LocalRetrieval, ParseError


class GroundingDenied(PermissionError):
    pass


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, max_length=80)
    source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_id: str = Field(pattern=r"^[0-9a-f]{24}$")


def action_digest(action: ActionEnvelope) -> str:
    # Receipt itself is excluded; all other metadata and payload remain bound.
    data = action.model_dump(mode="json")
    data["policy_provenance"].pop("grounding_receipt", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class GroundingResult:
    allowed: bool
    reason: str


class GroundingVerifier:
    def __init__(self, retrieval: LocalRetrieval, *, known_kcs: set[str],
                 reviewed_source_versions: set[tuple[str, str]],
                 reviewed_source_kcs: dict[tuple[str, str], frozenset[str]] | None = None,
                 max_receipts: int = 2048):
        if max_receipts < 1:
            raise ValueError("max_receipts must be positive")
        self.retrieval = retrieval
        self.known_kcs = frozenset(known_kcs)
        self.reviewed_source_versions = frozenset(reviewed_source_versions)
        self.reviewed_source_kcs = dict(reviewed_source_kcs or {})
        self.max_receipts = max_receipts
        self._receipts: dict[str, tuple[str, tuple[SourceReference, ...]]] = {}
        self._lock = threading.RLock()

    def check_references(self, action: ActionEnvelope, references: list[SourceReference]) -> GroundingResult:
        refs = action.policy_provenance.get("grounded_to_kg")
        if not isinstance(refs, list) or not refs or any(not isinstance(kc, str) for kc in refs):
            return GroundingResult(False, "missing_or_invalid_kc_binding")
        if len(set(refs)) != len(refs) or not set(refs) <= self.known_kcs:
            return GroundingResult(False, "unknown_or_duplicate_kc")
        if not references or len(references) > 20:
            return GroundingResult(False, "source_reference_required")
        covered: set[str] = set()
        identities = set()
        for ref in references:
            identity = (ref.source_id, ref.source_version, ref.chunk_id)
            if identity in identities:
                return GroundingResult(False, "duplicate_source_reference")
            identities.add(identity)
            if (ref.source_id, ref.source_version) not in self.reviewed_source_versions:
                return GroundingResult(False, "source_version_not_reviewed")
            try:
                chunk = self.retrieval.resolve_reference(*identity)
            except (ParseError, OSError):
                return GroundingResult(False, "source_reference_unavailable_or_stale")
            if not chunk.kc_refs or not set(chunk.kc_refs) <= self.known_kcs:
                return GroundingResult(False, "source_kc_untrusted")
            if frozenset(chunk.kc_refs) != self.reviewed_source_kcs.get((ref.source_id, ref.source_version)):
                return GroundingResult(False, "source_kc_mapping_not_reviewed")
            if not set(refs).intersection(chunk.kc_refs):
                return GroundingResult(False, "source_kc_conflict")
            covered.update(chunk.kc_refs)
        if not set(refs) <= covered:
            return GroundingResult(False, "incomplete_kc_coverage")
        return GroundingResult(True, "verified_origin_not_semantic_entailment")

    def bind_action(self, action: ActionEnvelope, references: list[SourceReference]) -> ActionEnvelope:
        """Trusted server approval operation; must not be offered as a learner API."""
        result = self.check_references(action, references)
        if not result.allowed:
            raise GroundingDenied(result.reason)
        bound = action.model_copy(deep=True)
        token = secrets.token_urlsafe(32)
        bound.policy_provenance["grounding_receipt"] = token
        with self._lock:
            if len(self._receipts) >= self.max_receipts:
                self._receipts.pop(next(iter(self._receipts)))
            self._receipts[token] = (action_digest(bound), tuple(references))
        return bound

    def validate_action(self, action: ActionEnvelope) -> str | None:
        """Governor callback: None means pass, a stable reason means deny."""
        token = action.policy_provenance.get("grounding_receipt")
        if not isinstance(token, str):
            return "server_grounding_receipt_required"
        with self._lock:
            receipt = self._receipts.get(token)
        if receipt is None or receipt[0] != action_digest(action):
            return "action_receipt_missing_or_tampered"
        result = self.check_references(action, list(receipt[1]))
        return None if result.allowed else result.reason
