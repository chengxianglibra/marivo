from __future__ import annotations

from typing import Protocol

from app.contracts.ids import Action, ResourceId, UserId
from app.contracts.values import AuthZDecision


class AuthZ(Protocol):
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision: ...
