"""Bounded continuation facts for current lazy analysis results."""

from dataclasses import dataclass, field


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
    """Bounded logical Query or Population facts and mechanically valid next calls."""

    kind: str
    actions: tuple[AnalysisAction, ...]
    domain: str
    quantity: str | None
    _facts: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def show(self) -> None:
        """Print bounded logical definition facts and mechanically valid next calls.

        Args:
            None.
        Returns: None; prints one bounded contract card.
        Example: ``query.contract().show()``.
        Constraints: Reads bound metadata only; never reads schemas or business rows or creates a Run.
        """
        lines = [
            f"logical {self.kind}",
            f"domain={self.domain}; quantity={self.quantity or 'none'}",
            *(f"{name}={value}" for name, value in self._facts),
            *(f"call={action.call}; help={action.help_target}" for action in self.actions),
        ]
        text = "\n".join(lines)
        print(text.encode("utf-8")[:8192].decode("utf-8", errors="ignore"))

    def __repr__(self) -> str:
        return f"<AnalysisContract kind={self.kind}; use .show()>"
