"""ComponentFrame and ComponentFrameMeta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta


def resolve_role_column_name(components: dict[str, str | Any], role: str) -> str:
    """Resolve a composition role to its DataFrame column name.

    Uses the component metric's short name (part after the last dot). Falls
    back to the role name when two components share the same short name.
    """
    from marivo.refs import SemanticRef

    def _to_id(v: str | Any) -> str:
        return v.id if isinstance(v, SemanticRef) else str(v)

    short_name: str = _to_id(components[role]).rsplit(".", 1)[-1]
    short_names: list[str] = [_to_id(mid).rsplit(".", 1)[-1] for mid in components.values()]
    if len(short_names) != len(set(short_names)):
        return role
    return short_name


def resolve_role_columns(components: dict[str, str]) -> list[str]:
    """Resolve all composition roles to their DataFrame column names."""
    return [resolve_role_column_name(components, role) for role in components]


class ComponentFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["component_frame"] = "component_frame"
    parent_ref: str
    parent_kind: Literal["metric_frame", "delta_frame"]
    metric_id: str
    composition_kind: Literal["ratio", "weighted_average", "linear"]
    components: dict[str, str]
    linear_terms: tuple[tuple[str, str], ...] = ()
    axes: dict[str, Any]
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str


@dataclass(repr=False)
class ComponentFrame(BaseFrame):
    meta: ComponentFrameMeta

    _NEXT_INTENTS: tuple[str, ...] = ()

    def _repr_identity(self) -> str:
        return (
            f"ComponentFrame ref={self.meta.ref} parent={self.meta.parent_ref} "
            f"metric={self.meta.metric_id} rows={self.meta.row_count}"
        )
