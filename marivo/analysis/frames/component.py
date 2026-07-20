"""Call mv.help() for bounded agent help over the Marivo analysis runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from marivo.analysis._semantic_persistence import AxisBindingV1, ComponentBindingV1
from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.semantic.metric_graph import CatalogMetricIdentity, MetricIdentity


def resolve_role_column_name(components: dict[str, str | Any], role: str) -> str:
    """Resolve a composition role to its DataFrame column name.

    Uses the component metric's short name (part after the last dot). Falls
    back to the role name when two components share the same short name.
    """
    from marivo.refs import Ref

    def _to_id(v: str | Any) -> str:
        return v.path if type(v) is Ref else str(v)

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
    metric_identity: MetricIdentity
    component_bindings: tuple[ComponentBindingV1, ...] = ()
    axis_bindings: tuple[AxisBindingV1, ...] = ()
    metric_id: str | None = Field(default=None, exclude=True)
    composition_kind: Literal["ratio", "weighted_average", "linear"] | None = None
    components: dict[str, str] = Field(default_factory=dict, exclude=True)
    linear_terms: tuple[tuple[str, str], ...] = ()
    axes: dict[str, Any] = Field(default_factory=dict, exclude=True)
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"]
    semantic_model: str = Field(default="", exclude=True)
    component_graph_schema: Literal["metric-component-graph/v1"] = "metric-component-graph/v1"
    root_node_ids: tuple[str, ...] = ()
    component_graph: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_component_graph(self) -> ComponentFrameMeta:
        if isinstance(self.metric_identity, CatalogMetricIdentity):
            derived_metric_id = self.metric_identity.metric_ref.path
        else:
            derived_metric_id = f"runtime:{self.metric_identity.expression_fingerprint}"
        if self.metric_id is not None and self.metric_id != derived_metric_id:
            raise ValueError("component metric_id display does not match metric_identity")
        self.metric_id = derived_metric_id

        derived_components = {
            binding.role: (
                binding.metric_identity.metric_ref.path
                if isinstance(binding.metric_identity, CatalogMetricIdentity)
                else (
                    f"runtime:{binding.metric_identity.expression_fingerprint}"
                    if binding.metric_identity is not None
                    else binding.column
                )
            )
            for binding in self.component_bindings
        }
        if self.components and self.components != derived_components:
            raise ValueError("component display map does not match component_bindings")
        self.components = derived_components

        derived_axes: dict[str, Any] = {}
        for binding in self.axis_bindings:
            key = (
                "time" if binding.role == "time_dimension" else binding.ref.path.rsplit(".", 1)[-1]
            )
            axis: dict[str, Any] = {
                "role": "time" if binding.role == "time_dimension" else "dimension",
                "column": binding.column,
                "ref": binding.ref.path,
            }
            if binding.grain is not None:
                axis["grain"] = binding.grain
            if binding.role == "time_dimension":
                axis["time_dimension"] = binding.ref.path.rsplit(".", 1)[-1]
            derived_axes[key] = axis
        if self.axes and self.axes != derived_axes:
            raise ValueError("component axes display does not match axis_bindings")
        self.axes = derived_axes

        catalog_paths = [
            self.metric_identity.metric_ref.path
            if isinstance(self.metric_identity, CatalogMetricIdentity)
            else ""
        ] + [
            binding.metric_identity.metric_ref.path
            for binding in self.component_bindings
            if isinstance(binding.metric_identity, CatalogMetricIdentity)
        ]
        domains = {path.split(".", 1)[0] for path in catalog_paths if "." in path}
        derived_model = next(iter(domains)) if len(domains) == 1 else ""
        if self.semantic_model and derived_model and self.semantic_model != derived_model:
            raise ValueError("component semantic_model display does not match structured refs")
        self.semantic_model = derived_model

        graph = self.component_graph
        if graph is None:
            return self
        if graph.get("schema") != self.component_graph_schema:
            raise ValueError("component_graph schema does not match component_graph_schema")
        roots = graph.get("root_node_ids")
        nodes = graph.get("nodes")
        if (
            not isinstance(roots, list)
            or not roots
            or not all(isinstance(root, str) for root in roots)
        ):
            raise ValueError("component_graph requires a non-empty ordered root_node_ids list")
        if tuple(roots) != self.root_node_ids:
            raise ValueError("component_graph root_node_ids do not match ComponentFrameMeta")
        if not isinstance(nodes, list):
            raise ValueError("component_graph requires typed node records")
        required_node_fields = {
            "node_id",
            "node_fingerprint",
            "node_kind",
            "evaluator_contract",
            "ordered_children",
            "occurrence_paths",
            "value_semantics",
            "quality",
            "coverage_ref",
            "governed_leaf_lineage",
        }
        node_ids: list[str] = []
        children_by_node: dict[str, tuple[str, ...]] = {}
        for index, node in enumerate(nodes):
            if not isinstance(node, dict) or required_node_fields - set(node):
                raise ValueError(f"component_graph.nodes[{index}] record is incomplete")
            node_id = node["node_id"]
            if not isinstance(node_id, str) or node.get("node_fingerprint") != node_id:
                raise ValueError(f"component_graph.nodes[{index}] identity is invalid")
            ordered_children = node["ordered_children"]
            if not isinstance(ordered_children, list):
                raise ValueError(f"component_graph.nodes[{index}].ordered_children must be a list")
            child_ids: list[str] = []
            child_roles: set[str] = set()
            for child_index, child in enumerate(ordered_children):
                if (
                    not isinstance(child, dict)
                    or set(child) != {"role", "node_id"}
                    or not isinstance(child["role"], str)
                    or not isinstance(child["node_id"], str)
                ):
                    raise ValueError(
                        f"component_graph.nodes[{index}].ordered_children[{child_index}] is invalid"
                    )
                if child["role"] in child_roles:
                    raise ValueError(f"component_graph.nodes[{index}] has duplicate child roles")
                child_roles.add(child["role"])
                child_ids.append(child["node_id"])
            occurrences = node["occurrence_paths"]
            if not isinstance(occurrences, list) or not all(
                isinstance(path, str) and path for path in occurrences
            ):
                raise ValueError(f"component_graph.nodes[{index}].occurrence_paths is invalid")
            semantics = node["value_semantics"]
            if not isinstance(semantics, dict) or not {
                "unit",
                "unit_state",
                "unit_capability_issue",
                "additivity",
                "fold",
                "semantic_shape",
                "key_columns",
            } <= set(semantics):
                raise ValueError(f"component_graph.nodes[{index}].value_semantics is incomplete")
            if semantics["unit_state"] is None:
                raise ValueError(
                    f"component_graph.nodes[{index}].value_semantics.unit_state is missing"
                )
            if node["quality"] is None or not isinstance(node["quality"], dict):
                raise ValueError(f"component_graph.nodes[{index}].quality is missing")
            coverage_ref = node["coverage_ref"]
            if coverage_ref is not None and not isinstance(coverage_ref, str):
                raise ValueError(f"component_graph.nodes[{index}].coverage_ref is invalid")
            if not isinstance(node["governed_leaf_lineage"], list):
                raise ValueError(f"component_graph.nodes[{index}].governed_leaf_lineage is invalid")
            node_ids.append(node_id)
            children_by_node[node_id] = tuple(child_ids)
        if len(node_ids) != len(set(node_ids)) or any(root not in node_ids for root in roots):
            raise ValueError("component_graph roots and node identities are inconsistent")
        known_nodes = set(node_ids)
        for node_id, referenced_child_ids in children_by_node.items():
            missing = [child_id for child_id in referenced_child_ids if child_id not in known_nodes]
            if missing:
                raise ValueError(
                    f"component_graph node {node_id!r} references missing children {missing!r}"
                )
        visiting: set[str] = set()
        reachable: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError(f"component_graph contains a cycle at {node_id!r}")
            if node_id in reachable:
                return
            visiting.add(node_id)
            for child_id in children_by_node[node_id]:
                visit(child_id)
            visiting.remove(node_id)
            reachable.add(node_id)

        for root in roots:
            visit(root)
        if reachable != known_nodes:
            raise ValueError("component_graph contains nodes unreachable from its roots")
        return self


@dataclass(repr=False)
class ComponentFrame(BaseFrame):
    """Call mv.help(ComponentFrame) for its public consumption contract."""

    meta: ComponentFrameMeta

    _NEXT_INTENTS: tuple[str, ...] = ()

    def _repr_identity(self) -> str:
        subject = (
            f"metric={self.meta.metric_id}"
            if self.meta.metric_id is not None
            else f"roots={len(self.meta.root_node_ids)}"
        )
        return (
            f"ComponentFrame ref={self.meta.ref} parent={self.meta.parent_ref} "
            f"{subject} rows={self.meta.row_count}"
        )
