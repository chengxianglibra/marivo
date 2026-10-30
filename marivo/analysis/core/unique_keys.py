"""Invocation-local unique-key facts bound to one exact relation's slots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UniqueKeys:
    """Minimal candidate keys; no candidates means unknown, an empty key at most one row."""

    slots: tuple[str, ...] = ()
    keys: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.slots)) != len(self.slots):
            raise ValueError("Unique-key slots must be distinct")
        candidates: set[frozenset[str]] = set()
        for key in self.keys:
            if not set(key) <= set(self.slots):
                raise ValueError("Every unique-key component must belong to the relation")
            candidates.add(frozenset(key))
        minimal = [key for key in candidates if not any(other < key for other in candidates)]
        ordered = sorted(
            (tuple(slot for slot in self.slots if slot in key) for key in minimal),
            key=lambda key: (len(key), tuple(self.slots.index(slot) for slot in key)),
        )
        object.__setattr__(self, "keys", tuple(ordered))

    def proves(self, required: tuple[str, ...]) -> bool:
        """Whether a known candidate is contained in the requested complete key."""
        return set(required) <= set(self.slots) and any(
            set(key) <= set(required) for key in self.keys
        )

    def project(
        self, slots: tuple[str, ...], mapping: Mapping[str, str] | None = None
    ) -> UniqueKeys:
        """Keep fully retained candidates, applying an exact old-to-new slot rename."""
        renamed = mapping or {}
        keys = []
        for key in self.keys:
            candidate = tuple(renamed.get(slot, slot) for slot in key)
            if len(set(candidate)) == len(key) and set(candidate) <= set(slots):
                keys.append(candidate)
        return UniqueKeys(slots, tuple(keys))

    def add(self, key: tuple[str, ...]) -> UniqueKeys:
        """Record a producer guarantee or a successfully completed key check."""
        return UniqueKeys(self.slots, (*self.keys, key))

    def fixed(self, slots: tuple[str, ...], fixed: tuple[str, ...]) -> UniqueKeys:
        """Remove components restricted to one exact value before projection."""
        if not set(fixed) <= set(self.slots):
            raise ValueError("Fixed components must belong to the relation")
        return UniqueKeys(
            self.slots, tuple(tuple(slot for slot in key if slot not in fixed) for key in self.keys)
        ).project(slots)


UNKNOWN_UNIQUE_KEYS = UniqueKeys()
