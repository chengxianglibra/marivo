"""The semantic owner of methods already connected to the private algebra.

Rule derivations carry the bound Pre, quantity, Cell policy, state, parts and
obligations. Physical declarations never replace or amend those derivations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, get_args

from marivo.analysis.core import rules
from marivo.analysis.core.model import CheckId, PartRole, Signature, StatisticalWeightPart
from marivo.analysis.methods.errors import reject

if TYPE_CHECKING:
    from marivo.analysis.methods.physical import ValueType


MethodName: TypeAlias = Literal[
    "bind_project",
    "metric.observe",
    "metric.sum_zero",
    "metric.count",
    "metric.weighted_mean",
    "metric.ratio",
    "metric.linear",
    "map_correspond",
    "cell.difference",
    "cell.ratio",
    "row.sum",
    "row.mean",
    "row.count",
    "row.count_defined",
    "row.weighted_mean",
    "state_rollup",
    "state_rollup.sum_zero",
    "state_rollup.count",
    "state_rollup.ratio",
    "state_rollup.weighted_mean",
    "state_rollup.linear",
    "parts_transport",
    "association.spearman",
]


PersistentStateKind: TypeAlias = Literal[
    "none",
    "original_sum",
    "original_sum_zero",
    "original_count",
    "original_ratio",
    "original_weighted_mean",
    "original_linear",
    "difference",
    "row_sum",
    "row_count",
    "row_count_defined",
    "row_mean",
    "spearman",
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
    if type(params) is rules.OriginalRatio:
        return MethodKey("metric.ratio")
    if type(params) is rules.OccurrenceCombine:
        return MethodKey("metric.linear")
    if type(params) is rules.ObserveWeightedMean:
        return MethodKey("metric.weighted_mean")
    if type(params) is rules.ObserveCount:
        return MethodKey("metric.count")
    if type(params) is rules.ObserveMetric:
        return MethodKey(
            "metric.sum_zero" if params.metric.empty_rule == "zero" else "metric.observe"
        )
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
        return MethodKey(
            "state_rollup.linear"
            if params.method == "linear"
            else "state_rollup.weighted_mean"
            if params.method == "weighted_mean"
            else "state_rollup.ratio"
            if params.method == "ratio"
            else "state_rollup.count"
            if params.method == "count"
            else "state_rollup.sum_zero"
            if params.method == "sum_zero"
            else "state_rollup"
        )
    if type(params) is rules.PartsTransport:
        return MethodKey("parts_transport")
    if type(params) is rules.AssociationScore:
        return MethodKey("association.spearman")
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
    def persistent_state_kind(self) -> PersistentStateKind | None:
        """Return the connected durable state kind; absence grants no publication."""
        kinds: dict[MethodName, PersistentStateKind] = {
            "bind_project": "none",
            "metric.observe": "original_sum",
            "metric.sum_zero": "original_sum_zero",
            "state_rollup.sum_zero": "original_sum_zero",
            "metric.ratio": "original_ratio",
            "metric.linear": "original_linear",
            "state_rollup.linear": "original_linear",
            "state_rollup.ratio": "original_ratio",
            "metric.count": "original_count",
            "metric.weighted_mean": "original_weighted_mean",
            "state_rollup.weighted_mean": "original_weighted_mean",
            "state_rollup.count": "original_count",
            "state_rollup": "original_sum",
            "parts_transport": "none",
            "map_correspond": "none",
            "cell.difference": "difference",
            "row.sum": "row_sum",
            "row.count": "row_count",
            "row.count_defined": "row_count_defined",
            "row.mean": "row_mean",
            "association.spearman": "spearman",
        }
        return kinds.get(self.key.name)

    def validate_output_type(
        self, inputs: tuple[ValueType, ...], output: ValueType, params: rules.RuleParameters
    ) -> None:
        """Reject known type contradictions; exact physical metadata remains a runtime check."""
        from marivo.analysis.methods.physical import DecimalType, ScalarType

        name = self.key.name
        if name == "metric.count":
            if output != ScalarType("int64"):
                reject("int64 Entity count", repr(output), "Preserve the count result type.")
            return
        if name in ("metric.observe", "metric.sum_zero"):
            assert isinstance(params, rules.ObserveMetric)
            if output != ScalarType(params.amount_type):
                reject(
                    "the bound contribution amount type",
                    repr(output),
                    "Preserve the exact source schema type.",
                )
            return
        if name == "bind_project":
            assert isinstance(params, rules.BindProject)
            contract = (
                params.metric_contract
                if params.metric_contract is not None
                else params.field_contract
            )
            if contract is not None:
                logical_type = contract.logical_type
                known_scalars = {
                    "boolean": ScalarType("boolean"),
                    "string": ScalarType("string"),
                    "int64": ScalarType("int64"),
                    "float64": ScalarType("float64"),
                    "date": ScalarType("date"),
                    "timestamp": ScalarType("timestamp"),
                }
                declared_type = known_scalars.get(logical_type)
                if declared_type is not None and output != declared_type:
                    reject(
                        f"declared {logical_type} result type",
                        repr(output),
                        "Use the bound field or Metric type.",
                    )
                if logical_type == "decimal" and not isinstance(output, DecimalType):
                    reject(
                        "declared Decimal result type",
                        repr(output),
                        "Use a Decimal type and verify precision before consumption.",
                    )
            return
        if name in ("parts_transport", "map_correspond"):
            if any(value != output for value in inputs):
                reject(
                    "unchanged value type for transport/correspondence",
                    repr(output),
                    "Retain the exact input value type.",
                )
            return
        if name in (
            "association.spearman",
            "metric.ratio",
            "state_rollup.ratio",
            "metric.weighted_mean",
            "state_rollup.weighted_mean",
        ):
            expected: ValueType = ScalarType("float64")
        elif name in ("row.count", "row.count_defined"):
            expected = ScalarType("int64")
        elif any(isinstance(value, DecimalType) for value in inputs):
            if not isinstance(output, DecimalType):
                reject(
                    "an exact declared Decimal result type",
                    repr(output),
                    "Preserve Decimal typing and verify precision before consumption.",
                )
            return
        elif (
            name in ("row.mean", "row.weighted_mean", "cell.ratio")
            or ScalarType("float64") in inputs
        ):
            expected = ScalarType("float64")
        else:
            expected = ScalarType("int64")
        if output != expected:
            reject(f"result type {expected}", repr(output), "Use the method's exact result type.")

    @property
    def local_laws(self) -> tuple[str, ...]:
        """Registered comparison levels; state laws do not authorize graph rewrites."""
        if self.key.name == "parts_transport":
            return ("L1",)
        if self.key.name == "map_correspond":
            return ("L7",)
        if self.key.name in (
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.linear",
        ):
            return ("L8", "L9")
        return ()

    @property
    def rule(self) -> rules.RuleId:
        name = self.key.name
        if name in ("cell.difference", "cell.ratio"):
            return "cell_derive@v1"
        if name in ("row.sum", "row.mean", "row.count", "row.count_defined", "row.weighted_mean"):
            return "row_state@v1"
        if name == "metric.ratio":
            return "original_reduce@v1"
        if name == "metric.linear":
            return "occurrence_combine@v1"
        if name in (
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.linear",
        ):
            return "original_reduce@v1"
        if name == "association.spearman":
            return "association_score@v1"
        if name in (
            "bind_project",
            "metric.observe",
            "metric.count",
            "metric.sum_zero",
            "metric.weighted_mean",
        ):
            return "bind_project@v1"
        if name == "map_correspond":
            return "map_correspond@v1"
        return "parts_transport@v1"

    @property
    def cell_policy(
        self,
    ) -> Literal["strict", "count_all", "defined_only", "input_owned", "pair_null"]:
        if self.key.name == "association.spearman":
            return "pair_null"
        if self.key.name == "row.count":
            return "count_all"
        if self.key.name == "row.count_defined":
            return "defined_only"
        if self.rule in ("cell_derive@v1", "row_state@v1"):
            return "strict"
        return "input_owned"

    @property
    def unit_policy(
        self,
    ) -> Literal["preserve", "count", "mean", "difference", "ratio", "coefficient"]:
        if self.key.name in ("row.count", "row.count_defined"):
            return "count"
        if self.key.name in ("row.mean", "row.weighted_mean"):
            return "mean"
        if self.key.name == "cell.difference":
            return "difference"
        if self.key.name == "cell.ratio":
            return "ratio"
        if self.key.name == "association.spearman":
            return "coefficient"
        return "preserve"

    @property
    def empty_cell_reasons(
        self,
    ) -> tuple[tuple[Literal["null", "undefined", "unknown"], tuple[str, ...]], ...]:
        if self.key.name == "row.mean":
            return (("undefined", ("empty_mean",)),)
        if self.key.name == "bind_project":
            return (("null", ("source_null",)),)
        if self.key.name == "association.spearman":
            return (
                ("undefined", ("insufficient_pairs", "constant_a", "constant_b", "constant_both")),
            )
        if self.key.name in ("metric.ratio", "state_rollup.ratio"):
            # Each component retains its own empty rule. Null operands propagate;
            # defined operands with a zero denominator produce Undefined.
            return (
                ("null", ("empty_contribution",)),
                ("undefined", ("zero_denominator",)),
            )
        if self.key.name in ("metric.weighted_mean", "state_rollup.weighted_mean"):
            return (("null", ("empty_contribution", "zero_weight_sum")),)
        if self.key.name in ("metric.linear", "state_rollup.linear"):
            return (("null", ("empty_contribution",)),)
        if self.key.name in ("state_rollup", "metric.observe"):
            return (("null", ("empty_contribution",)),)
        return ()

    @property
    def required_parts(self) -> tuple[PartRole, ...]:
        if self.key.name in (
            "metric.observe",
            "metric.count",
            "metric.sum_zero",
            "metric.weighted_mean",
        ):
            return ("subject",)
        if self.key.name in (
            "metric.ratio",
            "metric.linear",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.linear",
        ):
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
        if self.key.name in (
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.linear",
            "metric.ratio",
            "metric.linear",
            "metric.sum_zero",
            "metric.observe",
            "metric.count",
            "metric.weighted_mean",
        ):
            return ("source.contribution_partition@v1", "source.complete_coverage@v1")
        if self.key.name == "association.spearman":
            return ("source.exact_pairing@v1", "source.finite_numeric@v1")
        return ()

    @property
    def output_parts(self) -> tuple[PartRole, ...]:
        if self.rule == "cell_derive@v1":
            return ("current_endpoint", "baseline_endpoint")
        if self.rule == "row_state@v1":
            return ("row_state",)
        if self.key.name in (
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.linear",
            "metric.ratio",
            "metric.linear",
            "metric.sum_zero",
            "metric.observe",
            "metric.count",
            "metric.weighted_mean",
        ):
            return ("original_state", "coverage")
        if self.key.name == "association.spearman":
            return ("pair_counts",)
        return ()

    @property
    def original_state_method(self) -> str:
        """The only connected original-state contract, independent of row sum."""
        if self.key.name not in (
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.linear",
        ):
            reject(
                "an original-state method", str(self.key), "Use state_rollup for original state."
            )
        return (
            "linear@v1"
            if self.key.name == "state_rollup.linear"
            else "weighted_mean@v1"
            if self.key.name == "state_rollup.weighted_mean"
            else "ratio@v1"
            if self.key.name == "state_rollup.ratio"
            else "count@v1"
            if self.key.name == "state_rollup.count"
            else "sum_zero@v1"
            if self.key.name == "state_rollup.sum_zero"
            else "sum@v1"
        )

    @property
    def state_components(self) -> tuple[str, ...]:
        if self.key.name in ("metric.linear", "state_rollup.linear"):
            # Component names and signs belong to the bound occurrence tuple.
            return ()
        if self.key.name in ("metric.weighted_mean", "state_rollup.weighted_mean"):
            return ("weighted_numerator", "weight_sum", "non_null_pair_count", "row_count")
        if self.key.name in ("metric.ratio", "state_rollup.ratio"):
            return (
                "numerator_sum",
                "numerator_non_null_count",
                "denominator_sum",
                "denominator_non_null_count",
            )
        if self.key.name in (
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.linear",
        ):
            return (
                ("count",) if self.key.name == "state_rollup.count" else ("sum", "non_null_count")
            )
        if self.key.name == "row.mean":
            return ("sum", "count")
        if self.key.name == "row.weighted_mean":
            return ("weighted_sum", "weight_sum")
        if self.key.name == "association.spearman":
            return (
                "input_observation_count",
                "matched_observation_count",
                "null_pair_count",
                "complete_pair_count",
            )
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
        if type(params) in (rules.ObserveMetric, rules.ObserveCount, rules.ObserveWeightedMean):
            assert isinstance(
                params, (rules.ObserveMetric, rules.ObserveCount, rules.ObserveWeightedMean)
            )
            return rules._observe_metric(inputs, params)
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
        if type(params) is rules.OriginalRatio:
            return rules._original_ratio(inputs, params)
        if type(params) is rules.OccurrenceCombine:
            return rules._occurrence_combine(inputs, params)
        if type(params) is rules.OriginalReduce:
            return rules._original_reduce(inputs, params)
        if type(params) is rules.PartsTransport:
            return rules._parts_transport(inputs, params)
        if type(params) is rules.AssociationScore:
            return rules._association_score(inputs, params)
        reject("closed method parameters", repr(params), "Use a registered parameter variant.")


CONNECTED_METHODS = (
    MethodSemantics(MethodKey("metric.sum_zero"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.ratio"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.linear"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.linear"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.ratio"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.sum_zero"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.count"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.weighted_mean"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.weighted_mean"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.count"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.observe"), "analysis.core.rules"),
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
    MethodSemantics(MethodKey("association.spearman"), "analysis.core.rules"),
)
