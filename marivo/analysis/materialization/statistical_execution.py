"""Registered complete-input statistics and frozen scope/view validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
from typing import Literal

import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    AssociationStatePart,
    DisplayPart,
    FindingPolicyPart,
    ForecastStatePart,
    FutureCellsPart,
    GridCellsPart,
    PairInputsPart,
    Signature,
    TableFitsPart,
    TrainingInputsPart,
    part_role,
)
from marivo.analysis.core.rules import AssociationFit, AssociationRead, ForecastFit, ForecastRead
from marivo.analysis.errors import StatisticalErrorCode, StatisticalRelationError
from marivo.analysis.materialization.deviation_execution import SavedPart, SavedTable, load, save
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.graph_protocol import digest, invalid
from marivo.analysis.methods import association_numeric as association
from marivo.analysis.methods import forecast_numeric as forecast
from marivo.analysis.methods import statistical_numeric as numeric
from marivo.analysis.methods.deviation_numeric import (
    RationalFact,
    RootCertificate,
    exact,
    finish,
    unit_type,
)
from marivo.analysis.methods.deviation_physical import parse_type
from marivo.analysis.methods.physical import arrow_scalar_type
from marivo.analysis.methods.registry import REGISTRY


@dataclass(frozen=True, slots=True)
class Input:
    signature: Signature
    value_type: str
    binding: str
    keys: tuple[str, ...]
    primary: SavedTable
    parts: tuple[SavedPart, ...]
    reasons: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class PairCapture:
    declaration: PairInputsPart
    inputs: tuple[Input, ...]
    version: Literal["r8.pair_inputs/v1"] = "r8.pair_inputs/v1"


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    a: int
    b: int
    series: str
    lag: int
    input_count: int
    matched: int
    boundary_drop: int
    null_pairs: int
    complete_pairs: int
    left_indices: tuple[int, ...]
    right_indices: tuple[int, ...]
    score: association.Score
    selected: bool


@dataclass(frozen=True, slots=True)
class AssociationState:
    input_digest: str
    candidates: tuple[Candidate, ...]
    views: SavedTable
    selection: Literal["association.max_abs_coefficient_min_abs_lag_min_signed_lag@v1"] = (
        "association.max_abs_coefficient_min_abs_lag_min_signed_lag@v1"
    )
    version: Literal["r8.association_state/v1"] = "r8.association_state/v1"


@dataclass(frozen=True, slots=True)
class TrainingCapture:
    declaration: TrainingInputsPart
    input: Input
    future: FutureCellsPart
    version: Literal["r8.training_inputs/v1"] = "r8.training_inputs/v1"


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    h: int
    point: RationalFact
    variance: RationalFact
    root: RootCertificate


@dataclass(frozen=True, slots=True)
class Series:
    identity: str
    indices: tuple[int, ...]
    training: forecast.Training
    points: tuple[ForecastPoint, ...]


@dataclass(frozen=True, slots=True)
class ForecastState:
    input_digest: str
    quantile: numeric.QuantileCertificate
    series: tuple[Series, ...]
    views: SavedTable
    assumptions: Literal["zero_mean_uncorrelated_homoskedastic_normal_innovations@v1"] = (
        "zero_mean_uncorrelated_homoskedastic_normal_innovations@v1"
    )
    interval_method: Literal["normal_residual@v1"] = "normal_residual@v1"
    version: Literal["r8.forecast_state/v1"] = "r8.forecast_state/v1"


PAIRS = TypeAdapter(PairCapture)
ASSOCIATION = TypeAdapter(AssociationState)
TRAINING = TypeAdapter(TrainingCapture)
FORECAST = TypeAdapter(ForecastState)
FUTURE = TypeAdapter(FutureCellsPart)


def fail(
    code: StatisticalErrorCode,
    operation: str,
    identity: str,
    expected: str,
    received: str,
    repair: str,
    *,
    method: str,
) -> StatisticalRelationError:
    return StatisticalRelationError(
        code=code,
        operation=operation,
        method=method,
        input_identity=identity,
        expected=expected,
        received=received,
        repair=repair,
    )


def capture(result: ExchangeResult, typ: str) -> Input:
    return Input(
        result.contract.signature,
        typ,
        result.contract.input_binding,
        result.contract.key_fields,
        save(result.primary),
        tuple(
            SavedPart(
                part_role(
                    next(d for d in result.contract.signature.parts if part_role(d) == p.role)
                ),
                next(d.key_fields for d in result.contract.parts if d.role == p.role),
                save(p.table),
            )
            for p in result.parts
        ),
        result.contract.cell_reasons,
    )


def index(table: pa.Table, keys: tuple[str, ...]) -> dict[tuple[object, ...], int]:
    result: dict[tuple[object, ...], int] = {}
    for i, row in enumerate(table.to_pylist()):
        key = tuple(row[k] for k in keys)
        if key in result:
            raise invalid("statistical input has duplicate composite keys")
        result[key] = i
    return result


def _input(
    value: Input, *, method: str, forecast_input: bool = False
) -> tuple[pa.Table, tuple[Fraction | None, ...]]:
    check()
    table = load(value.primary)
    typ = parse_type(value.value_type)
    if table.schema.field("value").type != arrow_scalar_type(typ):
        raise invalid("statistical input differs from captured physical type")
    allowed = dict(value.reasons)
    numbers: list[Fraction | None] = []
    for row in table.to_pylist():
        tag, reason, number = row["cell_tag"], row["cell_reason"], row["value"]
        if tag == "defined" and reason is None and isinstance(number, (int, float, Decimal)):
            try:
                numbers.append(exact(number, typ))
            except (ValueError, OverflowError) as error:
                raise fail(
                    "r8.cell_policy",
                    "forecast" if forecast_input else "correlate",
                    value.signature.domain.definition_id,
                    "finite original numeric carrier",
                    repr(number),
                    "Use finite captured observations of the declared original type.",
                    method=method,
                ) from error
        elif (
            not forecast_input
            and tag == "null"
            and number is None
            and reason in allowed.get("null", ())
            and reason in ("source_null", "empty_contribution")
        ):
            numbers.append(None)
        else:
            raise fail(
                "r8.cell_policy",
                "forecast" if forecast_input else "correlate",
                value.signature.domain.definition_id,
                "all Defined finite history"
                if forecast_input
                else "Defined finite or ordinary Null pair Cells",
                repr((tag, reason)),
                "Use complete finite observations; Undefined and Unknown cannot be deleted or imputed.",
                method=method,
            )
    index(table, value.keys)
    return table, tuple(numbers)


def _series(
    value: Input, table: pa.Table
) -> tuple[tuple[tuple[object, ...], tuple[int, ...]], ...]:
    domain = value.signature.domain
    grid = domain.time_grid
    if grid is None:
        return (((), tuple(range(table.num_rows))),)
    if any(c.partial for c in grid.cells):
        raise invalid("statistical time domain has partial cells")
    position = next((i for i, c in enumerate(domain.instance_key) if c.role == "anchor"), None)
    if position is None:
        raise invalid("time statistical input lacks a captured grid coordinate")
    coverage = next((load(p.table) for p in value.parts if p.role == "coverage"), None)
    if coverage is not None:
        keys = index(coverage, value.keys)
        completeness = dict(
            zip(keys, (coverage["coverage__complete"][keys[k]].as_py() for k in keys), strict=True)
        )
    else:
        mapping = next((load(p.table) for p in value.parts if p.role == "grid_cells"), None)
        owned_future = next(
            (p for p in value.signature.parts if isinstance(p, FutureCellsPart) and p.grid == grid),
            None,
        )
        if owned_future is not None:
            retained = tuple(ExchangePart(p.role, load(p.table)) for p in value.parts)
            original_training, original_forecast = decode_forecast(retained)
            if original_training.future != owned_future:
                raise invalid("numeric forecast view differs from its captured future authority")
            completeness = dict.fromkeys(index(load(original_forecast.views), value.keys), True)
        elif mapping is not None:
            keys = index(mapping, value.keys)
            completeness = {k: mapping["grid_cells__coverage"][i].as_py() for k, i in keys.items()}
        else:
            endpoints = tuple(
                next((load(p.table) for p in value.parts if p.role == role), None)
                for role in ("current_endpoint", "baseline_endpoint")
            )
            if any(t is None for t in endpoints):
                raise invalid("statistical time input lacks original complete coverage")
            completeness = {}
            for role, t in zip(("current_endpoint", "baseline_endpoint"), endpoints, strict=True):
                assert t is not None
                for k, i in index(t, value.keys).items():
                    completeness[k] = (
                        completeness.get(k, True) and t[role + "__complete"][i].as_py() is True
                    )
    groups: dict[tuple[object, ...], dict[str, int]] = {}
    actual = index(table, value.keys)
    if set(completeness) != set(actual) or any(v is not True for v in completeness.values()):
        raise invalid("statistical coverage differs from original full domain")
    for key, i in actual.items():
        series = key[:position] + key[position + 1 :]
        groups.setdefault(series, {})[str(key[position])] = i
    expected = tuple(c.identity for c in grid.cells)
    if any(set(cells) != set(expected) for cells in groups.values()) or not groups:
        raise invalid("statistical series have missing or extra original cells")
    return tuple(
        (series, tuple(cells[k] for k in expected))
        for series, cells in sorted(groups.items(), key=lambda item: repr(item[0]))
    )


def _pair_indices(
    saved: PairCapture,
) -> tuple[
    tuple[pa.Table, ...],
    tuple[tuple[Fraction | None, ...], ...],
    tuple[tuple[tuple[object, ...], tuple[int, ...]], ...],
]:
    decoded = tuple(
        _input(v, method="association." + saved.declaration.method + "@v1") for v in saved.inputs
    )
    tables, numbers = tuple(v[0] for v in decoded), tuple(v[1] for v in decoded)
    common = index(tables[0], saved.inputs[0].keys)
    if any(
        v.signature.domain != domain or v.value_type != typ or v.keys != saved.inputs[0].keys
        for v, typ, domain in zip(
            saved.inputs,
            saved.declaration.input_types,
            saved.declaration.input_domains,
            strict=True,
        )
    ):
        raise invalid("ordered input binding/type/domain differs from pair declaration")
    if any(set(index(t, v.keys)) != set(common) for t, v in zip(tables, saved.inputs, strict=True)):
        raise invalid("correlation inputs have different complete composite key sets")
    sequences = _series(saved.inputs[0], tables[0])
    # All input vectors are reordered by exact keys, never by row positions alone.
    reordered = []
    for table, v, values in zip(tables, saved.inputs, numbers, strict=True):
        mapping = index(table, v.keys)
        reordered.append(tuple(values[mapping[k]] for k in common))
        _series(v, table)
    return tables, tuple(reordered), sequences


def _grid_cells(value: Input) -> ExchangePart:
    """Freeze the original complete history map independently of current output keys."""
    table = load(value.primary)
    _series(value, table)
    grid = value.signature.domain.time_grid
    assert grid is not None
    position = next(
        i for i, c in enumerate(value.signature.domain.instance_key) if c.role == "anchor"
    )
    by_id = {cell.identity: (i, cell) for i, cell in enumerate(grid.cells)}
    cells = [by_id[row[value.keys[position]]] for row in table.to_pylist()]
    return ExchangePart(
        "grid_cells",
        pa.table(
            {
                **{key: table[key] for key in value.keys},
                "grid_cells__identity": pa.array([c.identity for _, c in cells], pa.string()),
                "grid_cells__ordinal": pa.array([i for i, _ in cells], pa.int64()),
                **{
                    "grid_cells__" + name: pa.array(
                        [getattr(c, name) for _, c in cells], pa.timestamp("us", "UTC")
                    )
                    for name in ("original_start", "original_end", "start", "end")
                },
                "grid_cells__partial": pa.array([c.partial for _, c in cells], pa.bool_()),
                "grid_cells__precision": pa.array([grid.precision] * len(cells), pa.string()),
                "grid_cells__coverage": pa.array([True] * len(cells), pa.bool_()),
            }
        ),
    )


def association_state(saved: PairCapture) -> AssociationState:
    _, vectors, sequences = _pair_indices(saved)
    declaration = saved.declaration
    count = (
        len(declaration.quantities)
        * (len(declaration.quantities) - 1)
        // 2
        * len(declaration.lags)
        * len(sequences)
    )
    if count > 4096:
        raise fail(
            "r8.candidate_ceiling",
            "correlate",
            declaration.association_id,
            "at most 4096 pair/lag/series candidates",
            str(count),
            "Reduce the explicitly requested quantity, lag or series scope before a new execution.",
            method="association." + declaration.method + "@v1",
        )
    candidates: list[Candidate] = []
    from dataclasses import replace

    for a, b in combinations(range(len(vectors)), 2):
        for series, positions in sequences:
            current = []
            for lag in declaration.lags:
                check()
                left = tuple(i for i in range(len(positions)) if 0 <= i + lag < len(positions))
                aligned = tuple((positions[i], positions[i + lag]) for i in left)
                complete = tuple(
                    (i, j)
                    for i, j in aligned
                    if vectors[a][i] is not None and vectors[b][j] is not None
                )
                x = tuple(vectors[a][i] for i, _ in complete)
                y = tuple(vectors[b][j] for _, j in complete)
                scored = association.score(
                    tuple(v for v in x if v is not None),
                    tuple(v for v in y if v is not None),
                    declaration.method,
                    checkpoint=check,
                )
                series_id = digest(repr(series))
                key = digest(repr((declaration.association_id, a, b, series_id, lag)))
                current.append(
                    Candidate(
                        key,
                        a,
                        b,
                        series_id,
                        lag,
                        len(positions),
                        len(aligned),
                        len(positions) - len(aligned),
                        len(aligned) - len(complete),
                        len(complete),
                        tuple(i for i, _ in complete),
                        tuple(j for _, j in complete),
                        scored,
                        False,
                    )
                )
            valid = [c for c in current if c.score.status == "valid"]
            if not valid:
                raise fail(
                    "r8.no_valid_candidate",
                    "correlate",
                    declaration.association_id,
                    "at least one valid lag per pair/series",
                    repr(
                        (
                            a,
                            b,
                            series,
                            tuple((c.lag, c.score.status, c.complete_pairs) for c in current),
                        )
                    ),
                    "Use corresponding observations with sufficient complete varying pairs, or explicitly change the requested lag scope.",
                    method="association." + declaration.method + "@v1",
                )
            winner = min(
                valid, key=lambda c: (-abs(c.score.coefficient or 0), abs(c.lag), c.lag)
            ).key
            candidates.extend(replace(c, selected=c.key == winner) for c in current)
    schema = pa.schema(
        [
            ("key_0", pa.string()),
            ("value", pa.float64()),
            ("cell_tag", pa.string()),
            ("cell_reason", pa.string()),
            ("selected", pa.bool_()),
            ("status", pa.string()),
            ("pair_a", pa.int64()),
            ("pair_b", pa.int64()),
            ("series", pa.string()),
            ("lag", pa.int64()),
            ("input_count", pa.int64()),
            ("matched_count", pa.int64()),
            ("boundary_drop_count", pa.int64()),
            ("null_pair_count", pa.int64()),
            ("complete_pair_count", pa.int64()),
        ]
    )
    rows = [
        {
            "key_0": c.key,
            "value": c.score.coefficient,
            "cell_tag": "defined" if c.score.status == "valid" else "undefined",
            "cell_reason": None if c.score.status == "valid" else c.score.status,
            "selected": c.selected,
            "status": c.score.status,
            "pair_a": c.a,
            "pair_b": c.b,
            "series": c.series,
            "lag": c.lag,
            "input_count": c.input_count,
            "matched_count": c.matched,
            "boundary_drop_count": c.boundary_drop,
            "null_pair_count": c.null_pairs,
            "complete_pair_count": c.complete_pairs,
        }
        for c in candidates
    ]
    return AssociationState(
        digest(PAIRS.dump_json(saved).decode()),
        tuple(candidates),
        save(pa.Table.from_pylist(rows, schema=schema)),
    )


def forecast_state(saved: TrainingCapture) -> ForecastState:
    table, numbers = _input(
        saved.input, method="forecast." + saved.declaration.model + "@v1", forecast_input=True
    )
    sequences = _series(saved.input, table)
    declaration = saved.declaration
    output = unit_type(parse_type(declaration.input_type))
    extra = 0
    while True:
        q = numeric.quantile(declaration.level, checkpoint=check, extra_precision=extra)
        records: list[dict[str, object]] = []
        series_records: list[Series] = []
        try:
            for series, indices in sequences:
                check()
                vector = tuple(numbers[i] for i in indices)
                values = tuple(v for v in vector if v is not None)
                training = forecast.train(values, declaration.model, declaration.season)
                witnesses = []
                anchor = next(
                    i
                    for i, c in enumerate(declaration.input_domain.instance_key)
                    if c.role == "anchor"
                )
                for h, cell in enumerate(saved.future.grid.cells, 1):
                    check()
                    point, variance = forecast.point(values, training, h)
                    lower, upper, root = numeric.interval(point, variance, q, output)
                    key = (*series[:anchor], cell.identity, *series[anchor:])
                    records.append(
                        {
                            **{f"key_{i}": v for i, v in enumerate(key)},
                            "prediction": finish(point, output),
                            "lower": lower,
                            "upper": upper,
                            "horizon": h,
                            "series": digest(repr(series)),
                        }
                    )
                    witnesses.append(
                        ForecastPoint(
                            h, RationalFact.capture(point), RationalFact.capture(variance), root
                        )
                    )
                series_records.append(
                    Series(digest(repr(series)), indices, training, tuple(witnesses))
                )
            break
        except ArithmeticError as error:
            if isinstance(error, OverflowError):
                raise
            extra += 40
            check()
    schema = pa.schema(
        [
            *(table.schema.field(k) for k in saved.input.keys),
            *((f, arrow_scalar_type(output)) for f in ("prediction", "lower", "upper")),
            ("horizon", pa.int64()),
            ("series", pa.string()),
        ]
    )
    return ForecastState(
        digest(TRAINING.dump_json(saved).decode()),
        q,
        tuple(series_records),
        save(pa.Table.from_pylist(records, schema=schema)),
    )


def _payload(role: str, value: str) -> ExchangePart:
    return ExchangePart(role, pa.table({role + "__retained": [value]}))


def _decode_pairs(parts: tuple[ExchangePart, ...]) -> tuple[PairCapture, AssociationState]:
    a = next(p.table["pair_inputs__retained"][0].as_py() for p in parts if p.role == "pair_inputs")
    b = next(
        p.table["association_state__retained"][0].as_py()
        for p in parts
        if p.role == "association_state"
    )
    captured, state = PAIRS.validate_json(a, strict=True), ASSOCIATION.validate_json(b, strict=True)
    if (
        PAIRS.dump_json(captured).decode() != a
        or ASSOCIATION.dump_json(state).decode() != b
        or state.input_digest != digest(a)
    ):
        raise invalid("association canonical encoding or capture binding differs")
    return captured, state


def _decode_forecast(parts: tuple[ExchangePart, ...]) -> tuple[TrainingCapture, ForecastState]:
    a = next(
        p.table["training_inputs__retained"][0].as_py()
        for p in parts
        if p.role == "training_inputs"
    )
    b = next(
        p.table["forecast_state__retained"][0].as_py() for p in parts if p.role == "forecast_state"
    )
    captured, state = TRAINING.validate_json(a, strict=True), FORECAST.validate_json(b, strict=True)
    future = next(
        p.table["future_cells__retained"][0].as_py() for p in parts if p.role == "future_cells"
    )
    if (
        TRAINING.dump_json(captured).decode() != a
        or FORECAST.dump_json(state).decode() != b
        or FUTURE.dump_json(captured.future).decode() != future
        or state.input_digest != digest(a)
    ):
        raise invalid("forecast canonical encoding, future or capture binding differs")
    return captured, state


def decode_pairs(parts: tuple[ExchangePart, ...]) -> tuple[PairCapture, AssociationState]:
    try:
        return _decode_pairs(parts)
    except (ValueError, ValidationError, StopIteration, KeyError, IndexError) as error:
        raise invalid("invalid retained association capture/state") from error


def decode_forecast(parts: tuple[ExchangePart, ...]) -> tuple[TrainingCapture, ForecastState]:
    try:
        return _decode_forecast(parts)
    except (ValueError, ValidationError, StopIteration, KeyError, IndexError) as error:
        raise invalid("invalid retained forecast capture/state/future") from error


def views(parts: tuple[ExchangePart, ...]) -> pa.Table:
    state = (
        decode_pairs(parts)[1]
        if any(p.role == "pair_inputs" for p in parts)
        else decode_forecast(parts)[1]
    )
    return load(state.views)


def _primary(table: pa.Table, field: str) -> pa.Table:
    if field == "coefficient":
        return table.select(
            (
                *[k for k in table.column_names if k not in ("value", "cell_tag", "cell_reason")],
                "value",
                "cell_tag",
                "cell_reason",
            )
        )
    if field == "selected":
        return pa.table(
            {
                **{
                    k: table[k]
                    for k in table.column_names
                    if k not in ("value", "cell_tag", "cell_reason")
                },
                "value": table["selected"],
                "cell_tag": pa.array(["defined"] * table.num_rows, pa.string()),
                "cell_reason": pa.array([None] * table.num_rows, pa.string()),
            }
        )
    keys = tuple(k for k in table.column_names if k.startswith("key_"))
    return pa.table(
        {
            **{k: table[k] for k in keys},
            "horizon": table["horizon"],
            "series": table["series"],
            "value": table[field],
            "cell_tag": pa.array(["defined"] * table.num_rows, pa.string()),
            "cell_reason": pa.array([None] * table.num_rows, pa.string()),
        }
    )


def _result(
    node: MethodNode, primary: pa.Table, parts: tuple[ExchangePart, ...], binding: str
) -> ExchangeResult:
    keys = tuple(k for k in primary.column_names if k.startswith("key_"))
    kind = REGISTRY.lookup(node.method).semantics.persistent_state_kind
    status = primary.select((*keys, "status")) if kind == "spearman" else None
    return from_arrow(
        primary,
        ExchangeContract(
            node.signature,
            node.method,
            binding,
            primary.schema,
            keys,
            tuple(
                PartContract(
                    p.role,
                    p.table.schema,
                    tuple(k for k in p.table.column_names if k.startswith("key_")),
                )
                for p in parts
            ),
            (("undefined", ("insufficient_pairs", "constant_a", "constant_b", "constant_both")),),
            kind or "none",
            status.schema if status is not None else None,
        ),
        parts=parts,
        method_state=status,
    )


def execute(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    params = node.parameters
    if isinstance(params, (AssociationRead, ForecastRead)):
        source = inputs[0]
        all_views = views(source.parts)
        keys = source.contract.key_fields
        mapping = index(all_views, keys)
        selected = all_views.take(
            pa.array([mapping[k] for k in index(source.primary, keys)], pa.int64())
        )
        return _result(node, _primary(selected, params.field), source.parts, binding)
    parts: tuple[ExchangePart, ...]
    if isinstance(params, AssociationFit):
        declaration = next(p for p in node.signature.parts if isinstance(p, PairInputsPart))
        saved_pairs = PairCapture(
            declaration,
            tuple(capture(v, t) for v, t in zip(inputs, params.input_types, strict=True)),
        )
        state_a = association_state(saved_pairs)
        primary = _primary(load(state_a.views), "coefficient")
        parts = (
            _payload("pair_inputs", PAIRS.dump_json(saved_pairs).decode()),
            _payload("association_state", ASSOCIATION.dump_json(state_a).decode()),
        )
        original_digest = state_a.input_digest
        eligible = sum(c.score.status == "valid" for c in state_a.candidates)
    else:
        assert isinstance(params, ForecastFit)
        declaration_f = next(p for p in node.signature.parts if isinstance(p, TrainingInputsPart))
        future = next(p for p in node.signature.parts if isinstance(p, FutureCellsPart))
        saved_training = TrainingCapture(
            declaration_f, capture(inputs[0], params.input_type), future
        )
        try:
            state_f = forecast_state(saved_training)
        except (ValueError, OverflowError) as error:
            raise fail(
                "r8.forecast_history" if isinstance(error, ValueError) else "r8.numeric_overflow",
                "forecast",
                params.forecast_id,
                "complete admissible history and representable future values",
                str(error),
                "Use the original full finite time observation with sufficient history and approved future coverage.",
                method="forecast." + params.model + "@v1",
            ) from error
        primary = _primary(load(state_f.views), "prediction")
        parts = (
            _payload("training_inputs", TRAINING.dump_json(saved_training).decode()),
            _payload("forecast_state", FORECAST.dump_json(state_f).decode()),
            _payload("future_cells", FUTURE.dump_json(future).decode()),
        )
        original_digest = state_f.input_digest
        eligible = primary.num_rows
    from marivo.analysis.core.model import FindingPolicyPart
    from marivo.analysis.materialization.graph_findings import CAP, POLICY, Policy

    policy = next(p for p in node.signature.parts if isinstance(p, FindingPolicyPart))
    parts += (
        _payload(
            "finding_policy",
            POLICY.dump_json(
                Policy(
                    policy.producer,
                    "v1",
                    policy.extractor,
                    policy.policy,
                    ("capture:" + original_digest,),
                    eligible,
                    min(CAP, eligible),
                    max(eligible - CAP, 0),
                )
            ).decode(),
        ),
    )
    original = saved_pairs.inputs[0] if isinstance(params, AssociationFit) else saved_training.input
    if original.signature.domain.time_grid is not None:
        parts += (_grid_cells(original),)
    return _result(node, primary, parts, binding)


def _validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    """Validate original equations and current views without rerunning any estimator."""
    check()
    field: str
    table_fields: tuple[str, ...]
    association_declaration = next(
        (p for p in contract.signature.parts if isinstance(p, PairInputsPart)), None
    )
    if association_declaration is not None:
        captured, state_a = decode_pairs(parts)
        if captured.declaration != association_declaration:
            raise invalid("association input/scope declaration differs")
        _, vectors, sequences = _pair_indices(captured)
        expected_keys = {
            (a, b, digest(repr(s)), lag)
            for a, b in combinations(range(len(vectors)), 2)
            for s, _ in sequences
            for lag in association_declaration.lags
        }
        if (
            {(c.a, c.b, c.series, c.lag) for c in state_a.candidates} != expected_keys
            or len(state_a.candidates) != len(expected_keys)
            or len(expected_keys) > 4096
        ):
            raise invalid("association full candidate domain differs")
        groups: dict[tuple[int, int, str], list[Candidate]] = {}
        for c in state_a.candidates:
            check()
            sequence = next(positions for s, positions in sequences if digest(repr(s)) == c.series)
            aligned = tuple(
                (sequence[i], sequence[i + c.lag])
                for i in range(len(sequence))
                if 0 <= i + c.lag < len(sequence)
            )
            complete = tuple(
                (i, j)
                for i, j in aligned
                if vectors[c.a][i] is not None and vectors[c.b][j] is not None
            )
            if (
                c.input_count != len(sequence)
                or c.matched != len(aligned)
                or c.complete_pairs != len(complete)
                or c.null_pairs + c.complete_pairs != c.matched
                or c.input_count - c.matched != c.boundary_drop
                or tuple(zip(c.left_indices, c.right_indices, strict=True)) != complete
                or c.key
                != digest(repr((association_declaration.association_id, c.a, c.b, c.series, c.lag)))
            ):
                raise invalid("association counts, pairing or candidate identity differs")
            x = tuple(vectors[c.a][i] for i, _ in complete)
            y = tuple(vectors[c.b][j] for _, j in complete)
            status = (
                "insufficient_pairs"
                if len(x) < 2
                else "constant_both"
                if len(set(x)) == len(set(y)) == 1
                else "constant_a"
                if len(set(x)) == 1
                else "constant_b"
                if len(set(y)) == 1
                else "valid"
            )
            if c.score.status != status or (status == "valid") != (c.score.coefficient is not None):
                raise invalid("association status contradicts original pair values")
            if status == "valid":
                xx, yy = tuple(v for v in x if v is not None), tuple(v for v in y if v is not None)
                if association_declaration.method == "spearman":
                    for original, retained in ((xx, c.score.ranks_a), (yy, c.score.ranks_b)):
                        _verify_ranks(original, retained)
                    xx, yy = (
                        tuple(v.value() for v in c.score.ranks_a),
                        tuple(v.value() for v in c.score.ranks_b),
                    )
                if association_declaration.method == "kendall":
                    counts = [0, 0, 0, 0]
                    for i in range(len(xx)):
                        for j in range(i):
                            if j % 256 == 0:
                                check()
                            u, v = xx[i] - xx[j], yy[i] - yy[j]
                            if u == v == 0:
                                continue
                            counts[2 if u == 0 else 3 if v == 0 else 0 if u * v > 0 else 1] += 1
                    ca, di, ta, tb = counts
                    num, rad = Fraction(ca - di), Fraction((ca + di + ta) * (ca + di + tb))
                    if tuple(counts) != (
                        c.score.concordant,
                        c.score.discordant,
                        c.score.ties_a,
                        c.score.ties_b,
                    ):
                        raise invalid("Kendall paired counts differ")
                else:
                    mx, my = sum(xx, Fraction()) / len(xx), sum(yy, Fraction()) / len(yy)
                    num = sum(
                        ((a - mx) * (b - my) for a, b in zip(xx, yy, strict=True)), Fraction()
                    )
                    rad = sum(((a - mx) ** 2 for a in xx), Fraction()) * sum(
                        ((b - my) ** 2 for b in yy), Fraction()
                    )
                if (
                    c.score.numerator.value() != num
                    or c.score.radicand.value() != rad
                    or c.score.coefficient is None
                    or not -1 <= c.score.coefficient <= 1
                ):
                    raise invalid("association centered equation or coefficient differs")
                if c.score.root is not None:
                    cert = c.score.root
                    if not numeric.verify_root(cert, rad):
                        raise invalid("association root certificate differs")
                    bounds = sorted(
                        (num / Fraction(Decimal(cert.lower)), num / Fraction(Decimal(cert.upper)))
                    )
                    if (
                        float(bounds[0]) != c.score.coefficient
                        or float(bounds[1]) != c.score.coefficient
                    ):
                        raise invalid("association coefficient finish differs")
                else:
                    a_root, b_root = math.isqrt(rad.numerator), math.isqrt(rad.denominator)
                    if num == 0:
                        expected_coefficient = 0.0
                    elif a_root * a_root == rad.numerator and b_root * b_root == rad.denominator:
                        expected_coefficient = float(num / Fraction(a_root, b_root))
                    else:
                        raise invalid("association irrational result lacks a root certificate")
                    if c.score.coefficient != expected_coefficient:
                        raise invalid("association rational finish differs")
            elif c.selected:
                raise invalid("invalid association candidate selected")
            groups.setdefault((c.a, c.b, c.series), []).append(c)
        for current in groups.values():
            valid = [c for c in current if c.score.status == "valid"]
            if not valid or [c.key for c in current if c.selected] != [
                min(valid, key=lambda c: (-abs(c.score.coefficient or 0), abs(c.lag), c.lag)).key
            ]:
                raise invalid("association selected lag differs from original full search")
        all_views = load(state_a.views)
        for row, c in zip(all_views.to_pylist(), state_a.candidates, strict=True):
            if (
                (row["cell_tag"], row["cell_reason"]) != ("defined", None)
                if c.score.status == "valid"
                else (row["cell_tag"], row["cell_reason"]) != ("undefined", c.score.status)
            ):
                raise invalid("association frozen Cell status differs")
            if (
                row["key_0"],
                row["value"],
                row["selected"],
                row["status"],
                row["pair_a"],
                row["pair_b"],
                row["series"],
                row["lag"],
                row["input_count"],
                row["matched_count"],
                row["boundary_drop_count"],
                row["null_pair_count"],
                row["complete_pair_count"],
            ) != (
                c.key,
                c.score.coefficient,
                c.selected,
                c.score.status,
                c.a,
                c.b,
                c.series,
                c.lag,
                c.input_count,
                c.matched,
                c.boundary_drop,
                c.null_pairs,
                c.complete_pairs,
            ):
                raise invalid("association frozen view differs from retained candidate")
        declared_state = next(
            p for p in contract.signature.parts if isinstance(p, AssociationStatePart)
        )
        table_fields = declared_state.table_views
        eligible = sum(c.score.status == "valid" for c in state_a.candidates)
        input_digest = state_a.input_digest
        field = "coefficient" if declared_state.view == "result" else declared_state.view
    else:
        captured_f, state_f = decode_forecast(parts)
        declaration_f = next(
            p for p in contract.signature.parts if isinstance(p, TrainingInputsPart)
        )
        future_f = next(p for p in contract.signature.parts if isinstance(p, FutureCellsPart))
        if (
            captured_f.declaration != declaration_f
            or captured_f.future != future_f
            or captured_f.input.signature.domain != declaration_f.input_domain
        ):
            raise invalid("forecast input/model/future declaration differs")
        table, vector = _input(
            captured_f.input, method="forecast." + declaration_f.model + "@v1", forecast_input=True
        )
        sequences = _series(captured_f.input, table)
        if len(sequences) != len(state_f.series) or not numeric.verify_quantile(
            state_f.quantile, declaration_f.level
        ):
            raise invalid("forecast series count or normal quantile certificate differs")
        all_views = load(state_f.views)
        rows = all_views.to_pylist()
        output = unit_type(parse_type(declaration_f.input_type))
        for (series_key, positions), series_record in zip(sequences, state_f.series, strict=True):
            t = series_record.training
            values = tuple(vector[i] for i in positions)
            vv = tuple(v for v in values if v is not None)
            distance = declaration_f.season or 1
            slope = (
                (vv[-1] - vv[0]) / (len(vv) - 1) if declaration_f.model == "drift" else Fraction()
            )
            innovations = tuple(vv[i] - vv[i - distance] - slope for i in range(distance, len(vv)))
            df = len(vv) - 2 if declaration_f.model == "drift" else len(vv) - distance
            if (
                series_record.identity != digest(repr(series_key))
                or series_record.indices != positions
                or t.model != declaration_f.model
                or t.season != declaration_f.season
                or t.n != len(vv)
                or df <= 0
                or t.df != df
                or t.slope.value() != slope
                or tuple(v.value() for v in t.innovations) != innovations
                or t.sigma2.value() != sum((v * v for v in innovations), Fraction()) / df
                or t.exact_zero != all(v == 0 for v in innovations)
                or len(series_record.points) != len(future_f.grid.cells)
            ):
                raise invalid("forecast per-series innovation/df/variance facts differ")
            for h, point in enumerate(series_record.points, 1):
                check()
                prediction = (
                    vv[-distance + (h - 1) % distance]
                    if t.model == "seasonal_naive"
                    else vv[-1] + h * slope
                )
                variance = t.sigma2.value() * (
                    (h - 1) // distance + 1
                    if t.model == "seasonal_naive"
                    else h * (1 + Fraction(h, t.n - 1))
                    if t.model == "drift"
                    else h
                )
                if (
                    point.h != h
                    or point.point.value() != prediction
                    or point.variance.value() != variance
                    or not numeric.verify_root(point.root, variance)
                ):
                    raise invalid("forecast horizon equation or root enclosure differs")
                row = next(
                    r for r in rows if r["series"] == series_record.identity and r["horizon"] == h
                )
                anchor = next(
                    i
                    for i, c in enumerate(declaration_f.input_domain.instance_key)
                    if c.role == "anchor"
                )
                expected_key = (
                    *series_key[:anchor],
                    future_f.grid.cells[h - 1].identity,
                    *series_key[anchor:],
                )
                low = Fraction(Decimal(state_f.quantile.lower)) * Fraction(
                    Decimal(point.root.lower)
                )
                high = Fraction(Decimal(state_f.quantile.upper)) * Fraction(
                    Decimal(point.root.upper)
                )
                if (
                    tuple(row[k] for k in captured_f.input.keys) != expected_key
                    or row["prediction"] != finish(prediction, output)
                    or any(
                        finish(prediction - margin, output) != row["lower"]
                        or finish(prediction + margin, output) != row["upper"]
                        for margin in (low, high)
                    )
                ):
                    raise invalid("forecast rounded prediction/interval differs")
        if len(rows) != len(state_f.series) * len(future_f.grid.cells):
            raise invalid("forecast full future output count differs")
        declared_f = next(p for p in contract.signature.parts if isinstance(p, ForecastStatePart))
        table_fields = declared_f.table_views
        eligible = len(rows)
        input_digest = state_f.input_digest
        field = "prediction" if declared_f.view == "result" else declared_f.view
    from marivo.analysis.materialization.graph_findings import CAP, _policy

    original_input = captured.inputs[0] if association_declaration is not None else captured_f.input
    declaration_grid = next(
        (p for p in contract.signature.parts if isinstance(p, GridCellsPart)), None
    )
    if original_input.signature.domain.time_grid is not None:
        actual_grid = next(p.table for p in parts if p.role == "grid_cells")
        if (
            declaration_grid is None
            or declaration_grid.input_domain != original_input.signature.domain
            or not actual_grid.equals(_grid_cells(original_input).table)
        ):
            raise invalid("statistical original complete history grid mapping differs")
    elif declaration_grid is not None:
        raise invalid("non-temporal statistical input declares a time grid mapping")

    policy = _policy(parts)
    declaration_policy = next(
        p for p in contract.signature.parts if isinstance(p, FindingPolicyPart)
    )
    if (
        (policy.producer, policy.state_version, policy.extractor, policy.policy)
        != (
            declaration_policy.producer,
            "v1",
            declaration_policy.extractor,
            declaration_policy.policy,
        )
        or policy.ordered_input_bindings != ("capture:" + input_digest,)
        or (policy.eligible, policy.emitted, policy.truncated)
        != (eligible, min(CAP, eligible), max(0, eligible - CAP))
    ):
        raise invalid("statistical Finding policy differs from original full scope")
    # Display and row reducers retain scope only when their own typed part owners do so.
    mapping = index(all_views, contract.key_fields)
    current_keys = index(primary, contract.key_fields)
    if not set(current_keys) <= set(mapping):
        raise invalid("statistical current keys escape original output domain")
    selected = all_views.take(pa.array([mapping[k] for k in current_keys], pa.int64()))
    if any(isinstance(p, TableFitsPart) for p in contract.signature.parts):
        return  # Each captured table column is verified by the table owner.
    if table_fields:
        expected_primary = selected.select(contract.key_fields)
        for i, view in enumerate(table_fields):
            for name in ("value", "cell_tag", "cell_reason"):
                expected_primary = expected_primary.append_column(
                    f"column_{i}__{name}", _primary(selected, view)[name]
                )
    elif (
        any(isinstance(p, DisplayPart) for p in contract.signature.parts)
        and contract.signature.quantity is not None
        and contract.signature.quantity.method_version == "display.ranks@v1"
    ):
        return  # The display owner separately verifies ranks against original ordering witnesses.
    else:
        expected_primary = _primary(selected, field)
    if not set(primary.column_names) <= set(expected_primary.column_names) or not primary.equals(
        expected_primary.select(primary.column_names)
    ):
        raise invalid("statistical current owned view differs from frozen original output")


def _verify_ranks(original: tuple[Fraction, ...], retained: tuple[RationalFact, ...]) -> None:
    """Check frozen average ranks against ordered tie blocks without fitting new ranks."""
    if len(retained) != len(original):
        raise invalid("missing global rank witnesses")
    check()
    ordered = sorted(zip(original, retained, strict=True), key=lambda pair: pair[0])
    check()
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][0] == ordered[start][0]:
            check()
            end += 1
        expected = Fraction(start + end + 1, 2)
        for _, witness in ordered[start:end]:
            check()
            if witness.value() != expected:
                raise invalid("global average rank witness differs")
        start = end


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    """Reject malformed retained facts through the shared integrity error owner."""
    try:
        _validate(contract, primary, parts)
    except (ValueError, TypeError, ArithmeticError, StopIteration, IndexError, KeyError) as error:
        raise invalid("invalid frozen statistical equation or view") from error
