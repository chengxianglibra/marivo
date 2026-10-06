"""Public independent contribution-root regression; no scenario grants."""

import json
import math
import os
from collections.abc import Callable, Sequence
from contextlib import ExitStack, closing
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Literal, NoReturn

import ibis
import ibis.expr.datatypes as dt
import numpy as np
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.methods import builtin
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.physical import DecimalType, Qualified, ScalarType
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.methods.temporal_fold import decode_samples
from marivo.datasource.adapters import PhysicalRequirement, SourceSession, provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from marivo.semantic.runtime_metric import (
    RuntimeAggregateExpr,
    RuntimeLinearExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
)
from tests.datasource.source_cases import Case, SourceData, datasource, source_case
from tests.support.json import key_json
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("postgres", "mysql", "clickhouse"))
def test_wide_decimal_finish_keeps_extremes_and_half_even(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
) -> None:
    from marivo.analysis.compiler.numeric_sql import decimal_divide

    pairs = [
        (1, 2_000_000),
        (3, 2_000_000),
        (-5, 2_000_000),
        (7, 2_000_000),
        (10**38 - 1, 10**12),
        (10**38 - 1, 10**6),
        (10**38 - 1, 10**38 - 2),
    ]
    with localcontext() as context:
        context.prec = 120
        expected = {
            index: (Decimal(n) / Decimal(d)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
            for index, (n, d) in enumerate(pairs)
        }
    data = SourceData(
        "id BIGINT, n DECIMAL(38,0), d DECIMAL(38,0)",
        ",".join(f"({index},{n},{d})" for index, (n, d) in enumerate(pairs)),
        "id Int64, n Decimal(38,0), d Decimal(38,0)",
        [{"id": index, "n": Decimal(n), "d": Decimal(d)} for index, (n, d) in enumerate(pairs)],
    )
    with source_case(
        backend, "mergetree" if backend == "clickhouse" else "table", tmp_path, monkeypatch, data
    ) as case:
        binding = case.session.bind(case.source, source_identity="decimal-components")
        qualified = case.session.qualify(
            binding, PhysicalRequirement("metric.ratio", 1, frozenset({"scan", "project"}))
        )
        expression = decimal_divide(binding.relation, "n", "d", dt.Decimal(38, 6)).select(
            "id", "numeric_result"
        )
        read = case.session.compile(
            qualified,
            expression,
            purpose="c04-decimal-finish",
            expected_schema=expression.schema().to_pyarrow(),
        )
        actual: dict[int, Decimal] = {}
        with closing(case.session.batches(read, chunk_size=2)) as batches:
            for batch in batches:
                assert batch.schema.field("numeric_result").type == pa.decimal128(38, 6)
                for identity, value in zip(
                    batch.column("id").to_pylist(),
                    batch.column("numeric_result").to_pylist(),
                    strict=True,
                ):
                    assert isinstance(identity, int) and isinstance(value, Decimal)
                    actual[identity] = value
        assert actual == expected
    source_trace.save(
        f"c04-{backend}-decimal-finishing",
        case.environment,
        {"pairs": [[str(n), str(d)] for n, d in pairs]},
        {
            "values": {str(key): str(value) for key, value in expected.items()},
            "intermediate_exceeds_decimal38": True,
            "half_even": True,
            "output_type": "decimal128(38, 6)",
        },
        None,
        (),
    )


@pytest.mark.parametrize("method", ("metric.linear", "metric.ratio"))
def test_sqlite_float_does_not_grant_decimal(
    method: Literal["metric.linear", "metric.ratio"],
) -> None:
    from dataclasses import replace

    implementations = [
        item
        for item in builtin.implementations(MethodKey(method))
        if isinstance(item.qualification, Qualified)
        and item.qualification.implementation_id.startswith("r93.c04.sqlite.")
        and item.key.input_types == (ScalarType("float64"),) * 2
    ]
    assert len(implementations) == 1
    implementation = implementations[0]
    assert implementation.precision == "finite_float64"
    decimal_key = replace(implementation.key, input_types=(DecimalType(38, 6),) * 2)
    assert builtin.specialize_numeric(implementation, decimal_key).key != decimal_key


@pytest.mark.parametrize("backend", ("postgres", "mysql", "clickhouse"))
def test_wide_ratio_decimal_does_not_grant_other_scales(backend: str) -> None:
    from dataclasses import replace

    implementations = [
        item
        for item in builtin.implementations(MethodKey("metric.ratio"))
        if isinstance(item.qualification, Qualified)
        and item.qualification.implementation_id.startswith(f"r93.c04.{backend}.")
        and item.key.input_types == (DecimalType(38, 6),) * 2
    ]
    assert len(implementations) == 1
    implementation = implementations[0]
    assert implementation.precision == "exact"
    other_scale = replace(implementation.key, input_types=(DecimalType(38, 8),) * 2)
    assert builtin.specialize_numeric(implementation, other_scale).key != other_scale


def _data(
    backend: str,
    rows: Sequence[tuple[int, str, int | float | Decimal]],
    numeric_type: Literal["int64", "float64", "decimal"] = "int64",
) -> SourceData:
    instant = datetime(2026, 8, 1, tzinfo=timezone.utc)
    sql_type = {"int64": "BIGINT", "float64": "DOUBLE", "decimal": "DECIMAL(18,6)"}[numeric_type]
    if backend == "postgres" and numeric_type == "float64":
        sql_type = "DOUBLE PRECISION"
    ch_type = {"int64": "Int64", "float64": "Float64", "decimal": "Decimal(18,6)"}[numeric_type]
    return SourceData(
        f"id BIGINT, owner VARCHAR(10), amount {sql_type}, happened TIMESTAMP",
        ",".join(
            f"({identity},'{owner}',{'1e999' if isinstance(amount, float) and not math.isfinite(amount) else amount},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + f"'2026-08-0{2 if identity in (12, 22) else 1} 00:00:00')"
            for identity, owner, amount in rows
        ),
        f"id Int64, owner String, amount {ch_type}, happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "owner": owner,
                "amount": amount,
                "happened": instant.replace(day=2) if identity in (12, 22) else instant,
            }
            for identity, owner, amount in rows
        ],
    )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "mode,backend",
    [
        (mode, backend)
        for backend in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
        for mode in (
            "linear",
            "linear_composite",
            "linear_fanout",
            "linear_float",
            "linear_decimal",
            "linear_nonfinite",
            "ratio",
            "ratio_float",
            "ratio_decimal",
            "ratio_decimal_overflow",
            "ratio_decimal_output_overflow",
            "weighted_mean",
            "aggregate",
            "slice",
            "mean",
            "first",
            "last",
            "min",
            "max",
            "spatial_mean",
            "spatial_first",
            "spatial_last",
            "spatial_min",
            "spatial_max",
        )
        if mode != "linear_nonfinite" or backend == "sqlite"
        if mode != "linear_fanout" or backend == "duckdb"
        if mode not in ("ratio_decimal_overflow", "ratio_decimal_output_overflow")
        or backend in ("mysql", "clickhouse")
        if not mode.startswith("spatial_") or backend == "duckdb"
    ],
)
def test_independent_roots_and_temporal_fold(
    backend: str,
    mode: Literal[
        "linear",
        "linear_composite",
        "linear_fanout",
        "linear_float",
        "linear_decimal",
        "linear_nonfinite",
        "ratio",
        "ratio_float",
        "ratio_decimal",
        "ratio_decimal_overflow",
        "ratio_decimal_output_overflow",
        "weighted_mean",
        "aggregate",
        "slice",
        "mean",
        "first",
        "last",
        "min",
        "max",
        "spatial_mean",
        "spatial_first",
        "spatial_last",
        "spatial_min",
        "spatial_max",
    ],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    with ExitStack() as stack:
        root = tmp_path
        monkeypatch.chdir(root)
        monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(root))
        profile = (
            "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
        )
        linear = mode.startswith("linear")
        composite = mode in ("linear_composite", "linear_fanout")
        ratio = mode.startswith("ratio")
        runtime_mode = linear or ratio or mode in ("weighted_mean", "aggregate", "slice")
        fold = mode.removeprefix("spatial_")
        spatial_fold = mode.startswith("spatial_")
        numeric_type: Literal["int64", "float64", "decimal"] = (
            "float64"
            if mode in ("linear_float", "linear_nonfinite", "ratio_float")
            else "decimal"
            if mode
            in (
                "linear_decimal",
                "ratio_decimal",
                "ratio_decimal_overflow",
                "ratio_decimal_output_overflow",
            )
            else "int64"
        )
        amounts: tuple[int | float | Decimal, ...] = (
            (1.25, 2.5, 0.25, 0.5, 4.0)
            if numeric_type == "float64"
            else (Decimal("0.1"), Decimal("0.2"), Decimal("0.02"), Decimal("0.03"), Decimal("0.04"))
            if numeric_type == "decimal"
            else (10, 20, 2, 3, 4)
        )
        if mode == "linear_nonfinite":
            amounts = (float("inf"), 2.5, 0.25, 0.5, 4.0)
        elif mode == "ratio_decimal_overflow":
            amounts = (
                Decimal("6" + "0" * 31),
                Decimal("6" + "0" * 31),
                Decimal("2" + "0" * 31),
                Decimal("3" + "0" * 31),
                Decimal("0.04"),
            )
        elif mode == "ratio_decimal_output_overflow":
            amounts = (
                Decimal("1" + "0" * 31),
                Decimal("0"),
                Decimal("0.000001"),
                Decimal("0.000001"),
                Decimal("0.04"),
            )
        datasets = [
            _data(
                backend,
                [(1, "a", 0), (2, "b", 0)]
                + ([(3, "c", 0)] if ratio or mode == "weighted_mean" else []),
                numeric_type,
            ),
            _data(
                backend,
                [(index, "a", amounts[0]) for index in range(11, 17)]
                if mode == "ratio_decimal_overflow"
                else [(11, "a", 10), (14, "a", 5), (12, "a", 20), (22, "a", 1)]
                if spatial_fold
                else [(11, "a", amounts[0]), (12, "a", amounts[1])],
                numeric_type,
            ),
            _data(
                backend,
                [(21, "a", amounts[2]), (22, "a", amounts[3]), (23, "b", amounts[4])],
                numeric_type,
            ),
        ]
        if composite:
            composite_rows = (
                ((1, "a", 0, 1), (2, "a", 0, 2), (3, "b", 0, 1)),
                ((11, "a", 10, 1), (12, "a", 20, 1), (13, "a", 50, 2), (14, "a", 40, 2)),
                (
                    (21, "a", 2, 1),
                    (22, "a", 3, 1),
                    (24, "a", 7, 2),
                    (25, "a", 13, 2),
                    (23, "b", 4, 1),
                ),
            )
            datasets = []
            for composite_root in composite_rows:
                base = _data(
                    backend,
                    [(identity, owner, amount) for identity, owner, amount, _ in composite_root],
                )
                datasets.append(
                    replace(
                        base,
                        columns=base.columns + ", tenant BIGINT",
                        values=",".join(
                            _data(backend, [(identity, owner, amount)]).values[:-1] + f",{tenant})"
                            for identity, owner, amount, tenant in composite_root
                        ),
                        clickhouse_columns=base.clickhouse_columns + ", tenant Int64",
                        rows=[
                            {**record, "tenant": tenant}
                            for record, (_, _, _, tenant) in zip(
                                base.rows, composite_root, strict=True
                            )
                        ],
                    )
                )
        if mode in ("ratio_decimal_overflow", "ratio_decimal_output_overflow"):
            datasets = [
                replace(
                    data,
                    columns=data.columns.replace("DECIMAL(18,6)", "DECIMAL(38,6)"),
                    clickhouse_columns=data.clickhouse_columns.replace(
                        "Decimal(18,6)", "Decimal(38,6)"
                    ),
                )
                for data in datasets
            ]
        if backend == "duckdb":
            path = root / "source.duckdb"
            admin = ibis.duckdb.connect(path)
            for name, rows in zip(("subjects", "left_facts", "right_facts"), datasets, strict=True):
                admin.raw_sql(f"CREATE TABLE {name} ({rows.columns})")
                admin.raw_sql(f"INSERT INTO {name} VALUES {rows.values}")
            admin.disconnect()
            connection = stack.enter_context(
                provider_for("duckdb").open(
                    datasource("duckdb", {"path": str(path), "read_only": True})
                )
            )
            subject, left, right = [
                Case(connection, TableSourceIR(name), {"backend": "duckdb", "read_only": True})
                for name in ("subjects", "left_facts", "right_facts")
            ]
        else:
            subject, left, right = [
                stack.enter_context(source_case(backend, profile, root, monkeypatch, rows))
                for rows in datasets
            ]
        args = {
            **subject.session.datasource.fields,
            **{k + "_env": v for k, v in subject.session.datasource.env_refs.items()},
        }
        if "user" in args:
            monkeypatch.setenv("MARIVO_R93_READER", str(args.pop("user")))
            args["user_env"] = "MARIVO_R93_READER"
        argtext = ", ".join(f"{k}={v!r}" for k, v in args.items())
        models = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, case in (("subjects", subject), ("left", left), ("right", right)):
            assert isinstance(case.source, TableSourceIR)
            primary_key = (
                ["owner", "tenant"]
                if name == "subjects" and composite
                else ["owner" if name == "subjects" else "id"]
            )
            models += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key={primary_key!r})\n"
            models += (
                f"{name}_owner=ms.dimension_column(name='owner',entity={name},column='owner')\n"
            )
            if composite:
                models += f"{name}_tenant=ms.dimension_column(name='tenant',entity={name},column='tenant')\n"
            models += f"{name}_time=ms.time_dimension_column(name='time',entity={name},column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
            if name != "subjects":
                models += f"{name}_amount=ms.measure_column(name='amount',entity={name},column='amount',additivity=ms.additive_all())\n"
                models += f"{name}_total=ms.aggregate(name={name + '_total'!r},measure={name}_amount,agg='sum',empty=ms.empty.zero())\n"
                tenant_join = (
                    f",ms.join_on({name}_tenant,subjects_tenant)"
                    if mode == "linear_composite"
                    else ""
                )
                models += f"{name}_subject=ms.relationship(name={name + '_subject'!r},from_entity={name},to_entity=subjects,keys=[ms.join_on({name}_owner,subjects_owner){tenant_join}])\n"
                if not runtime_mode:
                    models += f"{name}_status=ms.measure_column(name='status',entity={name},column='amount',additivity=ms.additive_all(except_=({name}_time,)),status_time_dimension={name}_time,status_time_fold={fold!r})\n"
                    models += f"{name}_folded=ms.aggregate(name={name + '_folded'!r},measure={name}_status,agg='sum')\n"
        files = {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse',{argtext})\n",
            "semantic/sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
            "semantic/sales/models.py": models,
        }
        semantic_project_factory(
            {name.removeprefix("semantic/"): content for name, content in files.items()}
        )
        session = mv.session.get_or_create("r93-multiroot", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.subjects"))
        expression: (
            RuntimeLinearExpr
            | RuntimeRatioExpr
            | RuntimeWeightedMeanExpr
            | RuntimeAggregateExpr
            | RuntimeSliceExpr
        ) = mv.runtime_metric.linear(
            add=[ms.ref.metric("sales.left_total"), ms.ref.metric("sales.right_total")],
            label="combined",
        )
        if ratio:
            expression = mv.runtime_metric.ratio(
                ms.ref.metric("sales.left_total"), ms.ref.metric("sales.right_total"), label="ratio"
            )
        elif mode == "weighted_mean":
            expression = mv.runtime_metric.weighted_mean(
                ms.ref.measure("sales.left.amount"),
                ms.ref.measure("sales.left.amount"),
                label="weighted",
            )
        elif mode == "aggregate":
            expression = mv.runtime_metric.aggregate(
                ms.ref.measure("sales.left.amount"), agg="sum", label="total"
            )
        elif mode == "slice":
            expression = mv.runtime_metric.slice(
                ms.ref.metric("sales.right_total"),
                by={ms.ref.dimension("sales.right.owner"): "a"},
                label="selected",
            )
        routes = mv.routes(
            mv.route(
                ms.ref.entity("sales.left"), through=(ms.ref.relationship("sales.left_subject"),)
            ),
            mv.route(
                ms.ref.entity("sales.right"), through=(ms.ref.relationship("sales.right_subject"),)
            ),
        )
        if mode == "linear_fanout":
            from marivo.analysis.datasets.errors import DatasetConstructionError

            before = set(root.rglob("*.parquet"))
            with pytest.raises(DatasetConstructionError) as refused_route:
                members.observe(expression, via=routes)
            assert refused_route.value.received == "Metric roots or relationship endpoints differ"
            assert refused_route.value.location == "analysis.graph_observation"
            assert set(root.rglob("*.parquet")) == before
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            assert not source_trace.native_sql
            if evidence := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
                Path(evidence, "c04-refused-incomplete-relationship.json").write_text(
                    json.dumps(
                        {
                            "backend": backend,
                            "complete_target_key": ["owner", "tenant"],
                            "relationship_key": ["owner"],
                            "expected": refused_route.value.expected,
                            "received": refused_route.value.received,
                            "location": refused_route.value.location,
                            "actual_native_submissions": [],
                            "published_parquet": 0,
                            "resources": 0,
                            "boundary": "Shared construction refusal; no backend Runtime qualification grant",
                        },
                        sort_keys=True,
                    )
                )
            return
        values = (
            members.observe(expression, via=routes)
            if linear or ratio
            else members.observe(expression, via=ms.ref.relationship("sales.left_subject"))
            if mode in ("weighted_mean", "aggregate")
            else members.observe(expression, via=ms.ref.relationship("sales.right_subject"))
            if mode == "slice"
            else members.observe(
                ms.ref.metric("sales.left_folded"),
                during=mv.time_scope(start="2026-08-01", end="2026-08-03"),
                via=ms.ref.relationship("sales.left_subject"),
            )
        )
        assert isinstance(values, (mv.LogicalNumericRelation, mv.LogicalRatioRelation))
        if mode == "linear_nonfinite":
            from marivo.analysis.errors import AnalysisError

            before = set(root.rglob("*.parquet"))
            from marivo.datasource.errors import DatasourceSourceCapabilityError

            with pytest.raises(AnalysisError) as refused:
                values.execute()
            assert isinstance(refused.value.__cause__, DatasourceSourceCapabilityError)
            expected_source = refused.value.__cause__.expected
            assert expected_source is not None
            assert "exact double" in expected_source
            assert set(root.rglob("*.parquet")) == before
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            return
        if numeric_type == "decimal" and (
            backend == "sqlite"
            or (ratio and backend not in ("duckdb", "postgres", "mysql", "clickhouse"))
        ):
            before = set(root.rglob("*.parquet"))
            with pytest.raises(MethodRegistrationError) as refused:
                values.execute()
            assert refused.value.expected is not None
            assert refused.value.received is not None
            assert ("metric.ratio" if ratio else "metric.linear") in refused.value.expected
            assert "DecimalType(precision=38, scale=6)" in refused.value.received
            assert f"backend='{backend}'" in refused.value.received
            assert set(root.rglob("*.parquet")) == before
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            if ratio and (evidence := os.environ.get("MARIVO_R93_EVIDENCE_DIR")):
                Path(evidence, f"c04-refused-{backend}-{mode}.json").write_text(
                    json.dumps(
                        {
                            "backend": backend,
                            "mode": mode,
                            "expected": refused.value.expected,
                            "received": refused.value.received,
                            "repair": refused.value.repair.action if refused.value.repair else None,
                            "published_parquet": 0,
                            "resources": 0,
                            "boundary": "Exact-key admission refusal only; required Decimal execution remains unverified",
                        },
                        sort_keys=True,
                    )
                )
            return
        if mode in ("ratio_decimal_overflow", "ratio_decimal_output_overflow"):
            from marivo.analysis.errors import AnalysisError

            before = set(root.rglob("*.parquet"))
            with pytest.raises(AnalysisError) as refused:
                values.execute()
            from marivo.datasource.errors import DatasourceSourceCapabilityError

            if mode == "ratio_decimal_overflow":
                if backend == "clickhouse":
                    assert (
                        refused.value.expected == "Decimal sum within declared precision and scale"
                    ), str(refused.value.__cause__ or refused.value)
                    assert refused.value.location == "dataset.graph_check"
                else:
                    assert isinstance(refused.value.__cause__, DatasourceSourceCapabilityError)
                    assert (
                        refused.value.__cause__.expected
                        == "exact decimal128(38, 6) value for value"
                    )
            else:
                assert refused.value.expected == "valid four-state Cell encoding"
                assert refused.value.location == "dataset.graph_check"
            assert set(root.rglob("*.parquet")) == before
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            executions = [
                owner
                for owner in source_trace.owners
                if owner not in (subject.session, left.session, right.session)
            ]
            submissions = [asdict(item) for owner in executions for item in owner.submissions]
            assert executions and submissions
            assert all(owner._closed for owner in executions)
            assert all(item["connection_disconnected"] is True for item in submissions)
            assert all(item["sql"] in source_trace.native_sql for item in submissions)
            if evidence := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
                Path(evidence, f"c04-overflow-{backend}-{mode}.json").write_text(
                    json.dumps(
                        {
                            "backend": backend,
                            "mode": mode,
                            "expected": refused.value.expected,
                            "received": refused.value.received,
                            "cause": str(refused.value.__cause__),
                            "published_parquet": 0,
                            "resources": 0,
                            "connection_disconnected": True,
                            "actual_native_submissions": source_trace.native_sql,
                            "submissions": submissions,
                            "boundary": "Confirmed Decimal overflow refusal; no complete family grant",
                        },
                        sort_keys=True,
                    )
                )
            return
        result = values.execute()
        if mode == "linear_composite":
            assert result._dataset is not None
            verified = result._dataset.verified()
            actual = {
                (row["key_0"], row["key_1"]): (row["value"], row["cell_tag"])
                for row in verified.primary.to_pylist()
            }
            assert actual == {
                ("a", 1): (35, "defined"),
                ("a", 2): (110, "defined"),
                ("b", 1): (4, "defined"),
            }
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            source_trace.save(
                f"c04-source-{backend}-{mode}",
                {**subject.environment, "profile": profile},
                {"roots": [{"columns": data.columns, "values": data.values} for data in datasets]},
                {
                    "a_1": 35,
                    "a_2": 110,
                    "b_1": 4,
                    "complete_key": ["owner", "tenant"],
                    "roots_reduced_before_combining": True,
                },
                result,
                (subject.session, left.session, right.session),
            )
            return
        frame = result.to_pandas().set_index("member")
        assert set(frame.index) == (
            {"a", "b", "c"} if ratio or mode == "weighted_mean" else {"a", "b"}
        )
        expected: dict[str, int | float | Decimal] = {
            "linear": 35,
            "linear_float": 4.5,
            "linear_decimal": Decimal("0.35"),
            "ratio": 6.0,
            "ratio_float": 5.0,
            "ratio_decimal": Decimal("6"),
            "weighted_mean": 50 / 3,
            "aggregate": 30,
            "slice": 5,
            "mean": 15,
            "first": 10,
            "last": 20,
            "min": 10,
            "max": 20,
            "spatial_mean": 18,
            "spatial_first": 15,
            "spatial_last": 21,
            "spatial_min": 15,
            "spatial_max": 21,
        }
        if mode == "weighted_mean":
            assert frame.loc["a", "value"] == pytest.approx(expected[mode], rel=1e-14)
        else:
            assert frame.loc["a", "value"] == expected[mode]
        if spatial_fold:
            assert result._dataset is not None
            state_rows = next(
                part.table.to_pylist()
                for part in result._dataset.verified().parts
                if part.role == "original_state"
            )
            samples = {
                row["key_0"]: decode_samples(row["original_state__samples"]) for row in state_rows
            }
            assert samples == {
                "a": ((datetime(2026, 8, 1), 15, 2), (datetime(2026, 8, 2), 21, 2)),
                "b": (),
            }
        assert frame.loc["a", "cell_tag"] == "defined"
        if linear or ratio or mode == "slice":
            assert frame.loc["b", "value"] == (0.0 if ratio or mode == "slice" else amounts[4])
            if numeric_type == "decimal" and linear:
                assert isinstance(frame.loc["a", "value"], Decimal)
                assert isinstance(frame.loc["b", "value"], Decimal)
            assert frame.loc["b", "cell_tag"] == "defined"
        else:
            assert frame.loc["b", "cell_tag"] == "null"
            assert frame.loc["b", "cell_reason"] == "empty_contribution"
        if ratio or mode == "weighted_mean":
            assert set(frame.index) == {"a", "b", "c"}
            assert frame.loc["c", "cell_tag"] == ("undefined" if ratio else "null")
            assert frame.loc["c", "cell_reason"] == (
                "zero_denominator" if ratio else "empty_contribution"
            )
        ratio_total = (Fraction(amounts[0]) + Fraction(amounts[1])) / sum(
            (Fraction(value) for value in amounts[2:]), Fraction()
        )
        if ratio:
            assert result._dataset is not None
            original = (
                next(
                    part.table
                    for part in result._dataset.verified().parts
                    if part.role == "original_state"
                )
                .to_pandas()
                .set_index("key_0")
            )
            numerator: object = original.loc["a", "original_state__numerator_sum"]
            denominator: object = original.loc["a", "original_state__denominator_sum"]
            if isinstance(numerator, np.integer):
                numerator = int(numerator)
            if isinstance(denominator, np.integer):
                denominator = int(denominator)
            assert isinstance(numerator, (int, float, Decimal))
            assert isinstance(denominator, (int, float, Decimal))
            assert Fraction(numerator) == Fraction(amounts[0]) + Fraction(amounts[1])
            assert Fraction(denominator) == Fraction(amounts[2]) + Fraction(amounts[3])
            assert original.loc["b", "original_state__denominator_sum"] == amounts[4]
            if mode == "ratio_decimal":
                assert isinstance(numerator, Decimal)
                assert isinstance(denominator, Decimal)
                ratio_value: object = frame.loc["a", "value"]
                assert isinstance(ratio_value, Decimal)

            def forbid_source(*args: object, **kwargs: object) -> NoReturn:
                pytest.fail("Fixed ratio rollup must use retained original components")

            submissions_before = len(source_trace.native_sql)
            with monkeypatch.context() as isolated:
                isolated.setattr(SourceSession, "batches", forbid_source)
                rolled = result.rollup().execute()
                rolled_frame = rolled.to_pandas()
            assert len(rolled_frame) == 1
            assert rolled_frame.loc[0, "cell_tag"] == "defined"
            rolled_value: object = rolled_frame.loc[0, "value"]
            assert isinstance(rolled_value, (int, float, Decimal))
            if mode == "ratio_decimal":
                assert rolled_value == Decimal("3.333333")
                assert isinstance(rolled_value, Decimal)
            else:
                assert rolled_value == pytest.approx(float(ratio_total), rel=1e-14)
            assert float(rolled_value) != pytest.approx(float(expected[mode]) / 2)
            assert len(source_trace.native_sql) == submissions_before
            assert rolled._dataset is not None
            rolled._dataset.verified()
        if runtime_mode:
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            source_trace.save(
                f"c04-source-{backend}-{mode}",
                {**subject.environment, "profile": profile},
                {"roots": [{"columns": data.columns, "values": data.values} for data in datasets]},
                {
                    "a": str(expected[mode]),
                    "b": "0.0"
                    if ratio or mode == "slice"
                    else "Null(empty_contribution)"
                    if mode in ("weighted_mean", "aggregate")
                    else str(amounts[4]),
                    "numeric_type": numeric_type,
                    "empty_target": "Undefined(zero_denominator)"
                    if ratio
                    else "Null(empty_contribution)"
                    if mode == "weighted_mean"
                    else None,
                    "roots_reduced_before_combining": linear or ratio,
                    "resources": 0,
                    "fixed_ratio_rollup": str(ratio_total) if ratio else None,
                    "mean_of_target_ratios_rejected": ratio,
                    "fixed_source_reads_forbidden": ratio,
                    "exact_original_components": ratio,
                },
                result,
                (subject.session, left.session, right.session),
            )
        if not runtime_mode:
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            source_trace.save(
                f"c06-source-{backend}-{mode}",
                {**subject.environment, "profile": profile},
                {"roots": [{"columns": data.columns, "values": data.values} for data in datasets]},
                {
                    "fold": fold,
                    **(
                        {"spatial_samples": [15, 21], "sample_counts": [2, 2], "raw_row_mean": 9}
                        if spatial_fold
                        else {}
                    ),
                    "a": str(expected[mode]),
                    "b": "Null(empty_contribution)",
                    "resources": 0,
                },
                result,
                (subject.session, left.session, right.session),
            )
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert result._dataset is not None
            Path(evidence, f"supporting-c04-c06-{backend}-{mode}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "source_profile": profile,
                        "environment": subject.environment,
                        "mode": mode,
                        "oracle": {
                            "a": str(expected[mode]),
                            "b": "0.0"
                            if ratio or mode == "slice"
                            else str(amounts[4])
                            if linear
                            else "Null(empty_contribution)",
                        },
                        "roots_reduced_before_combining": linear or ratio,
                        "physical_keys": [
                            key_json(item.key)
                            for item in descriptor_plan(
                                result._dataset.artifact.descriptor, result._node.definition
                            ).physical_requirements
                        ],
                        "boundary": "Bounded independent-root or temporal-fold support; no complete C04/C06 qualification",
                    },
                    sort_keys=True,
                )
            )
