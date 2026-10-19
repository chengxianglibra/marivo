"""Complete invocation-local source layouts, without result correspondence lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from marivo._definition.correspondence import ComparisonAxis
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.model import reject


def fail(expected: str, received: str) -> NoReturn:
    reject(
        expected, received, "Preserve the complete source key and value schema.", "source.layout"
    )


@dataclass(frozen=True, slots=True)
class ComparisonEndpoint:
    """Complete prepared schema for one endpoint, without data or source handles."""

    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    axes: tuple[ComparisonAxis, ...]
    key_groups: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        slots = tuple(column.slot_id for column in self.columns)
        if (
            len(set(slots)) != len(slots)
            or len(self.axes) != len(self.key_groups)
            or tuple(key for group in self.key_groups for key in group) != self.keys
            or any(not group for group in self.key_groups)
            or len(set(self.keys)) != len(self.keys)
            or not set(self.keys).issubset(slots)
        ):
            fail("one complete key group per ordered axis", repr((self.keys, self.key_groups)))
        if len({column.name for column in self.values}) != len(self.values):
            fail("distinct endpoint quantity names", repr(self.values))

    def column(self, slot: str) -> DataColumn:
        for column in self.columns:
            if column.slot_id == slot:
                return column
        fail("a slot in the endpoint schema", slot)

    @property
    def values(self) -> tuple[DataColumn, ...]:
        support = set(self.keys)
        return tuple(column for column in self.columns if column.slot_id not in support)
