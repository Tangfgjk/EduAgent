"""Future OIDC boundary only: no issuer configured, no claim accepted as a role."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VerifiedFederatedSubject:
    issuer: str
    subject: str
    audience: str
    authentication_time: str


class FederatedIdentityVerifier(Protocol):
    """Future trusted port must check signature, issuer, audience, nonce and expiry.

    It returns an external subject only. A separate approved server-side mapping
    provisions local account, tenant, class and role; tokens never supply roles.
    """
    def verify(self, encoded_token: str, *, expected_nonce: str) -> VerifiedFederatedSubject: ...


class UnconfiguredFederation:
    def verify(self, encoded_token: str, *, expected_nonce: str) -> VerifiedFederatedSubject:
        raise PermissionError("OIDC is unconfigured; no federated identity accepted")
