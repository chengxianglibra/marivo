"""Capture provider-qualified Shapley inputs and approximation facts."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal, TypeAlias

from typing_extensions import override

from marivo.analysis.model.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.method_values import (
    _count,
    _digest_identity,
    _FactRecord,
    _fail,
    _short,
    _text,
)

_StateRoute: TypeAlias = Literal[
    "typed_sufficient_state/v1", "typed_value_frequency/v1", "typed_sufficient_frequency/v1"
]


def _reducer_kind(kind: str, expected: str, reducer: str, allowed: tuple[str, ...]) -> bool:
    return kind == expected and reducer in allowed


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyGame(_FactRecord):
    """Describe a fixed replacement game without retaining its executable formula.

    Read ``metric.method.game`` after attribution. Formula identities are SHA256
    digests; proof clauses describe checks over captured states. Neither can
    reopen a source or reconstruct the original Metric. Summary reconstruction
    has no promised bias bound; permutation standard error excludes that bias.
    """

    kind: Literal["group_replacement"]
    semantics_version: Literal["group_replacement/v1"]
    current_formula_id: str
    baseline_formula_id: str
    player_domain: Literal["joint_key_union/v1"]
    domain_policy: Literal["finite_interval_demand/v1"]
    domain_proof: tuple[str, ...]
    metric_approximation: Literal["exact_metric/v1", "summary_reconstruction/v1"]
    standard_error: Literal["permutation_mean/v1"]
    closure_policy: Literal["typed_marginal_float64_sum/v1"]

    def __post_init__(self) -> None:
        policies = (
            self.kind,
            self.semantics_version,
            self.player_domain,
            self.domain_policy,
            self.standard_error,
            self.closure_policy,
        )
        if policies != (
            "group_replacement",
            "group_replacement/v1",
            "joint_key_union/v1",
            "finite_interval_demand/v1",
            "permutation_mean/v1",
            "typed_marginal_float64_sum/v1",
        ) or self.metric_approximation not in ("exact_metric/v1", "summary_reconstruction/v1"):
            _fail(
                "closed replacement, domain, approximation, error and closure policies",
                repr(self.kind),
            )
        _digest_identity(self.current_formula_id)
        _digest_identity(self.baseline_formula_id)
        if type(self.domain_proof) is not tuple or not self.domain_proof:
            _fail("nonempty immutable captured domain certificate clauses", repr(self.domain_proof))
        for clause in self.domain_proof:
            _text(clause)

    @override
    def _summary(self) -> str:
        return f"kind=group_replacement, formula={self.current_formula_id[:12]}, proof_clauses={len(self.domain_proof)}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyQueryGame(_FactRecord):
    """Describe a replacement game evaluated through independent native reads.

    Only evaluated coalitions were checked. Observed marginal standard errors
    cannot separate permutation variation from changes between source reads.
    Formula identities are inert digests, without source or replay authority.
    """

    kind: Literal["group_replacement"]
    semantics_version: Literal["group_replacement/v1"]
    current_formula_id: str
    baseline_formula_id: str
    player_domain: Literal["joint_key_union/v1"]
    input_consistency: Literal["independent_reads/v1"]
    domain_policy: Literal["evaluated_coalitions/v1"]
    metric_approximation: Literal["exact_metric/v1", "native_approximate_metric/v1"]
    standard_error: Literal["observed_marginal_mean/v1"]
    closure_policy: Literal["typed_marginal_float64_sum/v1"]

    def __post_init__(self) -> None:
        policies = (
            self.kind,
            self.semantics_version,
            self.player_domain,
            self.input_consistency,
            self.domain_policy,
            self.standard_error,
            self.closure_policy,
        )
        if policies != (
            "group_replacement",
            "group_replacement/v1",
            "joint_key_union/v1",
            "independent_reads/v1",
            "evaluated_coalitions/v1",
            "observed_marginal_mean/v1",
            "typed_marginal_float64_sum/v1",
        ) or self.metric_approximation not in ("exact_metric/v1", "native_approximate_metric/v1"):
            _fail(
                "closed native query game, consistency, domain and error policies", repr(self.kind)
            )
        _digest_identity(self.current_formula_id)
        _digest_identity(self.baseline_formula_id)

    @override
    def _summary(self) -> str:
        return f"kind=group_replacement, formula={self.current_formula_id[:12]}, input=independent_reads/v1, domain=evaluated_coalitions/v1"


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyReduction(_FactRecord):
    """Name an original non-quantile reducer; no executable reduction is retained."""

    kind: Literal["aggregate"]
    reducer: Literal[
        "sum",
        "count",
        "entity_count",
        "count_distinct",
        "approx_nunique",
        "mean",
        "weighted_mean",
        "min",
        "max",
    ]

    def __post_init__(self) -> None:
        if not _reducer_kind(
            self.kind,
            "aggregate",
            self.reducer,
            (
                "sum",
                "count",
                "entity_count",
                "count_distinct",
                "approx_nunique",
                "mean",
                "weighted_mean",
                "min",
                "max",
            ),
        ):
            _fail("a closed original reducer identity", repr(self.reducer))

    @override
    def _summary(self) -> str:
        return f"kind=aggregate, reducer={self.reducer}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyQuantile(_FactRecord):
    """Capture original exact quantile parameters and the qualified finishing recipe.

    The finisher is captured from the original reduction rather than inferred
    from its backend or transport type. It remains separate from sampling error.
    """

    kind: Literal["quantile"]
    reducer: Literal["median", "quantile"]
    q: float
    finisher: Literal["rank_bounds", "duckdb_native", "postgres_native"]

    def __post_init__(self) -> None:
        if not _reducer_kind(self.kind, "quantile", self.reducer, ("median", "quantile")):
            _fail("an exact median or quantile reducer", repr(self.reducer))
        if type(self.q) is not float or not isfinite(self.q) or not 0 <= self.q <= 1:
            _fail("a finite captured Float64 quantile in [0, 1]", repr(self.q))
        if self.reducer == "median" and self.q != 0.5:
            _fail("q=0.5 for the median reducer", repr(self.q))
        if self.finisher not in ("rank_bounds", "duckdb_native", "postgres_native"):
            _fail("the closed original quantile finishing recipe", repr(self.finisher))

    @override
    def _summary(self) -> str:
        return f"kind=quantile, reducer={self.reducer}, q={self.q!r}, finisher={self.finisher}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyNativeQuantile(_FactRecord):
    """Retain the original native exact or approximate quantile operation.

    The reducer and backend receipts identify the original native algorithm;
    no local rank finisher or replacement sketch is claimed or retained.
    """

    kind: Literal["native_quantile"]
    reducer: Literal["median", "quantile", "approx_median", "approx_quantile"]
    q: float
    algorithm_version: Literal["original_native_reducer/v1"]

    def __post_init__(self) -> None:
        if not _reducer_kind(
            self.kind,
            "native_quantile",
            self.reducer,
            (
                "median",
                "quantile",
                "approx_median",
                "approx_quantile",
            ),
        ):
            _fail("an original native exact or approximate quantile reducer", repr(self.reducer))
        if type(self.q) is not float or not isfinite(self.q) or not 0 <= self.q <= 1:
            _fail("a finite captured Float64 quantile in [0, 1]", repr(self.q))
        if self.reducer in ("median", "approx_median") and self.q != 0.5:
            _fail("q=0.5 for the original median reducer", repr(self.q))
        if self.algorithm_version != "original_native_reducer/v1":
            _fail("the versioned original backend-native reducer", repr(self.algorithm_version))

    @override
    def _summary(self) -> str:
        return f"kind=native_quantile, reducer={self.reducer}, q={self.q!r}, algorithm={self.algorithm_version}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyLeaf(_FactRecord):
    """Associate one typed original reducer occurrence with its captured input receipts.

    Read ``metric.method.leaves`` for original input/output types and NULL masks.
    Capture identifiers reference inert cost records, not connections or state.
    Weighted mean repeats its single captured paired NULL policy in the value
    and weight fields; it does not declare independent NULL-policy inputs.
    """

    occurrence: tuple[int, ...]
    current_capture_ids: tuple[str, ...]
    baseline_capture_ids: tuple[str, ...]
    reduction: FrameShapleyReduction | FrameShapleyQuantile | FrameShapleyNativeQuantile
    input_types: tuple[DataType, ...]
    output: DataColumn
    mask_policy: Literal["rows", "non_null_value", "co_valid_value_weight"]
    null_policy: Literal["skip", "error", "not_applicable"]
    weight_null_policy: Literal["skip", "error", "not_applicable"]

    def __post_init__(self) -> None:
        if (
            type(self.occurrence) is not tuple
            or not self.occurrence
            or any(type(part) is not int or part < 0 for part in self.occurrence)
        ):
            _fail("an immutable nonempty original leaf occurrence", repr(self.occurrence))
        for captured in (self.current_capture_ids, self.baseline_capture_ids):
            if type(captured) is not tuple or not captured:
                _fail("nonempty immutable unique endpoint capture references", repr(captured))
            for capture_id in captured:
                _text(capture_id)
            if len(set(captured)) != len(captured):
                _fail("nonempty immutable unique endpoint capture references", repr(captured))
        if type(self.reduction) not in (
            FrameShapleyReduction,
            FrameShapleyQuantile,
            FrameShapleyNativeQuantile,
        ):
            _fail("a closed original reducer or quantile record", type(self.reduction).__name__)
        if type(self.output) is not DataColumn:
            _fail("the original concrete typed reducer output", type(self.output).__name__)
        if type(self.input_types) is not tuple or any(
            type(dtype)
            not in (ScalarDataType, DecimalDataType, TimestampDataType, DurationDataType)
            for dtype in self.input_types
        ):
            _fail("immutable concrete original input types", repr(self.input_types))
        weighted = (
            isinstance(self.reduction, FrameShapleyReduction)
            and self.reduction.reducer == "weighted_mean"
        )
        entity = (
            isinstance(self.reduction, FrameShapleyReduction)
            and self.reduction.reducer == "entity_count"
        )
        if len(self.input_types) != (2 if weighted else 0 if entity else 1):
            _fail("original reducer input arity", repr(self.occurrence))
        expected_mask = (
            "co_valid_value_weight" if weighted else "rows" if entity else "non_null_value"
        )
        if self.mask_policy != expected_mask:
            _fail("the original reducer's effective support mask", repr(self.mask_policy))
        if self.null_policy not in (("not_applicable",) if entity else ("skip", "error")):
            _fail("the original value NULL policy", repr(self.null_policy))
        if self.weight_null_policy not in (("skip", "error") if weighted else ("not_applicable",)):
            _fail(
                "the original weight NULL policy only for paired weights",
                repr(self.weight_null_policy),
            )
        if weighted and self.weight_null_policy != self.null_policy:
            _fail(
                "one captured NULL policy for the co-valid value/weight pair",
                repr(self.weight_null_policy),
            )

    @override
    def _summary(self) -> str:
        return f"kind=leaf, occurrence={self.occurrence}, reducer={self.reduction.reducer}, output={_short(self.output.name)!r}"


def state_metric_approximation(
    leaves: tuple[FrameShapleyLeaf, ...],
) -> Literal["exact_metric/v1", "summary_reconstruction/v1"]:
    """Classify the captured state recipe; permutation error does not cover its bias."""
    return (
        "summary_reconstruction/v1"
        if any(
            isinstance(leaf.reduction, FrameShapleyReduction)
            and (
                leaf.reduction.reducer == "mean"
                or (
                    leaf.reduction.reducer in ("sum", "weighted_mean")
                    and ScalarDataType("float64") in leaf.input_types
                )
            )
            for leaf in leaves
        )
        else "exact_metric/v1"
    )


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyCapture(_FactRecord):
    """Describe actual state capture and transport costs without retaining the state.

    The complete prepared-input directory includes auxiliary player-domain,
    diagnostic and shared sibling inputs even when no leaf references them.

    ``returned_bytes`` measures decoded Arrow bytes, not wire or scanned bytes.
    ``state_bytes`` measures the typed logical state payload, not process memory.
    Unknown backend scan/wire statistics and backend version stay None. Actual
    executed capture statements are counted by source_reads; physical scans and
    unobserved schema/catalog work are not inferred from that count.
    """

    capture_id: str
    endpoint: Literal["current", "baseline", "both"]
    backend: str
    backend_version: str | None
    state_route: _StateRoute
    state_entries: int
    state_bytes: int
    returned_rows: int
    returned_bytes: int
    source_reads: int
    native_parameter_bytes: int
    native_batches: int
    scanned_rows: int | None
    scanned_bytes: int | None
    wire_bytes: int | None

    def __post_init__(self) -> None:
        _text(self.capture_id)
        _text(self.backend)
        if self.backend_version is not None:
            _text(self.backend_version)
        if self.endpoint not in ("current", "baseline", "both") or self.state_route not in (
            "typed_sufficient_state/v1",
            "typed_value_frequency/v1",
            "typed_sufficient_frequency/v1",
        ):
            _fail("a captured endpoint and qualified typed state route", repr(self.endpoint))
        for value in (
            self.state_entries,
            self.state_bytes,
            self.returned_rows,
            self.returned_bytes,
            self.source_reads,
            self.native_parameter_bytes,
            self.native_batches,
        ):
            _count(value)
        for optional in (self.scanned_rows, self.scanned_bytes, self.wire_bytes):
            if optional is not None:
                _count(optional)

    @override
    def _summary(self) -> str:
        return f"kind=capture, input={_short(self.capture_id)!r}, endpoint={self.endpoint}, route={self.state_route}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameShapleyQuery(_FactRecord):
    """Record one executed native query and its measured returned-data costs.

    The complete invocation directory includes player-domain, diagnostic and
    coalition queries. Cached coalition lookups create no query receipt.
    ``returned_bytes`` means decoded Arrow bytes, not scanned or wire bytes.
    Unknown native backend version and physical scan/wire statistics stay None.
    These records contain no SQL, data state, source handle or replay program.
    """

    capture_id: str
    endpoint: Literal["current", "baseline", "both"]
    backend: str
    backend_version: str | None
    purpose: Literal["player_domain", "group_diagnostic", "coalition"]
    returned_rows: int
    returned_bytes: int
    source_reads: int
    native_parameter_bytes: int
    native_batches: int
    scanned_rows: int | None
    scanned_bytes: int | None
    wire_bytes: int | None

    def __post_init__(self) -> None:
        _text(self.capture_id)
        _text(self.backend)
        if self.backend_version is not None:
            _text(self.backend_version)
        if self.endpoint not in ("current", "baseline", "both") or self.purpose not in (
            "player_domain",
            "group_diagnostic",
            "coalition",
        ):
            _fail("an actual native query endpoint and purpose", repr(self.purpose))
        for value in (
            self.returned_rows,
            self.returned_bytes,
            self.source_reads,
            self.native_parameter_bytes,
            self.native_batches,
        ):
            _count(value)
        if self.source_reads != 1:
            _fail("one actual executed statement per native query receipt", repr(self.source_reads))
        for optional in (self.scanned_rows, self.scanned_bytes, self.wire_bytes):
            if optional is not None:
                _count(optional)

    @override
    def _summary(self) -> str:
        return f"kind=native_query, input={_short(self.capture_id)!r}, endpoint={self.endpoint}, purpose={self.purpose}"


def validate_shapley_leaves(
    leaves: tuple[FrameShapleyLeaf, ...],
    captures: tuple[FrameShapleyCapture | FrameShapleyQuery, ...],
    *,
    native: bool,
    column: str,
) -> None:
    directory = {capture.capture_id: capture for capture in captures}
    if len(directory) != len(captures) or len({leaf.occurrence for leaf in leaves}) != len(leaves):
        _fail("unique capture identities and original leaf occurrences", column)
    for leaf in leaves:
        if (
            not native
            and isinstance(leaf.reduction, FrameShapleyReduction)
            and leaf.reduction.reducer == "approx_nunique"
        ):
            _fail(
                "original approximate distinct through native queries without merged estimates",
                repr(leaf.occurrence),
            )
        if (native and isinstance(leaf.reduction, FrameShapleyQuantile)) or (
            not native and isinstance(leaf.reduction, FrameShapleyNativeQuantile)
        ):
            _fail(
                "original quantile metadata matching the state or native query route",
                repr(leaf.occurrence),
            )
        for capture_ids, endpoint in (
            (leaf.current_capture_ids, "current"),
            (leaf.baseline_capture_ids, "baseline"),
        ):
            resolved = tuple(directory.get(capture_id) for capture_id in capture_ids)
            for capture_id, capture in zip(capture_ids, resolved, strict=True):
                if capture is None or capture.endpoint not in (endpoint, "both"):
                    _fail(
                        "original leaves resolving to the correct fixed endpoint captures",
                        capture_id,
                    )
            if (
                not native
                and (
                    isinstance(leaf.reduction, FrameShapleyQuantile)
                    or leaf.reduction.reducer == "count_distinct"
                )
                and not any(
                    isinstance(capture, FrameShapleyCapture)
                    and capture.state_route
                    in ("typed_value_frequency/v1", "typed_sufficient_frequency/v1")
                    for capture in resolved
                )
            ):
                _fail(
                    "an exact frequency input among the original leaf's captured components",
                    repr(capture_ids),
                )
