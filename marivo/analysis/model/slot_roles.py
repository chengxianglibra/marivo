"""Typed roles for a Query stage's physical transport slots."""

from __future__ import annotations

from dataclasses import dataclass
from operator import attrgetter
from typing import Literal, TypeAlias

SlotRole: TypeAlias = Literal[
    "key",
    "value",
    "numeric_check",
    "predicate_check",
    "presence",
    "sample_scope",
    "witness_key",
    "raw_component",
]

NUMERIC_PRESENCE = "__numeric_present"
SAMPLE_PRESENCE = "__sample_present"


@dataclass(frozen=True, slots=True)
class StageSlot:
    name: str
    role: SlotRole
    position: int = 0
    owner: str = ""


def _physical_role(name: str, keys: dict[str, int] | None) -> StageSlot:
    """Read existing generated SQL aliases at the transport boundary."""
    if keys is not None and name in keys:
        return StageSlot(name, "key", keys[name])
    owner, witness, ordinal = name.rpartition(".witness.")
    if witness and ordinal.isdigit():
        return StageSlot(name, "witness_key", int(ordinal), owner)
    stem, _, _ = name.partition(".block")
    if keys is None and name.startswith("key.") and name[4:].isdigit():
        return StageSlot(name, "key", int(name[4:]))
    if stem.startswith("predicate_check."):
        return StageSlot(name, "predicate_check")
    if stem.startswith("numeric_check."):
        return StageSlot(name, "numeric_check")
    if stem in (NUMERIC_PRESENCE, SAMPLE_PRESENCE):
        return StageSlot(name, "presence")
    if stem.startswith("__sample_scope_"):
        width = stem.removeprefix("__sample_scope_")
        if width.isdigit():
            return StageSlot(name, "sample_scope", int(width))
    if stem.startswith("__original_"):
        return StageSlot(name, "raw_component")
    return StageSlot(name, "value")


@dataclass(frozen=True, slots=True)
class StageObligations:
    """Complete keys, checks, support and raw columns associated with a stage."""

    slots: tuple[StageSlot, ...]

    @classmethod
    def from_aliases(
        cls,
        names: tuple[str, ...],
        keys: tuple[str, ...] | None = None,
        *,
        checks: frozenset[str] = frozenset(),
    ) -> StageObligations:
        keyed = None if keys is None else {name: index for index, name in enumerate(keys)}
        slots = []
        for name in names:
            slot = _physical_role(name, keyed)
            if slot.role == "value" and name in checks:
                slot = StageSlot(name, "numeric_check")
            slots.append(slot)
        return cls(tuple(slots))

    def names(self, *roles: SlotRole) -> tuple[str, ...]:
        return tuple(slot.name for slot in self.slots if slot.role in roles)

    def ordered_keys(self) -> tuple[str, ...]:
        return tuple(
            slot.name
            for slot in sorted(self.slots, key=attrgetter("position"))
            if slot.role == "key"
        )

    def witnesses(self, check: str, *, width: int | None = None) -> tuple[str, ...]:
        return tuple(
            slot.name
            for slot in sorted(self.slots, key=attrgetter("position"))
            if slot.role == "witness_key"
            and slot.owner == check
            and (
                width is None
                or (slot.position < width and slot.name == f"{check}.witness.{slot.position}")
            )
        )

    @property
    def numeric_presence(self) -> str | None:
        return NUMERIC_PRESENCE if NUMERIC_PRESENCE in self.names("presence") else None

    def coverage_marker(self, width: int) -> str | None:
        scope_names = self.names("sample_scope")
        if (scope := f"__sample_scope_{width}") in scope_names:
            return scope
        return (
            SAMPLE_PRESENCE if SAMPLE_PRESENCE in self.names("presence") else self.numeric_presence
        )
