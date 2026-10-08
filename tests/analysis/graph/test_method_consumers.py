"""Public method counterexamples on exact physical source shapes."""

import json
import os
import subprocess
import sys
import warnings
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Literal

import ibis
import pyarrow as pa
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.source_time import source_time
from marivo.analysis.core.model import DomainKind
from marivo.analysis.materialization.deviation_execution import _decode as decode_deviation
from marivo.analysis.materialization.deviation_execution import load as load_retained_table
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.materialization.statistical_execution import decode_forecast
from marivo.analysis.methods.builtin import implementations
from marivo.analysis.methods.deviation_physical import specialize as specialize_deviation
from marivo.analysis.methods.physical import (
    DecimalType,
    NoTime,
    QualificationKey,
    Qualified,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.methods.statistical_physical import specialize
from marivo.datasource import adapters
from marivo.datasource.adapters import PhysicalRequirement, _Cursor, provider_for
from marivo.datasource.errors import DatasourceConnectionError, DatasourceSourceCapabilityError
from marivo.refs import RefPayloadV1
from marivo.semantic.ir import TargetDimensionContract, TimestampParse
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.statistics.deviation_oracle import expected as deviation_oracle
from tests.analysis.statistics.test_analysis_statistics_kernel import _oracle_cdf
from tests.datasource.source_cases import SourceData, source_case
from tests.support.json import Json, digest, encode, key_json, obj, read
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
def test_clickhouse_physical_utc_window_is_independent_of_session_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.datasource.environment import clickhouse_analysis as ch

    data = SourceData(
        "id BIGINT, happened TIMESTAMP",
        "(1,'2026-08-01 00:00:00')",
        "id Int64, happened DateTime64(6, 'UTC')",
        [{"id": 1, "happened": datetime(2026, 8, 1, tzinfo=timezone.utc)}],
    )
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data=data) as case:
        bound = case.session.bind(case.source, source_identity="r93.utc")
        axis = TargetDimensionContract(
            RefPayloadV1.from_ref(ms.ref.time_dimension("sales.facts.happened")),
            RefPayloadV1.from_ref(ms.ref.entity("sales.facts")),
            "happened",
            "timestamp",
            False,
            True,
            "second",
            True,
            "UTC",
            TimestampParse(timezone="UTC"),
        )
        value, authority = source_time(
            bound.relation.happened,
            axis,
            boundary_timezone="UTC",
            read_timezone=None,
            engine="clickhouse",
        )
        assert authority.source == "physical"
        start = ibis.literal(datetime(2026, 8, 1), type=value.type())
        end = ibis.literal(datetime(2026, 8, 2), type=value.type())
        expression = bound.relation.filter((value >= start) & (value < end)).select("id")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("r93.utc", 1, frozenset({"scan", "filter", "project"}))
        )
        read = case.session.compile(
            qualified,
            expression,
            purpose="r93.utc",
            expected_schema=expression.schema().to_pyarrow(),
        )
        # Test-only independent connection settings do not rewrite product SQL.
        observations: list[dict[str, object]] = []
        with ch.connection(admin=True) as observer:
            for zone in ("UTC", "America/New_York", "Asia/Shanghai"):
                rows = observer.query(read.sql, settings={"session_timezone": zone}).result_rows
                assert rows == [(1,)]
                observations.append({"session_timezone": zone, "rows": rows})
        evidence_dir = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence_dir:
            Path(evidence_dir, "clickhouse-utc-window.json").write_text(
                json.dumps(
                    {
                        "environment": case.environment,
                        "actual_sql": read.sql,
                        "observations": observations,
                        "oracle": [[1]],
                        "boundary": "Independent test connection settings verify physical UTC window semantics; not complete C06 or cancellation qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
def test_mysql_reader_initialization_warning_is_a_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ibis.backends.mysql import Backend

    with source_case("mysql", "table", tmp_path, monkeypatch) as case:

        def warning(_owner: object) -> None:
            warnings.warn("Unable to set session timezone to UTC: synthetic failure", stacklevel=2)

        monkeypatch.setattr(Backend, "_post_connect", warning)
        with (
            pytest.raises(DatasourceConnectionError) as failure,
            provider_for("mysql").open(case.session.datasource),
        ):
            pytest.fail("A failed UTC reader was admitted")
        assert failure.value.received == "timezone initialization warning"


@pytest.mark.parametrize(
    "value", [Decimal("1.5"), Decimal("NaN"), Decimal("Infinity"), Decimal(2**63), 1.0]
)
@pytest.mark.parametrize("backend", ["postgres", "mysql"])
def test_integer_decode_rejects_inexact_or_overflow(value: Decimal | float, backend: str) -> None:
    with pytest.raises(DatasourceSourceCapabilityError):
        adapters._exact_array([value], pa.field("value", pa.int64()), backend_name=backend)


@pytest.mark.parametrize("backend", ["postgres", "mysql"])
def test_integer_decode_preserves_full_width(backend: str) -> None:
    values = [Decimal(-(2**63)), Decimal(2**63 - 1)]
    assert adapters._exact_array(
        list(values), pa.field("value", pa.int64()), backend_name=backend
    ).to_pylist() == [-(2**63), 2**63 - 1]


@pytest.mark.parametrize("value", [2, -1, 0.0, "1"])
def test_mysql_boolean_carrier_rejects_non_boolean_domain(value: int | float | str) -> None:
    with pytest.raises(DatasourceSourceCapabilityError):
        adapters._exact_array([value], pa.field("complete", pa.bool_()), backend_name="mysql")


def test_mysql_utc_timestamp_carrier_preserves_microseconds() -> None:
    value = "2026-08-01 00:00:00.123456"
    assert adapters._exact_array(
        [value], pa.field("time", pa.timestamp("us", "UTC")), backend_name="mysql"
    ).to_pylist() == [datetime(2026, 8, 1, microsecond=123456, tzinfo=timezone.utc)]
    with pytest.raises(DatasourceSourceCapabilityError):
        adapters._exact_array(
            [value], pa.field("time", pa.timestamp("us", "America/New_York")), backend_name="mysql"
        )


@pytest.mark.parametrize(
    "types", [(DecimalType(18, 6),) * 2, (ScalarType("float64"),) * 2, (ScalarType("int64"),) * 3]
)
def test_sqlite_association_does_not_grant_untested_numeric_keys(
    types: tuple[ScalarType | DecimalType, ...],
) -> None:
    method = MethodKey("association.pearson")
    shape = SourceShape("sqlite", "table", "native", TimeShape("instant", "us", "UTC"))
    domain: DomainKind = "entity"
    domains = (domain,) * len(types)
    key = QualificationKey(method, types, domains, shape, "ibis_python")
    candidates = [item for item in implementations(method) if item.key.shape == shape]
    assert candidates
    assert all(specialize(item, key).key != key for item in candidates)


@pytest.mark.parametrize(
    "types", [(DecimalType(18, 6),), (ScalarType("float64"),), (ScalarType("int64"),) * 2]
)
def test_sqlite_deviation_does_not_grant_untested_numeric_keys(
    types: tuple[ScalarType | DecimalType, ...],
) -> None:
    method = MethodKey("deviation.zscore")
    shape = SourceShape("sqlite", "table", "native", NoTime())
    domain: DomainKind = "entity"
    key = QualificationKey(method, types, (domain,) * len(types), shape, "ibis_python")
    candidates = [item for item in implementations(method) if item.key.shape == shape]
    assert candidates
    assert all(specialize_deviation(item, key).key != key for item in candidates)


@pytest.mark.parametrize("backend", ["sqlite", "clickhouse", "mysql", "postgres", "trino"])
@pytest.mark.parametrize(
    "method",
    ["forecast.naive", "forecast.drift", "forecast.seasonal_naive", "time.runs", "time.runs_read"],
)
def test_deviation_registration_does_not_qualify_other_consumers(
    method: Literal[
        "forecast.naive", "forecast.drift", "forecast.seasonal_naive", "time.runs", "time.runs_read"
    ],
    backend: str,
) -> None:
    assert not any(
        isinstance(item.key.shape, SourceShape)
        and item.key.shape.backend == backend
        and isinstance(item.key.shape.time, NoTime)
        for item in implementations(MethodKey(method))
    )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
@pytest.mark.parametrize(
    "method",
    ["pearson", "spearman", "kendall", "zscore", "mad", "naive", "drift", "seasonal_naive", "runs"],
)
def test_source_statistic(
    backend: str,
    method: Literal[
        "pearson",
        "spearman",
        "kendall",
        "zscore",
        "mad",
        "naive",
        "drift",
        "seasonal_naive",
        "runs",
    ],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    submitted: list[str] = []
    owners: list[adapters.SourceSession] = []
    native = adapters._native_cursor
    open_session = adapters.SourceSession.__enter__

    def capture(owner: BaseBackend, name: str, sql: str) -> _Cursor:
        submitted.append(sql)
        return native(owner, name, sql)

    def entered(owner: adapters.SourceSession) -> adapters.SourceSession:
        result = open_session(owner)
        owners.append(owner)
        return result

    monkeypatch.setattr(adapters, "_native_cursor", capture)
    monkeypatch.setattr(adapters.SourceSession, "__enter__", entered)
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), happened TIMESTAMP",
        "(9007199254740992,1,'a','2026-08-01 00:00:00'),"
        "(9007199254740993,2,'a','2026-08-01 00:00:00'),"
        "(9007199254740993,1,'b','2026-08-01 00:00:00')",
        "id Int64, revision Int64, tenant String, happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "revision": revision,
                "tenant": tenant,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for identity, revision, tenant in (
                (9007199254740992, 1, "a"),
                (9007199254740993, 2, "a"),
                (9007199254740993, 1, "b"),
            )
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    if backend == "trino":
        data = replace(
            data,
            values=data.values.replace("'2026-08-01 00:00:00'", "TIMESTAMP '2026-08-01 00:00:00'"),
        )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        case.environment["profile"] = profile
        from marivo.datasource.ir import TableSourceIR

        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_R93_READER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_R93_READER"
        argument_text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        source_text = f"md.table({case.source.table!r}, database={case.source.database!r})"
        semantic_project_factory(
            {
                "datasources/warehouse.py": (
                    "import marivo.datasource as md\n"
                    f"md.{backend}(name='warehouse', {argument_text})\n"
                ),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
                "sales/models.py": (
                    "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                    f"facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source={source_text}, primary_key=['tenant', 'id', 'revision'])\n"
                    "x = ms.measure_column(name='x', entity=facts, column='id', additivity=ms.additive_all())\n"
                    "y = ms.measure_column(name='y', entity=facts, column='revision', additivity=ms.additive_all())\n"
                    "event_time = ms.time_dimension_column(name='event_time', entity=facts, column='happened', granularity='second', parse=ms.timestamp(timezone='UTC'), is_default=True)\n"
                    "a = ms.aggregate(name='a', measure=x, agg='sum')\n"
                    "b = ms.aggregate(name='b', measure=y, agg='sum', empty=ms.empty.zero())\n"
                ),
            }
        )
        session = mv.session.get_or_create("r93", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        during = mv.time_scope(start="2026-08-01", end="2026-08-02")
        a = members.observe(
            ms.ref.metric("sales.a"), during=during, by=(ms.ref.entity("sales.facts"),)
        )
        b = members.observe(
            ms.ref.metric("sales.b"), during=during, by=(ms.ref.entity("sales.facts"),)
        )
        if method in ("naive", "drift", "seasonal_naive", "runs"):
            grid = mv.time_grid(
                during=mv.time_scope(start="2026-08-01", end="2026-08-07"), grain=mv.grain("day")
            )
            daily = members.observe(
                ms.ref.metric("sales.b"), during=grid, by=(ms.ref.entity("sales.facts"),)
            )
            if method == "runs":
                runs = daily.runs(where=daily.value.gt(0))
                segments = runs.execute()
                assert segments.count.to_pandas().value.tolist() == [1, 1, 1]
                assert segments.duration.to_pandas().value.tolist() == [timedelta(days=1)] * 3
                assert runs.count.execute().to_pandas().value.tolist() == [1, 1, 1]
                assert segments._dataset is not None
                parts = {part.role for part in segments._dataset.verified().parts}
                assert {"condition_cells", "run_cells", "grid_cells", "subject"} <= parts
                family = "time"
                dataset, node = segments._dataset, segments._node.definition
                oracle: dict[str, object] = {
                    "positive_cells": 3,
                    "counts": [1, 1, 1],
                    "duration_us": [86400000000] * 3,
                }
            else:
                model = (
                    mv.naive()
                    if method == "naive"
                    else mv.drift()
                    if method == "drift"
                    else mv.seasonal_naive(periods=2)
                )
                history = daily.group_by(grid).rollup()
                forecast = history.forecast(horizon=mv.periods(2), model=model)
                predicted = forecast.execute()
                points = [-0.8, -1.6] if method == "drift" else [0.0, 0.0]
                assert predicted.prediction.to_pandas().value.tolist() == points
                assert forecast.prediction.execute().to_pandas().value.tolist() == points
                assert predicted._dataset is not None
                retained_forecast = predicted._dataset.verified()
                parts = {part.role for part in retained_forecast.parts}
                assert {"training_inputs", "forecast_state", "future_cells"} <= parts
                captured, forecast_state = decode_forecast(retained_forecast.parts)
                assert len(forecast_state.series) == 1
                series = forecast_state.series[0]
                original = load_retained_table(captured.input.primary)
                assert [original["value"][index].as_py() for index in series.indices] == [
                    4,
                    0,
                    0,
                    0,
                    0,
                    0,
                ]
                slope = Fraction(-4, 5) if method == "drift" else Fraction()
                distance = 2 if method == "seasonal_naive" else 1
                training = (Fraction(4),) + (Fraction(),) * 5
                innovations = tuple(
                    training[i] - training[i - distance] - slope for i in range(distance, 6)
                )
                df = 4 if method in ("drift", "seasonal_naive") else 5
                sigma2 = sum((value * value for value in innovations), Fraction()) / df
                assert series.training.n == 6 and series.training.df == df
                assert tuple(value.value() for value in series.training.innovations) == innovations
                assert series.training.slope.value() == slope
                assert series.training.sigma2.value() == sigma2
                variances = tuple(
                    sigma2
                    * (
                        1
                        if method == "seasonal_naive"
                        else h * (1 + Fraction(h, 5))
                        if method == "drift"
                        else h
                    )
                    for h in (1, 2)
                )
                assert tuple(point.variance.value() for point in series.points) == variances
                # Invert the independent defining integral, then round each exact
                # point/variance endpoint once to its published float64 carrier.
                with localcontext() as context:
                    context.prec = 220
                    probability = (1 + Decimal.from_float(0.95)) / 2
                    lower, upper = Decimal(1), Decimal(3)
                    for _ in range(220):
                        middle = (lower + upper) / 2
                        if _oracle_cdf(middle) < probability:
                            lower = middle
                        else:
                            upper = middle
                    quantile = (lower + upper) / 2
                    endpoints = []
                    for h, variance in enumerate(variances, start=1):
                        point = Decimal(slope.numerator * h) / slope.denominator
                        width = (
                            quantile * (Decimal(variance.numerator) / variance.denominator).sqrt()
                        )
                        endpoints.append((float(point - width), float(point + width)))
                assert predicted.lower.to_pandas().value.tolist() == [v[0] for v in endpoints]
                assert predicted.upper.to_pandas().value.tolist() == [v[1] for v in endpoints]
                family = "forecast"
                dataset, node = predicted._dataset, predicted._node.definition
                oracle = {
                    "training": [4, 0, 0, 0, 0, 0],
                    "prediction": points,
                    "period": 2 if method == "seasonal_naive" else 1,
                }
        elif method in ("zscore", "mad"):
            deviation = a.deviation(method=method)
            fitted = deviation.execute()
            observed = tuple(int(value) for value in fitted.observed.to_pandas().value)
            assert sorted(observed) == [2**53, 2**53 + 1, 2**53 + 1]
            center, scale, branch, scores = deviation_oracle(observed, method)
            assert fitted.score.to_pandas().value.tolist() == list(scores)
            assert deviation.score.execute().to_pandas().value.tolist() == list(scores)
            assert fitted._dataset is not None
            retained = fitted._dataset.verified()
            parts = {part.role for part in retained.parts}
            assert {"fit_inputs", "fit_state"} <= parts
            inputs, state = decode_deviation(retained.parts)
            assert inputs.fit_id == state.fit_id
            assert len(state.partitions) == 1
            partition = state.partitions[0]
            assert (partition.defined, partition.null, partition.undefined, partition.unknown) == (
                3,
                0,
                0,
                0,
            )
            assert sorted(partition.indices) == [0, 1, 2]
            fit = partition.fit
            assert fit.center is not None and fit.raw_scale is not None
            assert (fit.center.value(), fit.raw_scale.value(), fit.branch) == (
                center,
                scale,
                branch,
            )
            family = "deviation"
            dataset, node = fitted._dataset, fitted._node.definition
            oracle = {
                "originals": list(observed),
                "center": str(center),
                "scale": str(scale),
                "branch": branch,
                "scores": list(scores),
            }
        else:
            association_method: Literal["pearson", "spearman", "kendall"] = (
                "pearson"
                if method == "pearson"
                else "spearman"
                if method == "spearman"
                else "kendall"
            )
            logical = a.correlate(b, method=association_method)
            result = logical.execute()
            # Original exact pairs: (2**53, 1), (2**53+1, 2), (2**53+1, 1).
            expected = 0.5
            assert result.coefficient.to_pandas().value.tolist() == [expected]
            assert logical.coefficient.execute().to_pandas().value.tolist() == [expected]
            assert logical.selected.execute().to_pandas().value.tolist() == [True]
            assert result._dataset is not None
            parts = {part.role for part in result._dataset.verified().parts}
            assert {"pair_inputs", "association_state"} <= parts
            family = "association"
            dataset, node = result._dataset, result._node.definition
            oracle = {
                "pairs": [[2**53, 1], [2**53 + 1, 2], [2**53 + 1, 1]],
                "coefficient": expected,
            }
        selected = next(
            item
            for item in descriptor_plan(dataset.artifact.descriptor, node).physical_requirements
            if item.key.method.name == family + "." + method
        )
        assert isinstance(selected.key.shape, SourceShape) and selected.key.shape.backend == backend
        assert selected.key.route == "ibis_python"
        assert isinstance(selected.implementation.qualification, Qualified)
        submissions = [
            asdict(item)
            for owner in owners
            if owner is not case.session
            for item in owner.submissions
        ]
        assert submissions and submitted
        assert all(owner._closed for owner in owners if owner is not case.session)
        assert all(item["cursor_state"] in ("closed", "connection_owned") for item in submissions)
        assert all(item["state"] == "succeeded" for item in submissions)
        assert all(item["connection_disconnected"] is True for item in submissions)
        restored = session.artifact(dataset.artifact.artifact_ref)
        from marivo.analysis.public_dsl import _MaterializedRead

        assert isinstance(restored, _MaterializedRead)
        (tmp_path / "statistic-producer.json").write_bytes(
            encode({"session": session.id, "original": snapshot(restored)})
        )
        evidence_dir = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence_dir:
            payload = {
                "id": f"R9:{family}.{method}@v1:{backend}:ordinary-table:ibis_python:complete-source-capture",
                "oracle": oracle,
                "environment": case.environment,
                "input": {"columns": data.columns, "values": data.values},
                "submissions": submissions,
                "actual_native_submissions": submitted,
                "parts": sorted(parts),
                "physical_key": key_json(selected.key),
                "implementation_id": selected.implementation.qualification.implementation_id,
                "remote_termination": "not_applicable"
                if backend in {"duckdb", "sqlite"}
                else "remote_unknown",
                "boundary": "Bounded entity/int64 source capture and views; not full scenario qualification",
            }
            raw = json.dumps(payload, sort_keys=True).encode()
            name = f"{family}-{method}-{backend}.json"
            Path(evidence_dir, name).write_bytes(raw)
            summary = {
                **payload,
                "submissions": [
                    {key: value for key, value in item.items() if key != "sql"}
                    for item in submissions
                ],
                "actual_native_submissions": [digest(sql.encode()) for sql in submitted],
                "raw_receipt": name,
                "raw_receipt_sha256": digest(raw),
            }
            Path(evidence_dir, "binding-" + name).write_text(json.dumps(summary, sort_keys=True))
    (tmp_path / "models").rename(tmp_path / "models.offline")
    phases: list[Json] = []
    for phase in ("fixed", "cold"):
        report = tmp_path / ("statistic-" + phase + ".json")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.producer_recovery_worker",
                str(tmp_path),
                phase,
                str(report),
            ],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        phases.append(read(report))
    assert len({os.getpid(), *(obj(item)["pid"] for item in phases)}) == 3
    if evidence_dir:
        Path(evidence_dir, f"recovery-{family}-{method}-{backend}.json").write_bytes(
            encode(
                {
                    "backend": backend,
                    "method": method,
                    "producer_pid": os.getpid(),
                    "phases": phases,
                }
            )
        )
