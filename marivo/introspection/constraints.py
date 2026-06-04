"""Shared agent-facing constraint metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Phase = Literal["decorator", "ast", "assembly", "runtime", "parity"]


@dataclass(frozen=True)
class ASTSpec:
    """Machine-readable AST rule summary for decorator function bodies."""

    name: str
    single_return: bool
    forbidden_statements: tuple[str, ...] = ()
    forbidden_attributes: tuple[str, ...] = ()
    forbidden_calls: tuple[str, ...] = ()
    allowed_calls: tuple[str, ...] = ()
    allowed_binops: tuple[str, ...] = ()
    allowed_unary_ops: tuple[str, ...] = ()
    component_call_only: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "single_return": self.single_return,
            "forbidden_statements": list(self.forbidden_statements),
            "forbidden_attributes": list(self.forbidden_attributes),
            "forbidden_calls": list(self.forbidden_calls),
            "allowed_calls": list(self.allowed_calls),
            "allowed_binops": list(self.allowed_binops),
            "allowed_unary_ops": list(self.allowed_unary_ops),
            "component_call_only": self.component_call_only,
        }


@dataclass(frozen=True)
class Constraint:
    """Agent-facing rule metadata."""

    id: str
    error_kind: str
    phase: Phase
    applies_to: tuple[str, ...]
    title: str
    why: str
    hint: str
    example: str | None = None
    docs_ref: str | None = None
    ast_spec: ASTSpec | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": str(self.id),
            "error_kind": self.error_kind,
            "phase": self.phase,
            "applies_to": list(self.applies_to),
            "title": self.title,
            "why": self.why,
            "hint": self.hint,
        }
        if self.example is not None:
            data["example"] = self.example
        if self.docs_ref is not None:
            data["docs_ref"] = self.docs_ref
        if self.ast_spec is not None:
            data["ast_spec"] = self.ast_spec.to_dict()
        return data

    def to_summary_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": str(self.id),
            "title": self.title,
            "hint": self.hint,
        }
        if self.example is not None:
            data["example"] = self.example
        return data
