"""Checked in-memory original inputs to local attribution kernels."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import pandas as pd

from marivo.analysis.compiler.attribute_inputs import input_failure
from marivo.analysis.compiler.original import ComponentLayout
from marivo.analysis.compiler.query_stages import AttributeRootLayout
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.unique_keys import UniqueKeys

if TYPE_CHECKING:
    from marivo.analysis.core.attribute import AttributeMatrixSource
    from marivo.analysis.model.frame_contracts import CheckReceipt


@dataclass(frozen=True, slots=True, eq=False)
class ComponentFrame:
    """One source occurrence's complete actual inputs, confined to an invocation."""

    layout: ComponentLayout
    table: pd.DataFrame
    domain: pd.DataFrame | None = None
    table_unique_keys: UniqueKeys = field(default_factory=UniqueKeys)
    domain_unique_keys: UniqueKeys = field(default_factory=UniqueKeys)

    def __post_init__(self) -> None:
        expected = tuple(column.slot_id for column in self.layout.raw_columns)
        if tuple(self.table.columns) != expected:
            raise _invalid(f"original columns {tuple(self.table.columns)!r}; expected {expected!r}")
        # complete_components owns carrier, nullability and complete-key validation.
        if self.domain is not None and tuple(self.domain.columns) != self.layout.keys:
            raise _invalid("original coordinate support differs from the complete fine keys")
        object.__setattr__(self, "table_unique_keys", self.table_unique_keys.project(expected))
        object.__setattr__(
            self, "domain_unique_keys", self.domain_unique_keys.project(self.layout.keys)
        )

    @property
    def coordinates(self) -> pd.DataFrame:
        return (
            self.domain
            if self.domain is not None
            else self.table.loc[:, list(self.layout.keys)].drop_duplicates()
        )


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="complete invocation-local original inputs from one checked source statement",
        received=received,
        repair="Retain the original complete contribution identities and fine coordinate mapping.",
        stage="query_components",
        help_target="analysis_frame.compute",
    )


@dataclass(frozen=True, slots=True, eq=False)
class AttributeRootInputs:
    """One independently checked parent or fine role and its own source receipt."""

    layout: AttributeRootLayout
    stage_id: str
    parent: pd.DataFrame
    fine: pd.DataFrame
    receipts: tuple[CheckReceipt, ...]
    parent_state: tuple[Literal["present", "known_empty"], ...] = ()
    parent_unique_keys: UniqueKeys = field(default_factory=UniqueKeys)
    fine_unique_keys: UniqueKeys = field(default_factory=UniqueKeys)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parent_unique_keys",
            self.parent_unique_keys.project(tuple(str(c) for c in self.parent.columns)),
        )
        object.__setattr__(
            self,
            "fine_unique_keys",
            self.fine_unique_keys.project(tuple(str(c) for c in self.fine.columns)),
        )


def select_attribute_root(
    roots: tuple[AttributeRootInputs, ...],
    scope: Literal["parent", "fine"],
    source: AttributeMatrixSource,
) -> AttributeRootInputs:
    """Resolve one role, full use, slot and captured window without first-match lookup."""
    matched = tuple(
        root
        for root in {root.stage_id: root for root in roots}.values()
        if root.layout.scope == scope
        and any(
            leaf.use == source.use
            and (
                source.window is None or leaf.request.binding.window_key == source.window.window_key
            )
            for leaf in root.layout.leaves
        )
        and any(column.slot_id == source.source for column in root.layout.columns)
    )
    if len(matched) != 1:
        input_failure(
            "missing_component_proof",
            f"{scope}: expected one exact use/slot/window role, received {len(matched)}",
        )
    return matched[0]
