"""Bounded continuation facts for current lazy analysis results."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class AnalysisAction:
    """One admitted receiver call and its exact native Help target."""

    call: str
    help_target: str

    def show(self) -> None:
        """Print the exact receiver call and its native Help route.

        Args: None.
        Returns: None; prints one bounded continuation fact.
        Example: ``query.contract().actions[0].show()``.
        Constraints: This is static guidance and does not execute the call.
        """
        print(f"{self.call[:160]}; marivo.help({self.help_target!r})")

    def __repr__(self) -> str:
        return f"<AnalysisAction call={self.call[:80]}; use .show()>"


@dataclass(frozen=True, slots=True)
class AnalysisContract:
    """Bounded current result kind and mechanically valid next operations."""

    kind: str
    phase: Literal["logical", "materialized"]
    actions: tuple[AnalysisAction, ...]
    domain: str
    quantity: str | None
    required_parts: tuple[str, ...]
    retained_parts: tuple[str, ...]
    _facts: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def show(self) -> None:
        """Print bounded definition, retained-state and admissible-call facts.

        Args:
            None.
        Returns: None; prints one bounded contract card.
        Example: ``query.contract().show()``.
        Constraints: Reads bound metadata only; never queries business source rows.
        """
        lines = [
            f"{self.phase} {self.kind}",
            f"domain={self.domain}; quantity={self.quantity or 'none'}",
            f"required_parts={','.join(self.required_parts) or 'none'}; "
            f"retained_parts={','.join(self.retained_parts) or 'none'}",
            *(f"{name}={value}" for name, value in self._facts),
            *(f"call={action.call}; help={action.help_target}" for action in self.actions),
        ]
        text = "\n".join(lines)
        print(text.encode("utf-8")[:8192].decode("utf-8", errors="ignore"))

    def __repr__(self) -> str:
        return f"<AnalysisContract kind={self.kind} phase={self.phase}; use .show()>"
