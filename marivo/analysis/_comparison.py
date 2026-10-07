"""Immutable exact correspondence and comparison design values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from marivo.analysis.core.model import reject


@dataclass(frozen=True, slots=True)
class ExactKeys:
    """Pair unique complete typed keys with equal images, including double-empty inputs.

    Args: verification: Check an unknown key equality, or assume it for this call.
    Returns: An immutable exact-key correspondence policy.
    Example: ``current.ratio(reference, pairing=mv.ExactKeys())``.
    Constraints: Equal row counts alone never prove correspondence.
    """

    verification: Literal["check", "assume"] = "check"

    def __post_init__(self) -> None:
        if self.verification not in ("check", "assume"):
            reject(
                "check or assume",
                str(self.verification),
                "Use mv.ExactKeys(verification='check' or 'assume').",
                "analysis.comparison",
            )

    def __repr__(self) -> str:
        return f"<ExactKeys verification={self.verification!r}; use .show()>"

    def show(self) -> None:
        """Print the exact-key policy.

        Args: None.
        Returns: None; prints one bounded policy description.
        Example: ``mv.ExactKeys().show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print(
            "ExactKeys: unique complete typed keys with equal images; double-empty is valid. "
            f"Unknown equality: {self.verification}; applicable declarations and derivations are trusted."
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UnionKeys:
    """Pair the union of complete typed keys with an explicit missing-side policy.

    Args: missing: Keep missing sides or invoke proven original Metric empty finish.
    Returns: An immutable union-key correspondence policy.
    Example: ``mv.TimeChange(pairing=mv.UnionKeys(missing="keep"))``.
    Constraints: Missing coordinates remain distinct from present non-Defined Cells.
    """

    missing: Literal["keep", "metric_empty"]

    def __post_init__(self) -> None:
        if self.missing not in ("keep", "metric_empty"):
            reject(
                "keep or metric_empty",
                str(self.missing),
                "Choose an explicit missing-side policy.",
                "analysis.comparison",
            )

    def __repr__(self) -> str:
        return f"<UnionKeys missing={self.missing!r}; use .show()>"

    def show(self) -> None:
        """Print the union-key policy.

        Args: None.
        Returns: None; prints one bounded policy description.
        Example: ``mv.UnionKeys(missing="keep").show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print(f"UnionKeys: unique complete typed keys; missing={self.missing}.")


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeChange:
    """Compare distinct time roles over one shared target realization.

    Args: pairing: Complete typed-key correspondence policy.
    Returns: An immutable time-comparison design.
    Example: ``current.compare(baseline, design=mv.TimeChange())``.
    Constraints: Ordered recursive quantity templates and target captures must match.
    """

    pairing: ExactKeys | UnionKeys = field(default_factory=ExactKeys)
    kind: Literal["time"] = field(default="time", init=False)

    def __post_init__(self) -> None:
        if type(self.pairing) not in (ExactKeys, UnionKeys):
            reject(
                "ExactKeys or UnionKeys",
                type(self.pairing).__name__,
                "Choose a typed correspondence policy.",
                "analysis.comparison",
            )

    def __repr__(self) -> str:
        return f"<TimeChange pairing={type(self.pairing).__name__}; use .show()>"

    def show(self) -> None:
        """Print this time-comparison design.

        Args: None.
        Returns: None; prints one bounded design description.
        Example: ``mv.TimeChange().show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print("TimeChange: equal recursive templates, shared target captures, distinct time roles.")


@dataclass(frozen=True, slots=True, kw_only=True)
class CohortContrast:
    """Compare selected populations at one observation time on common Group or Singleton keys.

    Args: pairing: Complete typed-key correspondence policy.
    Returns: An immutable cohort-comparison design.
    Example: ``first.compare(second, design=mv.CohortContrast())``.
    Constraints: Entity rows cannot be paired as cohorts by position.
    """

    pairing: ExactKeys | UnionKeys = field(default_factory=ExactKeys)
    kind: Literal["cohort"] = field(default="cohort", init=False)

    def __post_init__(self) -> None:
        if type(self.pairing) not in (ExactKeys, UnionKeys):
            reject(
                "ExactKeys or UnionKeys",
                type(self.pairing).__name__,
                "Choose a typed correspondence policy.",
                "analysis.comparison",
            )

    def __repr__(self) -> str:
        return f"<CohortContrast pairing={type(self.pairing).__name__}; use .show()>"

    def show(self) -> None:
        """Print this cohort-comparison design.

        Args: None.
        Returns: None; prints one bounded design description.
        Example: ``mv.CohortContrast().show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print("CohortContrast: equal templates and time roles on common Group or Singleton keys.")


@dataclass(frozen=True, slots=True)
class WindowBucketAlignment:
    """Pair complete retained observation grids by original bucket ordinal.

    Args: None.
    Returns: The immutable window-bucket alignment value.
    Example: ``alignment = mv.window_bucket()``.
    Constraints: Original time coordinates and equal complete bucket counts are required.
    """

    kind: Literal["window_bucket"] = field(default="window_bucket", init=False)

    def __repr__(self) -> str:
        return "<WindowBucketAlignment kind='window_bucket'; use .show()>"

    def show(self) -> None:
        """Print the complete-bucket alignment rule.

        Args: None.
        Returns: None; prints one bounded alignment description.
        Example: ``mv.window_bucket().show()``.
        Constraints: Never renumbers filtered rows or infers calendar equivalence.
        """
        print(
            "window_bucket: pair equal complete grids by original ordinal; retain both coordinates."
        )


def window_bucket() -> WindowBucketAlignment:
    """Construct the canonical complete-grid bucket alignment.

    Args: None.
    Returns: An immutable WindowBucketAlignment.
    Example: ``design = mv.PeriodChange(alignment=mv.window_bucket())``.
    Constraints: Requires equal complete bucket counts, without truncation or renumbering.
    """
    return WindowBucketAlignment()


@dataclass(frozen=True, slots=True, kw_only=True)
class PeriodChange:
    """Compare complete ordered periods while retaining each original time coordinate.

    Args:
        alignment: Explicit window_bucket() correspondence.
        pairing: Correspondence for the complete non-time coordinates.
    Returns: An immutable period-comparison design.
    Example: ``current.compare(prior, design=mv.PeriodChange(alignment=mv.window_bucket()))``.
    Constraints: Bucket counts must match; filtered buckets cannot be renumbered.
    """

    alignment: WindowBucketAlignment
    pairing: ExactKeys | UnionKeys = field(default_factory=ExactKeys)
    kind: Literal["period"] = field(default="period", init=False)

    def __post_init__(self) -> None:
        if type(self.alignment) is not WindowBucketAlignment or type(self.pairing) not in (
            ExactKeys,
            UnionKeys,
        ):
            reject(
                "window_bucket alignment and typed correspondence",
                repr(self.alignment),
                "Use mv.PeriodChange(alignment=mv.window_bucket()).",
                "analysis.comparison",
            )

    def __repr__(self) -> str:
        return f"<PeriodChange alignment=window_bucket pairing={type(self.pairing).__name__}; use .show()>"

    def show(self) -> None:
        """Print the period-comparison design.

        Args: None.
        Returns: None; prints one bounded design description.
        Example: ``mv.PeriodChange(alignment=mv.window_bucket()).show()``.
        Constraints: Does not inspect rows or execute a Run.
        """
        print(
            "PeriodChange: equal complete ordered grids, shared targets, retained original coordinates."
        )
