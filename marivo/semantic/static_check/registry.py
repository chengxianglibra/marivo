"""In-memory registry of captured semantic definition records."""

from __future__ import annotations

from dataclasses import dataclass, field

from ibis.common.deferred import Deferred
from typing_extensions import override

from marivo.datasource.spi import DatasourceIR
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityIR,
    MeasureIR,
    RelationshipIR,
)


@dataclass
class Registry:
    """Holds all loaded IR objects, indexed by semantic_id."""

    domains: dict[str, DomainIR] = field(default_factory=dict)
    datasources: dict[str, DatasourceIR] = field(default_factory=dict)
    entities: dict[str, EntityIR] = field(default_factory=dict)
    dimensions: dict[str, DimensionIR] = field(default_factory=dict)
    measures: dict[str, MeasureIR] = field(default_factory=dict)
    relationships: dict[str, RelationshipIR] = field(default_factory=dict)
    # Published pure declarations, keyed by their qualified full name.
    declared_measures: dict[str, Deferred] = field(default_factory=dict)
    declared_metrics: dict[str, Deferred] = field(default_factory=dict)
    _frozen: bool = field(default=False, init=False, repr=False)

    @property
    def is_frozen(self) -> bool:
        """Report whether Registry attributes are frozen."""
        return self._frozen

    def freeze(self) -> None:
        """Prevent reassignment of Registry attributes after whole-graph validation."""
        if self._frozen:
            return
        object.__setattr__(self, "_frozen", True)

    @override
    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("compiled semantic registry is immutable")
        object.__setattr__(self, name, value)
