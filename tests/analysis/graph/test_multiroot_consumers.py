"""Public independent contribution-root regression; no scenario grants."""

import math
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal, NoReturn

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.datasource.source_cases import Case, SourceData, datasource, source_case, source_params
from tests.support.source_trace import SourceTrace


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


@pytest.mark.parametrize(
    "mode,backend",
    source_params(
        [
            (mode, backend)
            for backend in ("duckdb", "postgres", "mysql", "trino", "clickhouse")
            for mode in (
                "linear_fanout",
                "linear_float",
                "linear_decimal",
                "linear_nonfinite",
                "ratio_decimal",
                "ratio_decimal_overflow",
                "ratio_decimal_output_overflow",
                "spatial_mean",
            )
            if mode != "linear_nonfinite"
            if mode != "linear_fanout" or backend == "duckdb"
            if mode not in ("ratio_decimal_overflow", "ratio_decimal_output_overflow")
            or backend in ("mysql", "clickhouse")
            if not mode.startswith("spatial_") or backend == "duckdb"
            if backend == "duckdb"
            or (backend in ("mysql", "clickhouse") and mode == "ratio_decimal_overflow")
        ],
        backend_index=1,
    ),
)
def test_independent_roots_and_temporal_fold(
    backend: str,
    mode: Literal[
        "linear_fanout",
        "linear_float",
        "linear_decimal",
        "linear_nonfinite",
        "ratio_decimal",
        "ratio_decimal_overflow",
        "ratio_decimal_output_overflow",
        "spatial_mean",
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
        composite = mode == "linear_fanout"
        ratio = mode.startswith("ratio")
        runtime_mode = linear or ratio
        fold = mode.removeprefix("spatial_")
        spatial_fold = mode.startswith("spatial_")
        numeric_type: Literal["int64", "float64", "decimal"] = (
            "float64"
            if mode in ("linear_float", "linear_nonfinite")
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
                [(1, "a", 0), (2, "b", 0)] + ([(3, "c", 0)] if ratio else []),
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
                ((1, "a", 0, 1), (2, "a", 0, 2)),
                ((11, "a", 10, 1),),
                ((21, "a", 2, 1),),
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
                models += f"{name}_subject=ms.relationship(name={name + '_subject'!r},from_entity={name},to_entity=subjects,keys=[ms.join_on({name}_owner,subjects_owner)])\n"
        models += "frozen=ms.finalize()\n"
        for name in ("left", "right"):
            models += f"{name}_row=frozen.resolve({name}_amount)\n{name}_clock=frozen.resolve({name}_time)\n"
            models += f"@ms.metric()\ndef {name}_total():\n    return {name}_row.sum(time={name}_clock, empty='zero')\n"
            if not runtime_mode:
                models += f"@ms.metric()\ndef {name}_folded():\n    return {name}_total.per({name}_clock).{fold}()\n"
        files = {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse',{argtext})\n",
            "semantic/sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
            "semantic/sales/models.py": models,
        }
        semantic_project_factory(
            {name.removeprefix("semantic/"): content for name, content in files.items()}
        )
        session = mv.session.create("r93-multiroot", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.subjects"))
        metrics = session.catalog.metrics
        left_total, right_total = metrics.get("sales.left_total"), metrics.get("sales.right_total")
        expression = left_total / right_total if ratio else left_total + right_total
        if mode == "linear_fanout":
            before = set(root.rglob("*.parquet"))
            with pytest.raises(AnalysisError) as refused_route:
                session.observe({"value": expression}, by=members).persist()
            assert (
                refused_route.value.expected
                and refused_route.value.received
                and refused_route.value.repair
            )
            assert "left" in str(refused_route.value) or "right" in str(refused_route.value)
            assert set(root.rglob("*.parquet")) == before
            assert session.runs().items == ()
            assert not source_trace.native_sql
            return
        values = session.observe(
            {"value": expression if runtime_mode else metrics.get("sales.left_folded")},
            by=members
            if runtime_mode
            else mv.product(
                session.time_scope(start="2026-08-01", end="2026-08-03"),
                members,
            ),
        )
        if mode in {"linear_nonfinite", "ratio_decimal_overflow", "ratio_decimal_output_overflow"}:
            before = set(root.rglob("*.parquet"))
            with pytest.raises(AnalysisError) as refused:
                values.persist()
            assert refused.value.expected and refused.value.received and refused.value.repair
            if mode.startswith("ratio_decimal"):
                diagnostic = str(refused.value) + str(refused.value.__cause__)
                assert "overflow" in diagnostic.lower() or "precision" in diagnostic.lower()
            else:
                assert "finite" in (str(refused.value) + str(refused.value.__cause__)).lower()
            assert set(root.rglob("*.parquet")) == before
            assert session._runtime.store.resources(session.id) == ()
            return
        result = values.persist()
        exported = result.to_pandas()
        frame = exported.set_index(ordinary_columns(exported)[0])
        actual = frame[("value", "value")]
        assert frame.index.tolist() == (["a", "b", "c"] if ratio else ["a", "b"])
        expected: dict[str, int | float | Decimal] = {
            "linear_float": 4.5,
            "linear_decimal": Decimal("0.35"),
            "ratio_decimal": 6.0,
            "spatial_mean": 18,
        }
        if ratio:
            assert actual.loc["a"] == pytest.approx(expected[mode], rel=1e-14)
        else:
            assert actual.loc["a"] == expected[mode]
        if spatial_fold:
            assert pd.isna(actual.loc["b"])
            clock = session.catalog.fields.get("sales.left.time")
            amount = session.catalog.fields.get("sales.left.amount")
            samples = session.observe(
                {"value": left_total, "count": amount.count(time=clock)},
                by=mv.product(
                    session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day")),
                    members,
                ),
            ).persist()
            sample_rows = samples.to_pandas()
            groups = sample_rows.groupby(sample_rows.columns[1])
            assert groups.get_group("a")[("value", "value")].tolist() == [15, 21]
            assert groups.get_group("a")[("value", "count")].tolist() == [2, 2]
            assert groups.get_group("b")[("value", "value")].tolist() == [0, 0]
            source_trace.record(samples)
        else:
            assert actual.loc["b"] == (0.0 if ratio else amounts[4])
        if numeric_type == "decimal" and linear:
            assert isinstance(actual.loc["a"], Decimal) and isinstance(actual.loc["b"], Decimal)
        ratio_total = (Fraction(amounts[0]) + Fraction(amounts[1])) / sum(
            (Fraction(value) for value in amounts[2:]), Fraction()
        )
        if ratio:
            assert pd.isna(actual.loc["c"])
            components = session.observe(
                {"left": left_total, "right": right_total}, by=members
            ).persist()
            component_rows = components.to_pandas()
            component_rows = component_rows.set_index(ordinary_columns(component_rows)[0])
            numerators = component_rows[("value", "left")]
            denominators = component_rows[("value", "right")]
            assert Fraction(numerators.loc["a"]) == Fraction(amounts[0]) + Fraction(amounts[1])
            assert Fraction(denominators.loc["a"]) == Fraction(amounts[2]) + Fraction(amounts[3])
            assert denominators.loc["b"] == amounts[4]
            rolled = session.observe({"value": expression}, by=()).persist()
            assert rolled.to_pandas()[("value", "value")].tolist() == pytest.approx(
                [float(ratio_total)], rel=1e-14
            )
            assert rolled.to_pandas()[("value", "value")].iloc[0] != pytest.approx(
                float(expected[mode]) / 2
            )
            # The terminal component output is explicit data, sufficient for an
            # external calculation without restoring a hidden original graph.
            before_reads = len(source_trace.native_sql)
            with monkeypatch.context() as isolated:

                def forbidden(*args: object, **kwargs: object) -> NoReturn:
                    pytest.fail("terminal ratio calculation accessed its source")

                isolated.setattr(SourceSession, "execute", forbidden)
                saved = components.to_pandas()
                assert float(
                    saved[("value", "left")].sum() / saved[("value", "right")].sum()
                ) == pytest.approx(float(ratio_total), rel=1e-14)
                assert session.persisted_frame(result.artifact.id).to_pandas().equals(exported)
            assert len(source_trace.native_sql) == before_reads
            source_trace.record(components)
            source_trace.record(rolled)
        assert session._runtime.store.resources(session.id) == ()
        source_trace.save(
            f"c04-c06-source-{backend}-{mode}",
            {**subject.environment, "profile": profile},
            {"roots": [{"columns": data.columns, "values": data.values} for data in datasets]},
            {
                "a": str(expected[mode]),
                "b": None if spatial_fold else "0.0" if ratio else str(amounts[4]),
                "spatial_samples": [15, 21] if spatial_fold else None,
                "sample_counts": [2, 2] if spatial_fold else None,
                "ratio_total": str(ratio_total) if ratio else None,
            },
            result,
            (subject.session, left.session, right.session),
        )
