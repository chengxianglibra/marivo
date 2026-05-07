from __future__ import annotations

from app.contracts.ids import Action, ResourceId, UserId
from app.contracts.values import AuthZDecision


class NoopAuthZ:
    """Always-allow AuthZ for local single-user mode."""

    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision:
        return AuthZDecision(allowed=True)
