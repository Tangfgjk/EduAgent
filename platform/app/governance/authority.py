"""Server-provisioned roles and learner assignments, never request role claims."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class AuthorizationDenied(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    subject_id: str
    role: Literal["learner", "teacher", "privacy_officer"]
    learner_ids: frozenset[str]

    def require(self, learner_id: str, operation: str) -> None:
        allowed = {
            "learner": {"read", "withdraw", "request_deletion"},
            "teacher": {"read", "review", "quarantine", "worker"},
            "privacy_officer": {"read", "review", "quarantine", "restore", "delete", "backup", "worker"},
        }
        if not self.subject_id or learner_id not in self.learner_ids or operation not in allowed.get(self.role, set()):
            raise AuthorizationDenied("Server-provisioned role or learner assignment required")
