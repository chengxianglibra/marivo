"""Pure column inputs to a compiled formula."""

from dataclasses import dataclass

from marivo.analysis.model.data_types import DataColumn


@dataclass(frozen=True, slots=True)
class FormulaInput:
    """One completed reduction slot, including its missing-coordinate value."""

    occurrence: tuple[int, ...]
    stage_id: str
    column: DataColumn
    zero_when_absent: bool
    checks: tuple[tuple[str, str, str], ...] = ()
    keys: tuple[str, ...] = ()
    key_groups: tuple[tuple[str, ...], ...] = ()
    presence: str | None = None
