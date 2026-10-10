"""Immutable complete-key alignment values for comparisons and ratios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.analysis.core.model import reject


@dataclass(frozen=True, slots=True)
class OrdinalTimeKey:
    """Map each original grid bucket to its position within that complete grid.

    Args: None.
    Returns: An immutable ordinal time-key descriptor.
    Example: ``mv.exact(time=mv.ordinal())``.
    Constraints: Position correspondence does not establish calendar equivalence.
    """

    def __repr__(self) -> str:
        return "<OrdinalTimeKey; use .show()>"

    def show(self) -> None:
        """Print the time-key meaning without executing.

        Args: None.
        Returns: None; prints a bounded description.
        Example: ``mv.ordinal().show()``.
        Constraints: Requires complete equal-length grids in W1.
        """
        print("OrdinalTimeKey: original bucket positions; complete equal-length grids required.")


def _validate_time(time: OrdinalTimeKey | None) -> None:
    if time is not None and type(time) is not OrdinalTimeKey:
        reject(
            "an OrdinalTimeKey or None",
            type(time).__name__,
            "Use mv.ordinal() for complete time-grid correspondence.",
            "analysis.comparison",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ExactAlignment:
    """Pair unique complete keys with equal images under checked verification.

    Args: time: Optional original-grid ordinal mapping.
    Returns: An immutable exact alignment.
    Example: ``current.compare(baseline, align=mv.exact())``.
    Constraints: Unknown equality is checked; applicable frozen proofs remain trusted.
    """

    time: OrdinalTimeKey | None = None

    def __post_init__(self) -> None:
        _validate_time(self.time)

    def __repr__(self) -> str:
        return f"<ExactAlignment time={'ordinal' if self.time else 'none'}; use .show()>"

    def show(self) -> None:
        """Print the exact alignment policy.

        Args: None.
        Returns: None; prints a bounded description.
        Example: ``mv.exact().show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print(
            "ExactAlignment: unique complete keys with equal images; double-empty is valid; "
            f"verification=checked; time={'ordinal' if self.time else 'none'}."
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class OuterAlignment:
    """Pair the complete-key union while preserving or finishing missing sides.

    Args:
        missing: Keep missing coordinates, or use proven original Metric empty finish.
        time: Optional original-grid ordinal mapping.
    Returns: An immutable outer alignment.
    Example: ``current.compare(baseline, align=mv.outer())``.
    Constraints: Empty finish requires original complete coverage; grids must be equal-length.
    """

    missing: Literal["keep", "empty_value"] = "keep"
    time: OrdinalTimeKey | None = None

    def __post_init__(self) -> None:
        _validate_time(self.time)
        if self.missing not in ("keep", "empty_value"):
            reject(
                "keep or empty_value",
                str(self.missing),
                "Use mv.outer() to keep missing sides, or mv.outer(missing='empty_value').",
                "analysis.comparison",
            )

    def __repr__(self) -> str:
        return f"<OuterAlignment missing={self.missing!r} time={'ordinal' if self.time else 'none'}; use .show()>"

    def show(self) -> None:
        """Print the outer alignment policy.

        Args: None.
        Returns: None; prints a bounded description.
        Example: ``mv.outer().show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print(
            f"OuterAlignment: complete-key union; missing={self.missing}; time={'ordinal' if self.time else 'none'}."
        )


def exact(*, time: OrdinalTimeKey | None = None) -> ExactAlignment:
    """Bind checked exact correspondence, optionally over original time-grid positions.

    Args: time: Optional ordinal time-key descriptor.
    Returns: An immutable ExactAlignment.
    Example: ``current.compare(baseline, align=mv.exact(time=mv.ordinal()))``.
    Constraints: No assume option; unknown complete-key equality is checked.
    """
    return ExactAlignment(time=time)


def outer(
    *, missing: Literal["keep", "empty_value"] = "keep", time: OrdinalTimeKey | None = None
) -> OuterAlignment:
    """Bind a complete-key union with an explicit optional empty-finish policy.

    Args:
        missing: Keep missing coordinates by default, or invoke original Metric empty finish.
        time: Optional ordinal time-key descriptor.
    Returns: An immutable OuterAlignment.
    Example: ``current.compare(baseline, align=mv.outer())``.
    Constraints: Only compare accepts outer; W1 requires complete equal-length time grids.
    """
    return OuterAlignment(missing=missing, time=time)


def ordinal() -> OrdinalTimeKey:
    """Select position correspondence within each complete original time grid.

    Args: None.
    Returns: An immutable OrdinalTimeKey.
    Example: ``mv.exact(time=mv.ordinal())``.
    Constraints: Does not imply weekday, holiday or financial-calendar equivalence.
    """
    return OrdinalTimeKey()
