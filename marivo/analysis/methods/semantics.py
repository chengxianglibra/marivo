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
    "time.product",
    "group.attach",
    "group.complete",
    "bind_project",
    "metric.distinct",
    "metric.approx_distinct",
    "metric.quantile",
    "metric.approx_quantile",
    "metric.min",
    "metric.max",
    "metric.observe",
    "metric.mean",
    "metric.fold",
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
    "row.min",
    "row.max",
    "row.count",
    "row.count_defined",
    "row.weighted_mean",
    "state_rollup.min",
    "state_rollup.max",
    "state_rollup",
    "state_rollup.mean",
    "state_rollup.fold",
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
    "original_min",
    "original_max",
    "original_sum",
    "original_mean",
    "original_fold",
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
    "row_min",
    "row_max",
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
    if type(params) is rules.TimeProduct:
        return MethodKey("time.product")
    if type(params) is rules.CompleteGroups:
        return MethodKey("group.complete")
    if type(params) is rules.AttachCategory:
        return MethodKey("group.attach")
    if type(params) is rules.OriginalRatio:
        return MethodKey("metric.ratio")
    if type(params) is rules.OccurrenceCombine:
        return MethodKey("metric.linear")
    if type(params) is rules.ObserveWeightedMean:
        return MethodKey("metric.weighted_mean")
    if type(params) is rules.ObserveCount:
        return MethodKey("metric.count")
    if type(params) is rules.ObserveMetric:
        if params.method == "min":
            return MethodKey("metric.min")
        if params.method == "max":
            return MethodKey("metric.max")
        if params.method == "count_distinct":
            return MethodKey("metric.distinct")
        if params.method == "approx_count_distinct":
            return MethodKey("metric.approx_distinct")
        if params.method in ("median", "percentile"):
            return MethodKey("metric.quantile")
        if params.method in ("approx_median", "approx_percentile"):
            return MethodKey("metric.approx_quantile")
        if params.fold is not None:
            return MethodKey("metric.fold")
        if params.method == "mean":
            return MethodKey("metric.mean")
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
        for name in (
            "row.sum",
            "row.min",
            "row.max",
            "row.mean",
            "row.count",
            "row.count_defined",
            "row.weighted_mean",
        ):
            if name == f"row.{params.method}":
                return MethodKey(name)
    if type(params) is rules.OriginalReduce:
        if params.method == "min":
            return MethodKey("state_rollup.min")
        if params.method == "max":
            return MethodKey("state_rollup.max")
        if params.method == "fold":
            return MethodKey("state_rollup.fold")
        if params.method == "mean":
            return MethodKey("state_rollup.mean")
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
            "time.product": "none",
            "group.attach": "none",
            "group.complete": "none",
            "bind_project": "none",
            "metric.distinct": "none",
            "metric.approx_distinct": "none",
            "metric.quantile": "none",
            "metric.approx_quantile": "none",
            "metric.min": "original_min",
            "metric.max": "original_max",
            "state_rollup.min": "original_min",
            "state_rollup.max": "original_max",
            "metric.observe": "original_sum",
            "metric.mean": "original_mean",
            "metric.fold": "original_fold",
            "state_rollup.fold": "original_fold",
            "state_rollup.mean": "original_mean",
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
            "row.min": "row_min",
            "row.max": "row_max",
            "association.spearman": "spearman",
        }
        return kinds.get(self.key.name)

    def validate_output_type(
        self, inputs: tuple[ValueType, ...], output: ValueType, params: rules.RuleParameters
    ) -> None:
        """Reject known type contradictions; exact physical metadata remains a runtime check."""
        from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType

        name = self.key.name
        if name == "time.product":
            if len(inputs) != 1 or inputs[0] != output:
                reject("unchanged member type", repr(output), "Preserve the member identity type.")
            return
        if name in ("group.attach", "group.complete"):
            if len(inputs) != 2 or inputs[0] != output:
                reject(
                    "the receiver's unchanged value type",
                    repr(output),
                    "Preserve the classified relation type.",
                )
            return
        if (
            isinstance(params, (rules.ObserveMetric, rules.ObserveWeightedMean))
            and params.amount_type.startswith("interval(")
            and name not in ("metric.distinct", "metric.approx_distinct")
        ):
            if not isinstance(output, DurationType) or output.name != params.amount_type:
                reject(
                    "unchanged fixed Duration unit",
                    repr(output),
                    "Preserve the physical tick unit.",
                )
            return
        if any(isinstance(value, DurationType) for value in inputs):
            expected_duration = (
                ScalarType("float64")
                if name in ("metric.ratio", "state_rollup.ratio")
                else ScalarType("int64")
                if name in ("row.count", "row.count_defined")
                else inputs[0]
            )
            if any(value != inputs[0] for value in inputs) or output != expected_duration:
                reject(
                    "matching fixed Duration units",
                    repr(output),
                    "Use exact same-unit numeric operations.",
                )
            return
        if name in ("metric.count", "metric.distinct", "metric.approx_distinct"):
            if output != ScalarType("int64"):
                reject("int64 Entity count", repr(output), "Preserve the count result type.")
            return
        if name in ("metric.observe", "metric.sum_zero", "metric.min", "metric.max"):
            assert isinstance(params, rules.ObserveMetric)
            if output.name != params.amount_type and not (
                name in ("metric.observe", "metric.sum_zero")
                and isinstance(output, DecimalType)
                and output.precision == 38
                and params.amount_type.startswith("decimal(")
                and params.amount_type.endswith(f",{output.scale})")
            ):
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
            retained_inputs = (
                inputs[:1]
                if isinstance(params, rules.PartsTransport) and params.external_predicate
                else inputs
            )
            if any(value != output for value in retained_inputs):
                reject(
                    "unchanged value type for transport/correspondence",
                    repr(output),
                    "Retain the exact input value type.",
                )
            return
        if (
            name == "metric.quantile"
            and isinstance(params, rules.ObserveMetric)
            and params.amount_type.startswith("decimal(")
        ):
            if not isinstance(output, DecimalType):
                reject(
                    "Decimal quantile result", repr(output), "Preserve the declared decimal scale."
                )
            return
        if (
            name in ("metric.mean", "metric.weighted_mean")
            and isinstance(params, (rules.ObserveMetric, rules.ObserveWeightedMean))
            and params.amount_type.startswith("decimal(")
        ):
            scale = int(params.amount_type.removesuffix(")").split(",")[1])
            if output != DecimalType(38, max(scale, 6)):
                reject(
                    "Decimal mean finish scale",
                    repr(output),
                    "Preserve the declared single-round finish type.",
                )
            return
        if name in (
            "metric.ratio",
            "state_rollup.ratio",
            "state_rollup.mean",
            "state_rollup.weighted_mean",
        ) and all(isinstance(value, DecimalType) for value in inputs):
            if output != DecimalType(
                38, max(6, *(value.scale for value in inputs if isinstance(value, DecimalType)))
            ):
                reject(
                    "Decimal ratio finish scale",
                    repr(output),
                    "Preserve the exact component scales.",
                )
            return
        if name == "metric.fold" and isinstance(params, rules.ObserveMetric):
            if isinstance(output, DurationType) and params.amount_type == output.name:
                expected_fold: ValueType = output
            elif params.amount_type.startswith("decimal("):
                scale = int(params.amount_type.removesuffix(")").split(",")[1])
                expected_fold = DecimalType(38, max(scale, 6) if params.fold == "mean" else scale)
            else:
                expected_fold = ScalarType(
                    "float64"
                    if params.fold == "mean" or params.amount_type == "float64"
                    else "int64"
                )
            if output != expected_fold:
                reject("typed fold output", repr(output), "Preserve sample type and mean rounding.")
            return
        if name == "state_rollup.fold":
            if inputs != (output,):
                reject("unchanged fold result type", repr(output), "Preserve the bound fold type.")
            return
        if name in (
            "association.spearman",
            "metric.quantile",
            "metric.approx_quantile",
            "metric.ratio",
            "state_rollup.ratio",
            "metric.weighted_mean",
            "metric.mean",
            "metric.fold",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
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
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
        ):
            return ("L8", "L9")
        return ()

    @property
    def rule(self) -> rules.RuleId:
        name = self.key.name
        if name in ("cell.difference", "cell.ratio"):
            return "cell_derive@v1"
        if name in (
            "row.sum",
            "row.min",
            "row.max",
            "row.mean",
            "row.count",
            "row.count_defined",
            "row.weighted_mean",
        ):
            return "row_state@v1"
        if name == "metric.ratio":
            return "original_reduce@v1"
        if name == "metric.linear":
            return "occurrence_combine@v1"
        if name in (
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
        ):
            return "original_reduce@v1"
        if name == "association.spearman":
            return "association_score@v1"
        if name in (
            "bind_project",
            "metric.distinct",
            "metric.approx_distinct",
            "metric.quantile",
            "metric.approx_quantile",
            "metric.min",
            "metric.max",
            "metric.observe",
            "metric.count",
            "metric.sum_zero",
            "metric.weighted_mean",
            "metric.mean",
            "metric.fold",
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
        if self.key.name in ("row.mean", "row.min", "row.max"):
            return (("undefined", ("empty_" + self.key.name.removeprefix("row."),)),)
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
        if self.key.name in (
            "metric.quantile",
            "metric.approx_quantile",
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "metric.min",
            "metric.max",
            "metric.observe",
            "metric.mean",
            "state_rollup.mean",
            "metric.fold",
            "state_rollup.fold",
        ):
            return (("null", ("empty_contribution",)),)
        return ()

    @property
    def required_parts(self) -> tuple[PartRole, ...]:
        if self.key.name in (
            "metric.distinct",
            "metric.approx_distinct",
            "metric.quantile",
            "metric.approx_quantile",
        ):
            return ("subject",)
        if self.key.name in (
            "metric.min",
            "metric.max",
            "metric.observe",
            "metric.count",
            "metric.sum_zero",
            "metric.weighted_mean",
            "metric.mean",
            "metric.fold",
        ):
            return ("subject",)
        if self.key.name in (
            "metric.ratio",
            "metric.linear",
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
        ):
            return ("original_state", "coverage")
        if self.key.name == "row.weighted_mean":
            return ("statistical_weight",)
        return ()

    @property
    def required_checks(self) -> tuple[CheckId, ...]:
        """Minimum checker coverage; invocation-specific checks remain in Pre."""
        if self.key.name in (
            "metric.distinct",
            "metric.approx_distinct",
            "metric.quantile",
            "metric.approx_quantile",
        ):
            return ("source.contribution_partition@v1", "source.complete_coverage@v1")
        if self.rule == "cell_derive@v1":
            return ("source.exact_pairing@v1", "source.finite_numeric@v1")
        if self.key.name == "row.count_defined":
            return ("source.cell_policy@v1",)
        if self.rule == "row_state@v1" and self.key.name != "row.count":
            return ("source.finite_numeric@v1",)
        if self.key.name in (
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
            "metric.ratio",
            "metric.linear",
            "metric.sum_zero",
            "metric.min",
            "metric.max",
            "metric.observe",
            "metric.count",
            "metric.weighted_mean",
            "metric.mean",
            "metric.fold",
        ):
            return ("source.contribution_partition@v1", "source.complete_coverage@v1")
        if self.key.name == "association.spearman":
            return ("source.exact_pairing@v1", "source.finite_numeric@v1")
        return ()

    @property
    def output_parts(self) -> tuple[PartRole, ...]:
        if self.key.name in (
            "metric.distinct",
            "metric.approx_distinct",
            "metric.quantile",
            "metric.approx_quantile",
        ):
            return ("coverage",)
        if self.rule == "cell_derive@v1":
            return ("current_endpoint", "baseline_endpoint")
        if self.rule == "row_state@v1":
            return ("row_state",)
        if self.key.name in (
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
            "metric.ratio",
            "metric.linear",
            "metric.sum_zero",
            "metric.min",
            "metric.max",
            "metric.observe",
            "metric.count",
            "metric.weighted_mean",
            "metric.mean",
            "metric.fold",
        ):
            return ("original_state", "coverage")
        if self.key.name == "association.spearman":
            return ("pair_counts",)
        return ()

    @property
    def original_state_method(self) -> str:
        """The only connected original-state contract, independent of row sum."""
        if self.key.name not in (
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
        ):
            reject(
                "an original-state method", str(self.key), "Use state_rollup for original state."
            )
        if self.key.name in ("state_rollup.min", "state_rollup.max"):
            return self.key.name.removeprefix("state_rollup.") + "@v1"
        return (
            "fold@v1"
            if self.key.name == "state_rollup.fold"
            else "mean@v1"
            if self.key.name == "state_rollup.mean"
            else "linear@v1"
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
        if self.key.name in ("metric.min", "metric.max", "state_rollup.min", "state_rollup.max"):
            return (self.key.name.rsplit(".", 1)[1], "non_null_count")
        if self.key.name in ("metric.fold", "state_rollup.fold"):
            return ("samples", "fold_kind")
        if self.key.name in ("metric.mean", "state_rollup.mean"):
            return ("sum", "non_null_count", "row_count")
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
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
        ):
            return (
                ("count",) if self.key.name == "state_rollup.count" else ("sum", "non_null_count")
            )
        if self.key.name in ("row.min", "row.max"):
            return (self.key.name.removeprefix("row."), "count")
        if self.key.name in ("row.sum", "row.mean"):
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
        if type(params) is rules.TimeProduct:
            return rules._time_product(inputs, params)
        if type(params) is rules.CompleteGroups:
            return rules._complete_groups(inputs, params)
        if type(params) is rules.AttachCategory:
            return rules._attach_category(inputs, params)
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
    MethodSemantics(MethodKey("metric.min"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.max"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.min"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.max"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.distinct"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.approx_distinct"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.quantile"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.approx_quantile"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.fold"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.fold"), "analysis.core.rules"),
    MethodSemantics(MethodKey("metric.mean"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup.mean"), "analysis.core.rules"),
    MethodSemantics(MethodKey("group.complete"), "analysis.core.rules"),
    MethodSemantics(MethodKey("time.product"), "analysis.core.rules"),
    MethodSemantics(MethodKey("group.attach"), "analysis.core.rules"),
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
    MethodSemantics(MethodKey("row.min"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.max"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.count"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.count_defined"), "analysis.core.rules"),
    MethodSemantics(MethodKey("row.weighted_mean"), "analysis.core.rules"),
    MethodSemantics(MethodKey("state_rollup"), "analysis.core.rules"),
    MethodSemantics(MethodKey("parts_transport"), "analysis.core.rules"),
    MethodSemantics(MethodKey("association.spearman"), "analysis.core.rules"),
)


def observation_disclosure(
    method_version: str, value_type: ValueType
) -> tuple[tuple[str, str], ...]:
    """Describe the qualified source algorithm without promising exact arithmetic."""
    algorithms = {
        "count_distinct@v1": "DuckDB COUNT(DISTINCT); exact identity count",
        "approx_count_distinct@v1": "DuckDB APPROX_COUNT_DISTINCT; HyperLogLog; no declared error bound",
        "median@v1": "DuckDB QUANTILE_CONT; continuous linear interpolation; q=0.5",
        "percentile@v1": "DuckDB QUANTILE_CONT; continuous linear interpolation; q owned by Metric",
        "approx_median@v1": "DuckDB APPROX_QUANTILE; T-Digest; q=0.5; no declared error bound",
        "approx_percentile@v1": "DuckDB APPROX_QUANTILE; T-Digest; q owned by Metric; no declared error bound",
    }
    algorithm = algorithms.get(method_version)
    if algorithm is None:
        return ()
    facts: tuple[tuple[str, str], ...] = (
        ("algorithm", algorithm),
        ("output_type", value_type.name),
    )
    if "median" in method_version or "percentile" in method_version:
        facts += (
            (
                "numeric_precision",
                "Source-native SQL arithmetic; large integers may lose precision and Decimal interpolation retains source scale; no local recomputation.",
            ),
        )
    return facts
