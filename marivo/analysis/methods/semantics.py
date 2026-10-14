"""The semantic owner of methods already connected to the private algebra.

Rule derivations carry the bound Pre, quantity, Cell policy, state, parts and
obligations. Physical declarations never replace or amend those derivations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, get_args

from marivo.analysis.core import rules
from marivo.analysis.core.model import CheckId, PartRole, Quantity, Signature, StatisticalWeightPart
from marivo.analysis.methods.descriptor import (
    BY_NAME,
    DESCRIPTORS,
    CellPolicy,
    EmptyCellReasons,
    MethodDescriptor,
    MethodName,
    PersistentStateKind,
    UnitPolicy,
    method_name_for,
)
from marivo.analysis.methods.errors import reject

if TYPE_CHECKING:
    from marivo.analysis.methods.physical import ValueType


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
                "Use a connected method; datasource terminals are not methods.",
            )

    def __str__(self) -> str:
        return f"{self.name}@v{self.version}"


def key_for_parameters(params: rules.RuleParameters) -> MethodKey:
    """Map closed parameter variants to exactly one concrete method identity."""
    name = method_name_for(params)
    if name is None:
        reject(
            "closed method parameters", repr(params), "Use an exact registered parameter variant."
        )
    return MethodKey(name)


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
    def descriptor(self) -> MethodDescriptor:
        return BY_NAME[self.key.name]

    @property
    def persistent_state_kind(self) -> PersistentStateKind | None:
        """Return the connected durable state kind; absence grants no publication."""
        return self.descriptor.state_kind

    def validate_output_type(
        self, inputs: tuple[ValueType, ...], output: ValueType, params: rules.RuleParameters
    ) -> None:
        """Reject known type contradictions; exact physical metadata remains a runtime check."""
        from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType

        name = self.key.name
        if isinstance(
            params,
            (rules.AssociationFit, rules.AssociationRead, rules.ForecastFit, rules.ForecastRead),
        ):
            from marivo.analysis.methods.deviation_numeric import unit_type
            from marivo.analysis.methods.deviation_physical import parse_type

            statistical_type = (
                ScalarType("float64")
                if isinstance(params, rules.AssociationFit)
                else ScalarType("boolean" if params.field == "selected" else "float64")
                if isinstance(params, rules.AssociationRead)
                else unit_type(parse_type(params.input_type))
            )
            if output != statistical_type:
                reject(
                    "owned statistical field carrier",
                    repr(output),
                    "Read the original owned field.",
                )
            return
        if isinstance(params, (rules.TimeRuns, rules.TimeRunRead)):
            from marivo.analysis.methods.runs_physical import output_type as run_output_type

            if output != run_output_type(
                params.field if isinstance(params, rules.TimeRunRead) else "count"
            ):
                reject("the owned run field type", repr(output), "Read an owned run field.")
            return
        if isinstance(params, (rules.DeviationFit, rules.DeviationRead)):
            from marivo.analysis.methods.deviation_physical import (
                output_type as deviation_output_type,
            )
            from marivo.analysis.methods.deviation_physical import parse_type

            expected_deviation = (
                ScalarType("float64")
                if isinstance(params, rules.DeviationFit)
                else deviation_output_type(params.field, parse_type(params.input_type))
            )
            if output != expected_deviation:
                reject(
                    "the exact owned deviation field type",
                    repr(output),
                    "Preserve the fitted output carrier.",
                )
            return
        if isinstance(params, rules.PreparedObservation):
            self.validate_output_type(inputs[1:], output, params.observation)
            return
        if isinstance(params, rules.AttributionDerive):
            if output.name != params.value_type:
                reject(
                    "the exact allocation result type",
                    repr(output),
                    "Preserve the original numeric carrier.",
                )
            return
        if isinstance(params, (rules.DisplayRank, rules.DisplayTable)):
            declared_types = (
                (params.value_type, *params.partition_types)
                if isinstance(params, rules.DisplayRank)
                else params.types
            )
            if (
                tuple(value.name for value in inputs) != declared_types
                or output != inputs[0]
                or (
                    isinstance(params, rules.DisplayRank)
                    and isinstance(inputs[0], ScalarType)
                    and inputs[0].name not in ("int64", "float64")
                )
            ):
                reject(
                    "exact display input type",
                    repr(output),
                    "Preserve the ranked numeric or ordered terminal column types.",
                )
            return
        if isinstance(params, rules.PartsTransport) and params.display_view == "ranks":
            if output != ScalarType("int64"):
                reject("int64 ranks", repr(output), "Use the typed ranks view.")
            return
        if name == "domain.cohort":
            if output != inputs[0]:
                reject("the target domain physical type", repr(output), "Preserve the target type.")
            return
        if name in ("parts_transport", "map_correspond"):
            retained_inputs = inputs[:1] if isinstance(params, rules.PartsTransport) else inputs
            if any(value != output for value in retained_inputs):
                reject(
                    "unchanged value type for transport/correspondence",
                    repr(output),
                    "Retain the exact input value type.",
                )
            return
        if isinstance(params, rules.ReferenceDerive):
            from marivo.analysis.methods.references import reference_type

            if output != reference_type(params.kind, inputs[0], inputs[1]):
                reject(
                    "the registered reference output type",
                    repr(output),
                    "Preserve exact input families and finish once.",
                )
            return
        if isinstance(params, rules.ComparisonRead):
            from marivo.analysis.methods.comparison import output_type

            view_type = (
                inputs[0]
                if params.field == "absolute"
                else output_type("relative_change", inputs[0], inputs[0])
            )
            if output != view_type:
                reject(
                    "the owned comparison view type",
                    repr(output),
                    "Read the frozen comparison carrier.",
                )
            return
        if isinstance(params, rules.CellDerive):
            from marivo.analysis.methods.comparison import output_type

            if params.value_policy == "duration_ratio_unknown" and not (
                params.method == "ratio"
                and all(isinstance(value, DurationType) for value in inputs)
            ):
                reject(
                    "Duration ratio operands for Unknown propagation",
                    repr(inputs),
                    "Preserve the original Duration endpoints.",
                )

            try:
                expected_comparison = output_type(params.method, inputs[0], inputs[1])
            except (ValueError, IndexError) as error:
                reject(
                    "two homogeneous comparison operands",
                    str(error),
                    "Bind matching numeric types and units.",
                )
            if isinstance(params, rules.CellCompare) and (
                params.absolute_type != expected_comparison.name
                or params.relative_type != output_type("relative_change", inputs[0], inputs[1]).name
            ):
                reject(
                    "both exact comparison carriers",
                    repr(params),
                    "Preserve the original operand types.",
                )
            if output != expected_comparison:
                reject(
                    "the exact comparison output type",
                    repr(output),
                    "Preserve the registered result type.",
                )
            return
        if name == "time.product":
            if len(inputs) != 1 or inputs[0] != output:
                reject("unchanged member type", repr(output), "Preserve the member identity type.")
            return
        if name == "group.attach":
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
            name == "metric.mean"
            and isinstance(params, rules.ObserveMetric)
            and params.amount_type.startswith("decimal(")
        ):
            if output.name != params.amount_type:
                reject(
                    "Ibis mean result type", repr(output), "Preserve the bound input Decimal type."
                )
            return
        if name == "state_rollup.mean" and len(inputs) == 1 and isinstance(inputs[0], DecimalType):
            if output != inputs[0]:
                reject(
                    "retained mean result type", repr(output), "Preserve the captured mean type."
                )
            return
        if name == "metric.linear":
            from marivo.analysis.methods.native_numeric import linear_type

            if output != linear_type(inputs):
                reject(
                    "Ibis linear result type",
                    repr(output),
                    "Preserve the inferred component result type.",
                )
            return
        if name == "state_rollup.linear" and inputs == (output,):
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
        if (
            name in ("row.sum", "row.mean")
            and isinstance(params, rules.RowState)
            and len(inputs) == 1
            and isinstance(inputs[0], DecimalType)
        ):
            expected_row = DecimalType(
                38, max(inputs[0].scale, 6) if name == "row.mean" else inputs[0].scale
            )
            if output != expected_row:
                reject(
                    "the exact Decimal row-statistic finish type",
                    repr(output),
                    "Preserve the input scale for sums and finish means once at the declared scale.",
                )
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
        return self.descriptor.local_laws

    @property
    def rule(self) -> rules.RuleId:
        return self.descriptor.rule

    @property
    def cell_policy(self) -> CellPolicy:
        return self.descriptor.cell_policy

    @property
    def unit_policy(self) -> UnitPolicy:
        return self.descriptor.unit_policy

    @property
    def empty_cell_reasons(self) -> EmptyCellReasons:
        return self.descriptor.empty_cell_reasons

    @property
    def required_parts(self) -> tuple[PartRole, ...]:
        return self.descriptor.required_parts

    @property
    def required_checks(self) -> tuple[CheckId, ...]:
        """Minimum checker coverage; invocation-specific checks remain in Pre."""
        return self.descriptor.required_checks

    @property
    def output_parts(self) -> tuple[PartRole, ...]:
        return self.descriptor.output_parts

    @property
    def original_state_method(self) -> str:
        """The only connected original-state contract, independent of row sum."""
        method = self.descriptor.original_state_method
        if method is None:
            reject(
                "an original-state method", str(self.key), "Use state_rollup for original state."
            )
        return method

    @property
    def state_components(self) -> tuple[str, ...]:
        return self.descriptor.state_components

    def derive(
        self, inputs: tuple[Signature, ...], params: rules.RuleParameters
    ) -> rules.RuleDerivation:
        """Validate exact inputs and apply the sole owning semantic rule."""
        if isinstance(
            params,
            (rules.AssociationFit, rules.AssociationRead, rules.ForecastFit, rules.ForecastRead),
        ):
            from marivo.analysis.core.statistical_rules import derive as derive_statistics

            return derive_statistics(inputs, params)
        if isinstance(params, (rules.TimeRuns, rules.TimeRunRead)):
            from marivo.analysis.core.runs_rules import derive as derive_runs

            return derive_runs(inputs, params)
        if isinstance(params, (rules.DeviationFit, rules.DeviationRead)):
            from marivo.analysis.core.deviation_rules import derive as derive_deviation

            return derive_deviation(inputs, params)
        if isinstance(params, rules.PreparedObservation):
            return rules._prepared_observation(inputs, params)
        if key_for_parameters(params) != self.key:
            reject(str(self.key), str(key_for_parameters(params)), "Use this method's parameters.")
        if type(inputs) is not tuple or any(type(item) is not Signature for item in inputs):
            reject("immutable exact Signature inputs", repr(inputs), "Bind core signatures.")
        if (
            isinstance(params, (rules.CellDerive, rules.RowState))
            and params.value_policy != self.cell_policy
            and not (
                isinstance(params, rules.CellDerive)
                and params.method == "ratio"
                and params.value_policy == "duration_ratio_unknown"
            )
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
        if type(params) is rules.AttachCategory:
            return rules._attach_category(inputs, params)
        if type(params) is rules.BindProject:
            return rules._bind_project(inputs, params)
        if type(params) is rules.MapCorrespond:
            return rules._map_correspond(inputs, params)
        if type(params) is rules.CellCompare:
            return rules._cell_compare(inputs, params)
        if type(params) is rules.ComparisonRead:
            return rules._comparison_read(inputs, params)
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
        if isinstance(params, (rules.DisplayRank, rules.DisplayTable)):
            return rules._display(inputs, params)
        if type(params) is rules.AttributionDerive:
            return rules._attribution(inputs, params)
        if type(params) is rules.ReferenceDerive:
            return rules._reference(inputs, params)
        if type(params) is rules.PartsTransport:
            return rules._parts_transport(inputs, params)
        if type(params) is rules.AssociationScore:
            return rules._association_score(inputs, params)
        reject("closed method parameters", repr(params), "Use a registered parameter variant.")


CONNECTED_METHODS = tuple(
    MethodSemantics(MethodKey(item.name), "analysis.core.rules") for item in DESCRIPTORS
)


def quantity_cell_reasons(quantity: Quantity | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Retain Cell reasons from the same native method owner as the input quantity."""
    if quantity is None:
        return ()
    method = quantity.method_version.removesuffix("@v1")
    names = {
        "sum": "metric.observe",
        "sum_zero": "metric.sum_zero",
        "count": "metric.count",
        "mean": "metric.mean",
        "weighted_mean": "metric.weighted_mean",
        "ratio": "metric.ratio",
        "linear": "metric.linear",
    }
    owner = names.get(method, method)
    semantics = next((item for item in CONNECTED_METHODS if item.key.name == owner), None)
    if semantics is None:
        reject(
            "a quantity with a registered Cell policy",
            method,
            "Use the frozen original quantity or registered reference result.",
        )
    return semantics.empty_cell_reasons


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
