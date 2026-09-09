"""Pure objective-specific discovery and exact private Candidate registration."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset, MaterializedDataset, _dataset_repr
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.datasets.registry import (
    ConsumerRegistration,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _validate_materialized_state
from marivo.analysis.observation.contracts import (
    EntityReducedMetricSemantics,
    RetainedRowsPayload,
    owner_of,
    producer_contract,
)
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.candidate_contracts import (
    METHODS,
    SHAPES,
    CandidateDefinition,
    CandidateObjective,
    CandidatePayload,
    CandidateSemantics,
    CandidateSpecV1,
)
from marivo.analysis.operators.candidate_dataset import (
    LogicalCandidateDataset,
    MaterializedCandidateDataset,
)
from marivo.analysis.operators.contracts import (
    DeltaSemantics,
    comparison_basis,
    decode_comparison_basis,
)
from marivo.analysis.operators.errors import discovery_error
from marivo.semantic._quantile import approximation_class, decode_approximation

COMMON_FIELDS = (("item_id", "string"), ("score", "float64"), ("reason_codes", "candidate_reasons"))
VALUE_FIELDS: dict[CandidateObjective, tuple[tuple[str, str], ...]] = {
    "point_anomalies": (
        ("observed_value", "float64"),
        ("baseline_value", "float64"),
        ("signed_deviation", "float64"),
        ("direction", "string"),
    ),
    "interesting_windows": (
        ("point_count", "int64"),
        ("peak_absolute_zscore", "float64"),
        ("direction", "string"),
    ),
    "period_shifts": (
        ("window_size", "int64"),
        ("peak_absolute_zscore", "float64"),
        ("direction", "string"),
    ),
}
TIME_FIELDS: dict[CandidateObjective, tuple[str, ...]] = {
    "point_anomalies": ("time_coordinate",),
    "interesting_windows": ("window_start", "window_end", "baseline_start", "baseline_end"),
    "period_shifts": ("window_start", "window_end", "baseline_start", "baseline_end"),
}


@dataclass(frozen=True, slots=True, repr=False)
class MetricDiscovery:
    """Non-callable discovery namespace bound to one exact Metric receiver."""

    _dataset: Dataset

    def point_anomalies(
        self, *, threshold: float = 3.0, limit: int = 50
    ) -> LogicalCandidateDataset:
        """Return Logical Candidate leads for individual unusual time points.

        Args: threshold: Positive absolute z-score cutoff. limit: Maximum leads in [1, 1000].
        Example: ``history.discover.point_anomalies(threshold=2.5, limit=20)``.
        Constraints: One time-bearing Metric; finite non-constant evaluation occurs on execute().
        """
        return _discover(self._dataset, "point_anomalies", threshold=threshold, limit=limit)

    def interesting_windows(
        self, *, threshold: float = 2.0, limit: int = 50
    ) -> LogicalCandidateDataset:
        """Return Logical Candidate leads for maximal contiguous unusual runs.

        Args: threshold: Positive absolute z-score cutoff. limit: Maximum leads in [1, 1000].
        Example: ``history.discover.interesting_windows(threshold=2.0)``.
        Constraints: One time-bearing Metric; gaps and null points break runs.
        """
        return _discover(self._dataset, "interesting_windows", threshold=threshold, limit=limit)

    def __repr__(self) -> str:
        return "<MetricDiscovery; use .point_anomalies() or .interesting_windows()>"


@dataclass(frozen=True, slots=True, repr=False)
class DeltaDiscovery:
    """Non-callable discovery namespace bound to one exact Delta receiver."""

    _dataset: Dataset

    def period_shifts(self, *, threshold: float = 2.0, limit: int = 50) -> LogicalCandidateDataset:
        """Return Logical Candidate leads for unusual trailing Delta window means.

        Args: threshold: Positive absolute z-score cutoff. limit: Maximum leads in [1, 1000].
        Example: ``change.discover.period_shifts(threshold=2.0, limit=20)``.
        Constraints: One time-bearing Delta; only complete consecutive windows are evaluated.
        """
        return _discover(self._dataset, "period_shifts", threshold=threshold, limit=limit)

    def __repr__(self) -> str:
        return "<DeltaDiscovery; use .period_shifts()>"


def _parameters(threshold: float, limit: int) -> float:
    if type(threshold) not in (int, float):
        raise discovery_error("finite positive threshold", "invalid threshold type")
    try:
        normalized = float(threshold)
    except (OverflowError, ValueError):
        raise discovery_error("finite positive threshold", "unrepresentable threshold") from None
    if not math.isfinite(normalized) or normalized <= 0:
        raise discovery_error("finite positive threshold", "invalid threshold value")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise discovery_error("integer discovery limit in [1, 1000]", "invalid limit")
    return normalized


def _field_id(objective: CandidateObjective, name: str) -> d.DatasetFieldId:
    return d._make_field_id(f"generated.discover.{objective}.{name}@v1")


def _generated(
    objective: CandidateObjective, name: str, kind: str, ids: d._StableIdRegistry
) -> d.DatasetField:
    field_id = _field_id(objective, name)
    role = (
        "candidate_reason_codes"
        if name == "reason_codes"
        else "candidate_coordinate"
        if name in TIME_FIELDS[objective]
        else "effect_value"
    )
    return d._make_field(
        field_id=field_id,
        name=name,
        role_id=role,
        identity=d._generated_identity(field_id),
        derivation_identity=f"discover.{objective}.{name}@v1",
        logical_type_id=kind,
        physical_type_state=d._deferred_type(kind, ids=ids),
        nullable=False,
        ids=ids,
    )


def _definition(
    dataset: Dataset, objective: CandidateObjective, threshold: float, limit: int
) -> CandidateDefinition:
    incoming = dataset.row_contract.family_semantics
    if dataset.row_contract.shape_id.local_shape_id not in ("time", "dimension-time"):
        raise discovery_error("one time or dimension-time Metric or Delta", "unsupported shape")
    if objective == "period_shifts":
        if dataset.kind != "delta" or not isinstance(incoming, DeltaSemantics):
            raise discovery_error("one time-bearing Delta", "unsupported discovery receiver")
        fold = incoming.current_fold_authority
        baseline = incoming.baseline_fold_authority
        metric_key = "metric:" + incoming.metric_ref
        metric_unit = incoming.metric_unit
        approximation = incoming.approximation_class
    else:
        metrics = tuple(f for f in dataset.schema.columns if f.role_id == "metric")
        if (
            dataset.kind != "metric"
            or not isinstance(incoming, EntityReducedMetricSemantics)
            or len(metrics) != 1
            or not isinstance(metrics[0].identity, d._CatalogFieldIdentity)
            or (
                not metrics[0].logical_type_id.startswith(("int", "uint", "float", "decimal"))
                and metrics[0].logical_type_id not in ("integer", "floating")
            )
        ):
            raise discovery_error("one quantitative time-bearing Metric", "unsupported receiver")
        fold = incoming.fold_authority
        baseline = None
        metric_key = metrics[0].identity.identity_id
        metric_unit = incoming.metric_bindings[0][1]
        authority = decode_fold_authority(fold)
        approximation = approximation_class(
            sampled=bool(decode_comparison_basis(comparison_basis(dataset)).sampling_definition),
            semantic=any(
                item.distribution is not None
                and item.distribution.quantile.method == "duckdb_tdigest@v1"
                for item in authority.metrics
            ),
        )
    result = CandidateDefinition(
        objective,
        METHODS[objective],
        "materialized" if isinstance(dataset, MaterializedDataset) else "logical",
        dataset.state.artifact_ref.ref
        if isinstance(dataset, MaterializedDataset)
        else dataset.definition_fingerprint,
        threshold,
        limit,
        approximation,
        fold,
        baseline,
        metric_key,
        metric_unit,
    )
    validate_definition(result)
    return result


def validate_definition(definition: CandidateDefinition) -> None:
    """Reject changed or contradictory retained search authority."""
    if (
        type(definition) is not CandidateDefinition
        or definition.objective not in METHODS
        or definition.method_id != METHODS[definition.objective]
    ):
        raise discovery_error("one closed discovery objective and method", "changed definition")
    _parameters(definition.threshold, definition.limit)
    if type(definition.threshold) is not float:
        raise discovery_error("normalized floating threshold", "noncanonical threshold")
    pattern = (
        r"ds_[a-f0-9]{64}"
        if definition.input_state_kind == "logical"
        else r"artifact_[a-f0-9]{32}"
        if definition.input_state_kind == "materialized"
        else None
    )
    if pattern is None or re.fullmatch(pattern, definition.input_authority) is None:
        raise discovery_error(
            "exact logical fingerprint or Artifact ref", "invalid input authority"
        )
    decode_approximation(definition.approximation)
    authority = decode_fold_authority(definition.fold_authority)
    if (
        len(authority.metrics) != 1
        or "metric:" + authority.metrics[0].metric_ref != definition.metric_key
        or authority.time_grain() is None
        or (definition.metric_unit is not None and type(definition.metric_unit) is not str)
    ):
        raise discovery_error(
            "one exact Metric and temporal authority", "invalid discovery binding"
        )
    if definition.objective == "period_shifts":
        if definition.baseline_fold_authority is None:
            raise discovery_error("paired Delta temporal authority", "missing baseline")
        baseline = decode_fold_authority(definition.baseline_fold_authority)
        if (
            baseline.metrics != authority.metrics
            or baseline.time_grain() != authority.time_grain()
            or baseline.temporal_snapshot() != authority.temporal_snapshot()
        ):
            raise discovery_error("compatible paired Delta temporal authority", "changed baseline")
    elif definition.baseline_fold_authority is not None or authority.time_scope() is None:
        raise discovery_error(
            "one current Metric observation scope", "invalid Metric time authority"
        )


def _discover(
    dataset: Dataset, objective: CandidateObjective, *, threshold: float, limit: int
) -> LogicalCandidateDataset:
    threshold = _parameters(threshold, limit)
    definition = _definition(dataset, objective, threshold, limit)
    ids = dataset._registration.ids
    dimensions = tuple(f for f in dataset.schema.columns if f.role_id == "dimension")
    time = next(
        f
        for f in dataset.schema.columns
        if f.role_id == "time_dimension"
        or (f.role_id == "comparison_time" and f.name == "current_time")
    )
    baseline_time = next(
        (
            f
            for f in dataset.schema.columns
            if f.role_id == "comparison_time" and f.name == "baseline_time"
        ),
        time,
    )
    generated = tuple(_generated(objective, name, kind, ids) for name, kind in COMMON_FIELDS)
    values = tuple(_generated(objective, name, kind, ids) for name, kind in VALUE_FIELDS[objective])
    temporal: tuple[d.DatasetField, ...]
    if objective == "point_anomalies":
        temporal = (replace(time, _token=d._CORE_TOKEN, name="time_coordinate"),)
        columns = (*generated, *dimensions, *temporal, *values)
    else:
        temporal = tuple(
            _generated(
                objective,
                name,
                baseline_time.logical_type_id
                if name.startswith("baseline_")
                else time.logical_type_id,
                ids,
            )
            for name in TIME_FIELDS[objective]
        )
        columns = (
            (*generated, *dimensions, *temporal[:2], *values, *temporal[2:])
            if objective == "interesting_windows"
            else (*generated, *dimensions, *temporal, *values)
        )
    if len({f.name for f in columns}) != len(columns):
        raise discovery_error(
            "unambiguous retained and generated names", "Candidate field collision"
        )
    coordinates = (*dimensions, *temporal)
    keys = tuple(
        f.field_id
        for f in (
            (*dimensions, *temporal[:2]) if objective == "interesting_windows" else coordinates
        )
    )
    semantics = CandidateSemantics(
        _token=d._CORE_TOKEN,
        objective=objective,
        method_id=definition.method_id,
        approximation=definition.approximation,
        item_id_field_id=generated[0].field_id,
        score_field_id=generated[1].field_id,
        reason_codes_field_id=generated[2].field_id,
    )
    row = d._make_row_contract(
        schema_version=1,
        shape_id=d._make_shape_id("candidate", SHAPES[objective], 1, ids=ids),
        schema=d._make_schema(columns),
        coordinate_field_ids=tuple(f.field_id for f in coordinates),
        key_field_ids=keys,
        family_semantics=semantics,
    )
    rows = d._make_row_set_contract(
        schema_version=1,
        cardinality=d._keyed_cardinality(d._static_row_bound(limit)),
        ordering=d._ordered_ordering(
            tuple(
                d._make_order_term(
                    field_id,
                    direction="descending" if field_id == semantics.score_field_id else "ascending",
                    nulls="last",
                    value_order_contract_id="observation.scalar_order@v1",
                    ids=ids,
                )
                for field_id in (semantics.score_field_id, *keys, semantics.item_id_field_id)
            )
        ),
    )
    spec = CandidateSpecV1(dataset.row_contract, dataset.row_set_contract, row, rows, definition)
    result = construct_operator(
        owner=owner_of(dataset),
        registry=dataset._registry,
        operator_id=f"discover.{objective}",
        contract_versions=producer_contract(f"discover.{objective}").versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=rows,
        payload=CandidatePayload(_token=d._CORE_TOKEN, spec=spec),
    )
    if not isinstance(result, LogicalCandidateDataset):
        raise discovery_error("paired Logical Candidate", "invalid family registration")
    return result


def _generated_field(
    field: d.DatasetField, objective: CandidateObjective, name: str, kind: str
) -> bool:
    expected_role = (
        "candidate_reason_codes"
        if name == "reason_codes"
        else "candidate_coordinate"
        if name in TIME_FIELDS[objective]
        else "effect_value"
    )
    return (
        field.name == name
        and field.field_id == _field_id(objective, name)
        and field.role_id == expected_role
        and field.logical_type_id == kind
        and not field.nullable
        and isinstance(field.identity, d._GeneratedFieldIdentity)
        and field.identity.producer_field_id == field.field_id
        and field.derivation_identity == f"discover.{objective}.{name}@v1"
    )


def candidate_filterable_field(field: d.DatasetField) -> bool:
    """Admit only exact retained coordinates and registered generated scalars."""
    if field.role_id in ("dimension", "time_dimension"):
        return isinstance(field.identity, d._CatalogFieldIdentity)
    if field.role_id == "rank":
        return (
            field.field_id.value == "generated.rank@v1"
            and field.name == "rank"
            and field.logical_type_id == "int64"
            and field.nullable
            and isinstance(field.identity, d._GeneratedFieldIdentity)
            and field.identity.producer_field_id == field.field_id
        )
    for objective in METHODS:
        if any(
            _generated_field(field, objective, name, kind)
            for name, kind in (*COMMON_FIELDS[:2], *VALUE_FIELDS[objective])
        ):
            return True
        if (
            objective != "point_anomalies"
            and field.logical_type_id in ("date", "timestamp")
            and any(
                _generated_field(field, objective, name, field.logical_type_id)
                for name in TIME_FIELDS[objective]
            )
        ):
            return True
    return False


def validate_candidate(row: d.DatasetRowContract, rows: d.DatasetRowSetContract) -> None:
    s = row.family_semantics
    if (
        not isinstance(s, CandidateSemantics)
        or s.objective not in METHODS
        or s.method_id != METHODS[s.objective]
        or row.shape_id.local_shape_id != SHAPES[s.objective]
    ):
        raise discovery_error("closed objective-specific Candidate meaning", "invalid semantics")
    decode_approximation(s.approximation)
    fields = {f.name: f for f in row.schema.columns}
    dimensions = tuple(f for f in row.schema.columns if f.role_id == "dimension")
    temporal = tuple(fields.get(name) for name in TIME_FIELDS[s.objective])
    if any(f is None for f in temporal):
        raise discovery_error("complete objective temporal fields", "missing coordinates")
    for name, kind in (*COMMON_FIELDS, *VALUE_FIELDS[s.objective]):
        f = fields.get(name)
        if f is None or not _generated_field(f, s.objective, name, kind):
            raise discovery_error(
                "exact generated Candidate field bindings", "invalid generated field"
            )
    if any(not isinstance(f.identity, d._CatalogFieldIdentity) for f in dimensions):
        raise discovery_error("retained governed Dimensions", "invalid Dimension identity")
    for f in temporal:
        if f is None:
            raise discovery_error("complete temporal fields", "missing temporal field")
        if (
            f.logical_type_id not in ("date", "timestamp")
            or (
                s.objective == "point_anomalies"
                and (
                    f.role_id != "time_dimension"
                    or not isinstance(f.identity, d._CatalogFieldIdentity)
                )
            )
            or (
                s.objective != "point_anomalies"
                and not _generated_field(f, s.objective, f.name, f.logical_type_id)
            )
        ):
            raise discovery_error("exact typed Candidate coordinates", "changed temporal field")
    common_names = tuple(name for name, _ in COMMON_FIELDS)
    dim_names = tuple(f.name for f in dimensions)
    time_names = TIME_FIELDS[s.objective]
    value_names = tuple(name for name, _ in VALUE_FIELDS[s.objective])
    expected = (
        (*common_names, *dim_names, *time_names[:2], *value_names, *time_names[2:])
        if s.objective == "interesting_windows"
        else (*common_names, *dim_names, *time_names, *value_names)
    )
    if "rank" in fields:
        if not candidate_filterable_field(fields["rank"]):
            raise discovery_error("registered nullable rank", "invalid rank field")
        expected = (*expected, "rank")
    coordinate_ids = tuple(fields[name].field_id for name in (*dim_names, *time_names))
    key_names = (
        *dim_names,
        *(time_names[:2] if s.objective == "interesting_windows" else time_names),
    )
    keys = tuple(fields[name].field_id for name in key_names)
    if (
        tuple(fields) != expected
        or row.coordinate_field_ids != coordinate_ids
        or row.key_field_ids != keys
        or rows.cardinality.kind != "keyed"
        or not isinstance(rows.ordering, d._OrderedOrdering)
        or (s.item_id_field_id, s.score_field_id, s.reason_codes_field_id)
        != tuple(fields[name].field_id for name in common_names)
    ):
        raise discovery_error(
            "exact Candidate schema, references, keys and order", "invalid row contract"
        )
    if "rank" not in fields and (
        tuple(t.field_id for t in rows.ordering.terms)
        != (s.score_field_id, *keys, s.item_id_field_id)
        or any(
            (t.direction, t.nulls, t.value_order_contract_id)
            != (
                "descending" if i == 0 else "ascending",
                "last",
                "observation.scalar_order@v1",
            )
            for i, t in enumerate(rows.ordering.terms)
        )
    ):
        raise discovery_error(
            "score descending, typed key and item-id ascending", "changed candidate order"
        )


def _definition_of(dataset: Dataset) -> CandidateDefinition:
    current = dataset
    while not isinstance(current, MaterializedDataset):
        root = current._root
        if isinstance(root, LogicalRootHandle) and isinstance(root.payload, CandidatePayload):
            return root.payload.spec.definition
        if len(current._inputs) != 1 or current._inputs[0].kind != "candidate":
            raise discovery_error("original Candidate definition", "missing construction authority")
        current = current._inputs[0]
    definition = owner_of(current).candidate_definition_snapshot
    if definition is None:
        raise discovery_error("preloaded committed Candidate definition", "missing snapshot")
    return definition


def _contract_facts(dataset: Dataset) -> tuple[tuple[str, str], ...]:
    s = dataset.row_contract.family_semantics
    if not isinstance(s, CandidateSemantics):
        raise discovery_error("closed Candidate meaning", "missing disclosure authority")
    definition = _definition_of(dataset)
    return (
        ("objective", s.objective),
        ("method", s.method_id),
        ("threshold", str(definition.threshold)),
        ("discovery_limit", str(definition.limit)),
        ("input_state_kind", definition.input_state_kind),
        ("input_authority", definition.input_authority),
        ("approximation", s.approximation),
        (
            "baseline",
            "population mean/stddev of complete consecutive trailing-window means; window=max(7, floor(series_length/10))"
            if s.objective == "period_shifts"
            else "population mean/stddev of the complete current series' non-null points",
        ),
        (
            "interpretation",
            "descriptive screening leads; scores compare only within this definition",
        ),
        ("findings", "zero; filtering and ranking never establish causality or significance"),
    )


def register_candidate(registry: DatasetFamilyRegistry, ids: d._StableIdRegistry) -> None:
    def decode(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    shapes = tuple(d._make_shape_id("candidate", shape, 1, ids=ids) for shape in SHAPES.values())
    registry.register(
        DatasetFamilyRegistration(
            family_id="candidate",
            logical_type=LogicalCandidateDataset,
            materialized_type=MaterializedCandidateDataset,
            shape_ids=shapes,
            owner_id="operators.discovery",
            ids=ids,
            row_validator=validate_candidate,
            consumers=tuple(
                ConsumerRegistration(
                    f"candidate.{method}",
                    ("input",),
                    "candidate",
                    shapes,
                    ("candidate.current_rows@v1",),
                )
                for method in ("where", "rank", "limit")
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=decode,
            node_payload_types=(CandidatePayload, RetainedRowsPayload),
            contract_facts=_contract_facts,
            consumer_admission=lambda dataset, method: (
                not any(f.role_id == "rank" for f in dataset.schema.columns)
                if method == "candidate.rank"
                else dataset.row_set_contract.ordering.kind == "ordered"
                if method == "candidate.limit"
                else True
            ),
        )
    )
