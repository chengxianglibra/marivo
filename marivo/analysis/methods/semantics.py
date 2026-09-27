"""The semantic owner of methods already connected to the private algebra.

Rule derivations carry the bound Pre, quantity, Cell policy, state, parts and
obligations. Physical declarations never replace or amend those derivations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, get_args

from marivo.analysis.core import rules
from marivo.analysis.core.model import CheckId, PartRole, Signature, StatisticalWeightPart
from marivo.analysis.methods.errors import reject

MethodName: TypeAlias = Literal[
    "bind_project",
    "map_correspond",
    "cell.difference",
    "cell.ratio",
    "row.sum",
    "row.mean",
    "row.count",
    "row.count_defined",
    "row.weighted_mean",
    "state_rollup",
    "parts_transport",
]


@dataclass(frozen=True, slots=True)
class MethodKey:
    name: MethodName
    version: int = 1

    def __post_init__(self) -> None:
        if (
            self.name not in get_args(MethodName)
            or type(self.version) is not int
            or self.version != 1
        ):
            reject(
                "a connected Analysis method at version 1",
                f"{self.name}@v{self.version}",
                "Use a connected method; datasource terminals and legacy journey IDs are not methods.",
            )

    def __str__(self) -> str:
        return f"{self.name}@v{self.version}"


def key_for_parameters(params: rules.RuleParameters) -> MethodKey:
    """Map closed parameter variants to exactly one concrete method identity."""
    if type(params) is rules.BindProject:
        return MethodKey("bind_project")
    if type(params) is rules.MapCorrespond:
        return MethodKey("map_correspond")
    if type(params) is rules.CellDerive:
        if params.method == "difference":
            return MethodKey("cell.difference")
        if params.method == "ratio":
            return MethodKey("cell.ratio")
    if type(params) is rules.RowState:
        for name in ("row.sum", "row.mean", "row.count", "row.count_defined", "row.weighted_mean"):
            if name == f"row.{params.method}":
                return MethodKey(name)
    if type(params) is rules.OriginalReduce:
        return MethodKey("state_rollup")
    if type(params) is rules.PartsTransport:
        return MethodKey("parts_transport")
    reject("closed method parameters", repr(params), "Use an exact registered parameter variant.")


@dataclass(frozen=True, slots=True)
class ContinuationRequirement:
    """Conditional construction K, not a public or executable continuation.

    The successor must still derive with exact parameters, bindings and premises.
    """

    method: MethodKey
    required_parts: tuple[PartRole, ...]


@dataclass(frozen=True, slots=True)
class MethodSemantics:
    """One semantic owner; all bound semantics come from its core derivation."""

    key: MethodKey
    owner: str

    def __post_init__(self) -> None:
        if type(self.key) is not MethodKey or self.owner != "analysis.core.rules":
            reject("a complete connected semantic owner", repr(self), "Use analysis.core.rules.")

    @property
    def rule(self) -> rules.RuleId:
        name = self.key.name
        if name in ("cell.difference", "cell.ratio"):
            return "cell_derive@v1"
        if name in ("row.sum", "row.mean", "row.count", "row.count_defined", "row.weighted_mean"):
            return "row_state@v1"
        if name == "state_rollup":
            return "original_reduce@v1"
        if name == "bind_project":
            return "bind_project@v1"
        if name == "map_correspond":
            return "map_correspond@v1"
        return "parts_transport@v1"

    @property
    def cell_policy(self) -> Literal["strict", "count_all", "defined_only", "input_owned"]:
        if self.key.name == "row.count":
            return "count_all"
        if self.key.name == "row.count_defined":
            return "defined_only"
        if self.rule in ("cell_derive@v1", "row_state@v1"):
            return "strict"
        return "input_owned"

    @property
    def required_parts(self) -> tuple[PartRole, ...]:
        if self.key.name == "state_rollup":
            return ("original_state", "coverage")
        if self.key.name == "row.weighted_mean":
            return ("statistical_weight",)
        return ()

    @property
    def required_checks(self) -> tuple[CheckId, ...]:
        """Minimum checker coverage; invocation-specific checks remain in Pre."""
        if self.rule == "cell_derive@v1":
            return ("source.exact_pairing@v1", "source.finite_numeric@v1")
        if self.key.name == "row.count_defined":
            return ("source.cell_policy@v1",)
        if self.rule == "row_state@v1" and self.key.name != "row.count":
            return ("source.finite_numeric@v1",)
        if self.key.name == "state_rollup":
            return ("source.contribution_partition@v1", "source.complete_coverage@v1")
        return ()

    @property
    def output_parts(self) -> tuple[PartRole, ...]:
        if self.rule == "cell_derive@v1":
            return ("current_endpoint", "baseline_endpoint")
        if self.rule == "row_state@v1":
            return ("row_state",)
        if self.key.name == "state_rollup":
            return ("original_state", "coverage")
        return ()

    @property
    def original_state_method(self) -> str:
        """The only connected original-state contract, independent of row sum."""
        if self.key.name != "state_rollup":
            reject(
                "an original-state method", str(self.key), "Use state_rollup for original state."
            )
        return "sum@v1"

    @property
    def state_components(self) -> tuple[str, ...]:
        if self.key.name == "state_rollup":
            return ("sum", "non_null_count")
        if self.key.name == "row.mean":
            return ("sum", "count")
        if self.key.name == "row.weighted_mean":
            return ("weighted_sum", "weight_sum")
        if self.key.name.startswith("row."):
            return (self.key.name.removeprefix("row."),)
        return ()

    def derive(
        self, inputs: tuple[Signature, ...], params: rules.RuleParameters
    ) -> rules.RuleDerivation:
        """Validate exact inputs and apply the sole owning semantic rule."""
        if key_for_parameters(params) != self.key:
            reject(str(self.key), str(key_for_parameters(params)), "Use this method's parameters.")
        if type(inputs) is not tuple or any(type(item) is not Signature for item in inputs):
            reject("immutable exact Signature inputs", repr(inputs), "Bind core signatures.")
        if (
            isinstance(params, (rules.CellDerive, rules.RowState))
            and params.value_policy != self.cell_policy
        ):
            reject(
                self.cell_policy,
                params.value_policy,
                "Preserve the registered method's Cell policy; adapters cannot redefine it.",
            )
        if type(params) is rules.BindProject:
            return rules._bind_project(inputs, params)
        if type(params) is rules.MapCorrespond:
            return rules._map_correspond(inputs, params)
        if type(params) is rules.CellDerive:
            return rules._cell_derive(inputs, params)
        if type(params) is rules.RowState:
            if params.method == "weighted_mean" and len(inputs) == 1:
                for part in inputs[0].parts:
                    if (
                        isinstance(part, StatisticalWeightPart)
                        and part.binding != inputs[0].domain.binding
                    ):
                        reject(
                            "statistical weights bound to the exact input and scope",
                            repr(part.binding),
                            "Bind the named weights to this invocation's domain.",
                        )
            return rules._row_state(inputs, params)
        if type(params) is rules.OriginalReduce:
            return rules._original_reduce(inputs, params)
        if type(params) is rules.PartsTransport:
            return rules._parts_transport(inputs, params)
        reject("closed method parameters", repr(params), "Use a registered parameter variant.")


CONNECTED_METHODS = (
    MethodSemantics(MethodKey("bind_project"), "analysis.core.rules"),
    MethodSemantics(MethodKey("map_correspond"), "analysis.core.rules"),
    MethodSemantics(MethodKey("cell.difference"), "analysis.core.rules"),
    MethodSemantics(MethodKey("cell.ratio"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.sum"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.mean"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.count"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.count_defined"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.weighted_mean"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup"), "analysis.core.rules"),
    MethodSemantics(MethodKey("parts_transport"), "analysis.core.rules"),
)
