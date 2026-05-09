from __future__ import annotations

from app.contracts.ids import Action, ResourceId, UserId
from app.contracts.values import AuthZDecision


class NoopAuthZAdapter:
    """Always allows, returns ``AuthZDecision(allowed=True)``."""

    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision:
        return AuthZDecision(allowed=True)
