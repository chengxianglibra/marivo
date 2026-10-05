"""Public C-family source counterexamples; invocations alone grant no qualification."""

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NoReturn
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import DomainKind
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.methods import builtin
from marivo.analysis.methods.physical import (
    DecimalType,
    FixedShape,
    QualificationKey,
    Qualified,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey
from marivo.datasource.adapters import SourceSession
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.semantic.reader import SemanticProject
from scripts.r82_deviation_requirements import key_json
from tests.r9_source_cases import Case, SourceData, source_case
from tests.r93_source_trace import SourceTrace


@pytest.mark.parametrize("method", ["group.attach", "row.count", "row.mean", "state_rollup.mean"])
def test_c05_sqlite_does_not_specialize_to_unobserved_decimal(
    method: Literal["group.attach", "row.count", "row.mean", "state_rollup.mean"],
) -> None:
    types = (
        (DecimalType(18, 6), ScalarType("string"))
        if method == "group.attach"
        else (DecimalType(18, 6),)
    )
    domains: tuple[DomainKind, ...] = (
        ("entity", "entity")
        if method == "group.attach"
        else ("group",)
        if method in ("row.mean", "state_rollup.mean")
        else ("entity",)
    )
    key = QualificationKey(
        MethodKey(method),
        types,
        domains,
        SourceShape("sqlite", "table", "native", TimeShape("instant", "us", "UTC")),
        "ibis",
    )
    assert all(
        builtin.specialize_numeric(candidate, key).key != key
        for candidate in builtin.implementations(key.method)
    )


def _author_c05_project(
    backend: str,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    identity_type: Literal["int64", "string"] = "string",
    time_parse: Literal["native", "native_resolved", "string", "date"] = "native",
    cumulative_anchor: str | None = None,
    certified_calendar: bool = False,
    read_timezone: str = "UTC",
) -> bool:
    assert isinstance(case.source, TableSourceIR)
    remote = backend not in ("duckdb", "sqlite")
    if remote:

        def forbid_staging_write(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Remote C05 must not create or drop backend tables")

        monkeypatch.setattr(type(case.session._backend), "create_table", forbid_staging_write)
        monkeypatch.setattr(type(case.session._backend), "drop_table", forbid_staging_write)
    arguments = {
        **case.session.datasource.fields,
        **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
    }
    if "user" in arguments:
        monkeypatch.setenv("MARIVO_R93_READER", str(arguments.pop("user")))
        arguments["user_env"] = "MARIVO_R93_READER"
    argument_text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    primary_key = (
        ["tenant", "id", "revision"] if identity_type == "string" else ["id", "revision", "tenant"]
    )
    parse = (
        "None"
        if time_parse in {"date", "native_resolved"}
        else f"ms.timestamp(timezone={read_timezone!r})"
        if time_parse == "native"
        else "ms.strptime('%Y-%m-%d %H:%M:%S', timezone='UTC')"
    )
    granularity = "day" if time_parse == "date" else "second"
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse', {argument_text})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key={primary_key!r})\n"
            + "bucket = ms.dimension_column(name='bucket', entity=facts, column='tenant')\n"
            + "revision = ms.dimension_column(name='revision', entity=facts, column='revision')\n"
            + "amount = ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
            + f"happened = ms.time_dimension_column(name='happened', entity=facts, column='happened', granularity={granularity!r}, parse={parse}, is_default=True)\n"
            + "total = ms.aggregate(name='total', measure=amount, agg='sum', empty=ms.empty.zero())\n"
            + "average = ms.aggregate(name='average', measure=amount, agg='mean')\n"
            + (
                "import marivo.analysis as mv\n"
                "running = ms.cumulative(name='running', base=total, over=happened, "
                f"anchor={cumulative_anchor})\n"
                if cumulative_anchor is not None
                else ""
            )
            + (
                "from datetime import date\n"
                "calendar_day = ms.time_dimension_column(name='calendar_day', entity=facts, column='calendar_day', granularity='day')\n"
                "period = ms.dimension_column(name='period', entity=facts, column='period')\n"
                "fiscal = ms.period_calendar(name='fiscal', date=calendar_day, boundary_timezone='Asia/Shanghai', coverage=(date(2026, 8, 1), date(2026, 8, 6)), levels={'period': period})\n"
                if certified_calendar
                else ""
            ),
        }
    )
    return remote


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
@pytest.mark.parametrize("time_case", ["utc", "dst"])
def test_c06_declared_string_time_uses_owned_execution_functions(
    backend: str,
    time_case: Literal["utc", "dst"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (9007199254740992, 1, "a", 2, "2026-08-01 00:00:00"),
        (9007199254740993, 2, "a", None, "2026-08-01 00:00:00"),
        (9007199254740994, 1, "a", 99, "2026-08-02 00:00:00"),
        (9007199254740995, 1, "a", 77, "2026-07-31 23:59:59"),
    ]
    zone = ZoneInfo("America/New_York") if time_case == "dst" else ZoneInfo("UTC")
    start = (
        datetime(2026, 11, 1, tzinfo=zone)
        if time_case == "dst"
        else datetime(2026, 8, 1, tzinfo=zone)
    )
    end = (
        datetime(2026, 11, 2, tzinfo=zone)
        if time_case == "dst"
        else datetime(2026, 8, 2, tzinfo=zone)
    )
    if time_case == "dst":
        facts = [
            (9007199254740992, 1, "a", 2, "2026-11-01 04:00:00"),
            (9007199254740993, 2, "a", None, "2026-11-01 04:00:00"),
            (9007199254740994, 1, "a", 99, "2026-11-02 05:00:00"),
            (9007199254740995, 1, "a", 77, "2026-11-01 03:59:59"),
            (9007199254740996, 1, "a", 5, "2026-11-02 04:30:00"),
        ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        ",".join(
            f"({identity},{revision},'{tenant}',{amount if amount is not None else 'NULL'},'{instant}')"
            for identity, revision, tenant, amount, instant in facts
        ),
        "id Int64, revision Int64, tenant String, amount Nullable(Int64), happened String",
        [
            {
                "id": identity,
                "revision": revision,
                "tenant": tenant,
                "amount": amount,
                "happened": instant,
            }
            for identity, revision, tenant, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _author_c05_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="string"
        )
        session = mv.session.get_or_create("r93-string-time", report_timezone=str(zone))
        values = session.members(ms.ref.entity("sales.facts")).observe(
            ms.ref.metric("sales.total"), during=mv.time_scope(start=start, end=end)
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        result = values.execute()
        frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
        expected_values = {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 0,
            ("a", 9007199254740994, 1): 0,
            ("a", 9007199254740995, 1): 0,
        }
        if time_case == "dst":
            expected_values[("a", 9007199254740996, 1)] = 5
        assert frame.value.to_dict() == expected_values
        assert set(frame.cell_tag) == {"defined"}
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c06-source-{backend}-{'dst' if time_case == 'dst' else 'string-time'}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [2, 0, 0, 0, 5] if time_case == "dst" else [2, 0, 0, 0],
                "report_timezone": str(zone),
                "window_seconds": int(
                    (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
                ),
                "last_hour_included": time_case == "dst",
                "declared_format": "%Y-%m-%d %H:%M:%S",
                "start_inclusive": True,
                "end_exclusive": True,
                "before_start_excluded": True,
                "resources": 0,
            },
            result,
            (case.session,),
        )
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert result._dataset is not None
            Path(evidence, f"supporting-c06-{time_case}-string-time-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "source_profile": profile,
                        "environment": case.environment,
                        "declared_format": "%Y-%m-%d %H:%M:%S",
                        "oracle": [2, 0, 0, 0, 5] if time_case == "dst" else [2, 0, 0, 0],
                        "physical_keys": [
                            key_json(item.key)
                            for item in descriptor_plan(
                                result._dataset.artifact.descriptor, result._node.definition
                            ).physical_requirements
                        ],
                        "boundary": "Bounded declared-string-time support; no complete C06 qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize("numeric_type", ["int64", "float64"])
@pytest.mark.parametrize(
    "backend",
    ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"],
)
def test_c05_source_coordinates_statistics_and_original_state(
    backend: str,
    numeric_type: Literal["int64", "float64"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    instants = datetime(2026, 8, 1, tzinfo=timezone.utc)
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount "
        + ("BIGINT" if numeric_type == "int64" else "DOUBLE PRECISION")
        + ", happened TIMESTAMP",
        ",".join(
            f"({identity},{revision},'{tenant}',{amount},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + "'2026-08-01 00:00:00')"
            for identity, revision, tenant, amount in (
                (9007199254740992, 1, "a", "2"),
                (9007199254740993, 2, "a", "NULL"),
                (9007199254740993, 1, "b", "4" if numeric_type == "int64" else "4.5"),
            )
        ),
        "id Int64, revision Int64, tenant String, amount Nullable("
        + ("Int64" if numeric_type == "int64" else "Float64")
        + "), happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "revision": revision,
                "tenant": tenant,
                "amount": float(amount)
                if numeric_type == "float64" and amount is not None
                else amount,
                "happened": instants,
            }
            for identity, revision, tenant, amount in (
                (9007199254740992, 1, "a", 2),
                (9007199254740993, 2, "a", None),
                (9007199254740993, 1, "b", 4 if numeric_type == "int64" else 4.5),
            )
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        remote = _author_c05_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r93-c05", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        bucket = members.read(ms.ref.dimension("sales.facts.bucket"))
        assert isinstance(bucket, mv.LogicalCategoryRelation)
        selected = bucket.where(bucket.value.eq("a")).members()
        assert isinstance(selected, mv.LogicalAnalysisDomain)
        categories = selected.read(ms.ref.dimension("sales.facts.bucket"))
        assert isinstance(categories, mv.LogicalCategoryRelation)
        values = selected.observe(ms.ref.metric("sales.total"))
        assert isinstance(values, mv.LogicalNumericRelation)
        grouped = values.group_by(categories, groups=bucket.group_by())
        counted = grouped.summarize(mv.count()).execute()
        assert counted._dataset is not None
        assert any(
            item.key.method == MethodKey("group.attach")
            and item.key.input_types == (ScalarType(numeric_type), ScalarType("string"))
            for item in descriptor_plan(
                counted._dataset.artifact.descriptor, counted._node.definition
            ).physical_requirements
        )
        counts = counted.to_pandas().set_index("group")
        assert counts.value.to_dict() == {"a": 2, "b": 0}
        if numeric_type == "int64":
            revision = members.read(ms.ref.dimension("sales.facts.revision"))
            selected_revision = selected.read(ms.ref.dimension("sales.facts.revision"))
            assert isinstance(revision, mv.LogicalCategoryRelation)
            assert isinstance(selected_revision, mv.LogicalCategoryRelation)
            tuple_result = (
                values.group_by(
                    categories, selected_revision, groups=members.group_by(bucket, revision)
                )
                .summarize(mv.count())
                .execute()
            )
            tuples = tuple_result.to_pandas()
            assert tuples.set_index(["group", "coord_0"]).value.to_dict() == {
                ("a", 1): 1,
                ("a", 2): 1,
                ("b", 1): 0,
            }
            r93_source_trace.record(tuple_result)
        summed = grouped.summarize(mv.sum()).execute()
        assert summed.to_pandas().set_index("group").value.to_dict() == {"a": 2, "b": 0}
        defined = grouped.summarize(mv.count_defined()).execute()
        assert defined.to_pandas().set_index("group").value.to_dict() == {"a": 2, "b": 0}
        mean = grouped.summarize(mv.mean()).execute()
        means = mean.to_pandas().set_index("group")
        assert means.loc["a", "value"] == 1.0
        assert means.loc["b", "cell_reason"] == "empty_mean"
        assert mean.rollup().execute().to_pandas().value.tolist() == [1.0]
        original = values.group_by(categories).rollup().execute()
        assert original.to_pandas().value.tolist() == [2]
        assert original.rollup().execute().to_pandas().value.tolist() == [2]
        full = members.observe(ms.ref.metric("sales.total"))
        assert isinstance(full, mv.LogicalNumericRelation)
        if numeric_type == "int64":
            current = members.observe(
                ms.ref.metric("sales.total"),
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            )
            baseline = members.observe(
                ms.ref.metric("sales.total"),
                during=mv.time_scope(start="2026-08-02", end="2026-08-03"),
            )
            assert isinstance(current, mv.LogicalNumericRelation)
            assert isinstance(baseline, mv.LogicalNumericRelation)
            differences = current.compare(baseline).execute().to_pandas()
            assert differences.set_index(["member", "coord_0", "coord_1"]).value.to_dict() == {
                ("a", 9007199254740992, 1): 2,
                ("a", 9007199254740993, 2): 0,
                ("b", 9007199254740993, 1): 4,
            }
        full_sum = 6 if numeric_type == "int64" else 6.5
        assert full.group_by(bucket).rollup().rollup().execute().to_pandas().value.tolist() == [
            full_sum
        ]
        assert full.summarize(mv.mean()).execute().to_pandas().value.tolist() == pytest.approx(
            [full_sum / 3], abs=1e-12, rel=0
        )
        average = members.observe(ms.ref.metric("sales.average"))
        assert isinstance(average, mv.LogicalNumericRelation)
        assert average.summarize(mv.count()).execute().to_pandas().value.tolist() == [3]
        assert average.summarize(mv.count_defined()).execute().to_pandas().value.tolist() == [2]
        for reducer in (mv.sum(), mv.mean()):
            before = set(tmp_path.rglob("*.parquet"))
            with pytest.raises(MaterializationError) as refused:
                average.summarize(reducer).execute()
            assert refused.value.stage == "graph_check"
            assert refused.value.expected == "finite_numeric"
            assert refused.value.received == "1 violating rows"
            assert set(tmp_path.rglob("*.parquet")) == before
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
        empty_members = bucket.where(bucket.value.eq("missing")).members()
        assert isinstance(empty_members, mv.LogicalAnalysisDomain)
        empty_values = empty_members.observe(ms.ref.metric("sales.total"))
        assert isinstance(empty_values, mv.LogicalNumericRelation)
        assert empty_values.summarize(mv.count()).execute().to_pandas().value.tolist() == [0]
        empty_mean = empty_values.summarize(mv.mean()).execute().to_pandas()
        assert empty_mean.cell_reason.tolist() == ["empty_mean"]
        for result in (counted, summed, defined, mean, original):
            r93_source_trace.record(result)
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c05-source-{backend}-{numeric_type}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "counts": {"a": 2, "b": 0},
                "mean_a": 1.0,
                "mean_b_reason": "empty_mean",
                "original_a": 2,
                "original_full": full_sum,
                "current_full_mean": full_sum / 3,
                "complete_tuple_union": numeric_type == "int64",
                "null_arithmetic_rejected_atomically": True,
                "count_including_null": 3,
                "count_defined": 2,
                "empty_count": 0,
                "empty_mean_reason": "empty_mean",
                "numeric_type": numeric_type,
                "resources": 0,
            },
            None,
            (case.session,),
        )
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert (
                counted._dataset is not None
                and mean._dataset is not None
                and original._dataset is not None
            )
            Path(evidence, f"supporting-c05-{backend}-{numeric_type}.json").write_text(
                json.dumps(
                    {
                        "family": "C05.a/b/c",
                        "backend": backend,
                        "numeric_type": numeric_type,
                        "non_defined_arithmetic_rejected": True,
                        "environment": case.environment,
                        "source_profile": profile,
                        "remote_table_writes_forbidden": remote,
                        "oracle": {
                            "counts": {"a": 2, "b": 0},
                            "mean_a": 1.0,
                            "mean_b_reason": "empty_mean",
                            "original_a": 2,
                            "original_full": full_sum,
                            "current_full_mean": full_sum / 3,
                        },
                        "physical_keys": [
                            key_json(item.key)
                            for result in (counted, summed, defined, mean, original)
                            if result._dataset is not None
                            for item in descriptor_plan(
                                result._dataset.artifact.descriptor, result._node.definition
                            ).physical_requirements
                        ],
                        "boundary": "Bounded numeric/string source-only positive support; not complete C05 numeric/domain/data-check/cancellation qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize("identity_type", ["int64", "string"])
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
def test_c05_original_mean_preserves_component_weights(
    backend: str,
    identity_type: Literal["int64", "string"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [(i, "a", 1) for i in range(100)] + [(100, "b", 100)]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join(
            f"({9007199254740992 + i},1,'{tenant}',{amount},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + "'2026-08-01 00:00:00')"
            for i, tenant, amount in facts
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": 9007199254740992 + i,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for i, tenant, amount in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        remote = _author_c05_project(
            backend, case, monkeypatch, semantic_project_factory, identity_type
        )
        session = mv.session.get_or_create("r93-c05-mean", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        bucket = members.read(ms.ref.dimension("sales.facts.bucket"))
        values = members.observe(ms.ref.metric("sales.average"))
        assert isinstance(bucket, mv.LogicalCategoryRelation)
        assert isinstance(values, mv.LogicalNumericRelation)
        grouped = values.group_by(bucket).rollup()
        current = grouped.group_by().summarize(mv.mean()).execute()
        original = grouped.rollup().execute()
        assert current.to_pandas().value.tolist() == [50.5]
        assert original.to_pandas().value.tolist() == pytest.approx([200 / 101])
        fixed = grouped.execute()
        assert fixed.to_pandas().set_index("group").value.to_dict() == {"a": 1.0, "b": 100.0}
        assert fixed._dataset is not None
        state_rows = next(
            part.table.to_pylist()
            for part in fixed._dataset.verified().parts
            if part.role == "original_state"
        )
        assert {
            row["key_0"]: (row["original_state__sum"], row["original_state__non_null_count"])
            for row in state_rows
        } == {"a": (100, 100), "b": (100, 1)}

        def forbid_source(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed mean continuation must not read the source")

        submissions_before = len(r93_source_trace.native_sql)
        with monkeypatch.context() as isolated:
            isolated.setattr(type(case.session), "batches", forbid_source)
            assert fixed.group_by().summarize(mv.mean()).execute().to_pandas().value.tolist() == [
                50.5
            ]
            assert fixed.rollup().execute().to_pandas().value.tolist() == pytest.approx([200 / 101])
        assert len(r93_source_trace.native_sql) == submissions_before
        for result in (current, original):
            r93_source_trace.record(result)
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c05-mean-{backend}-{identity_type}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "current": 50.5,
                "original": 200 / 101,
                "rows": 101,
                "original_components": {"a": [100, 100], "b": [100, 1]},
                "fixed_source_reads_forbidden": True,
                "no_additional_native_submission": True,
                "identity_type": identity_type,
                "resources": 0,
            },
            fixed,
            (case.session,),
        )
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert fixed._dataset is not None
            Path(evidence, f"supporting-c05-mean-{backend}-{identity_type}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "identity_type": identity_type,
                        "source_profile": profile,
                        "environment": case.environment,
                        "remote_table_writes_forbidden": remote,
                        "fixed_source_reads_forbidden": True,
                        "oracle": {"current": 50.5, "original": 200 / 101, "rows": 101},
                        "original_components": {"a": [100, 100], "b": [100, 1]},
                        "physical_keys": [
                            key_json(item.key)
                            for result in (current, original, fixed)
                            if result._dataset is not None
                            for item in descriptor_plan(
                                result._dataset.artifact.descriptor, result._node.definition
                            ).physical_requirements
                        ],
                        "boundary": "Original/current mean supporting evidence; no complete C05 qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "clickhouse", "trino"]
)
@pytest.mark.parametrize(
    ("anchor", "expected", "overlap"),
    [
        ("None", [10, 28], True),
        ("ms.grain_to_date(grain=mv.grain('month'))", [5, 18], False),
        ("ms.trailing(count=31, unit='day')", [5, 21], True),
    ],
    ids=["all-history", "month-reset", "trailing-overlap"],
)
def test_c06_cumulative_keeps_anchor_and_overlap(
    backend: str,
    anchor: str,
    expected: list[int],
    overlap: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (1, 5, "2026-07-31 00:00:00"),
        (2, 2, "2026-08-01 00:00:00"),
        (3, 3, "2026-08-31 00:00:00"),
        (4, 7, "2026-09-01 00:00:00"),
        (5, 11, "2026-09-30 00:00:00"),
        (6, 99, "2026-10-01 00:00:00"),
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        ",".join(f"({identity},1,'a',{amount},'{instant}')" for identity, amount, instant in facts),
        "id Int64, revision Int64, tenant String, amount Int64, happened String",
        [
            {"id": identity, "revision": 1, "tenant": "a", "amount": amount, "happened": instant}
            for identity, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _author_c05_project(
            backend,
            case,
            monkeypatch,
            semantic_project_factory,
            time_parse="string",
            cumulative_anchor=anchor,
        )
        session = mv.session.get_or_create("r93-cumulative", report_timezone="UTC")
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-08-15", end="2026-10-01"),
            grain=mv.grain("month"),
        )
        logical = (
            session.members(ms.ref.entity("sales.facts"))
            .each(grid)
            .observe(
                ms.ref.metric("sales.running"),
                at=grid.end,
            )
        )
        fixed = logical.execute()
        r93_source_trace.record(fixed)
        grouped = fixed.group_by(grid).rollup().execute()
        assert grouped.to_pandas().value.tolist() == expected
        if overlap:
            with pytest.raises(AnalysisError, match="overlap"):
                grouped.rollup()
        else:
            assert grouped.rollup().execute().to_pandas().value.tolist() == [23]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        name = (
            "all-history" if anchor == "None" else "trailing-overlap" if overlap else "month-reset"
        )
        r93_source_trace.save(
            f"c06-source-{backend}-{name}",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": expected,
                "anchor": anchor,
                "overlap": overlap,
                "display_start_does_not_clip_anchor": True,
                "end_exclusive": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
@pytest.mark.parametrize("transition", ["spring", "fall"])
def test_c06_dst_grid_keeps_instant_edges_and_fixed_coordinate(
    backend: str,
    transition: Literal["spring", "fall"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    from marivo.analysis.core.time_grid import bind_grid

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    start, end = (
        ("2026-03-08", "2026-03-10") if transition == "spring" else ("2026-11-01", "2026-11-03")
    )
    instants = (
        ["2026-03-08 05:00:00", "2026-03-09 03:30:00", "2026-03-09 04:00:00", "2026-03-10 04:00:00"]
        if transition == "spring"
        else [
            "2026-11-01 04:00:00",
            "2026-11-02 04:30:00",
            "2026-11-02 05:00:00",
            "2026-11-03 05:00:00",
        ]
    )
    facts = list(zip([1, 2, 3, 4], [2, 5, 11, 99], instants, strict=True))
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        ",".join(f"({identity},1,'a',{amount},'{instant}')" for identity, amount, instant in facts),
        "id Int64, revision Int64, tenant String, amount Int64, happened String",
        [
            {"id": identity, "revision": 1, "tenant": "a", "amount": amount, "happened": instant}
            for identity, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _author_c05_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="string"
        )
        session = mv.session.get_or_create("r93-dst-grid", report_timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = mv.time_scope(
            start=datetime.fromisoformat(start).replace(tzinfo=zone),
            end=datetime.fromisoformat(end).replace(tzinfo=zone),
        )
        grain = mv.grain("day")
        grid = mv.time_grid(during=scope, grain=grain, timezone="America/New_York")
        bound = bind_grid(
            scope, grain, report_timezone="Asia/Tokyo", explicit_timezone="America/New_York"
        )
        hours = [23, 24] if transition == "spring" else [25, 24]
        assert [(c.end - c.start).total_seconds() / 3600 for c in bound.cells] == hours
        assert [c.start.strftime("%Y-%m-%d %H:%M:%S") for c in bound.cells] == [
            instants[0],
            instants[2],
        ]
        assert bound.cells[-1].end.strftime("%Y-%m-%d %H:%M:%S") == instants[3]
        fixed = (
            session.members(ms.ref.entity("sales.facts"))
            .each(grid)
            .observe(
                ms.ref.metric("sales.total"),
                during=grid.window,
            )
            .execute()
        )
        frame = fixed.to_pandas()
        assert len(frame) == 8
        assert set(frame.cell_tag) == {"defined"}
        r93_source_trace.record(fixed)
        grouped = fixed.group_by(grid).rollup().execute()
        assert grouped.to_pandas().value.tolist() == [7, 11]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c06-source-{backend}-{transition}-grid",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [7, 11],
                "cell_hours": hours,
                "product_rows": 8,
                "source_timezone": "UTC",
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": "America/New_York",
                "end_exclusive": True,
                "last_half_hour_included": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.parametrize(
    "method", ["parts_transport", "time.product", "metric.sum_zero", "state_rollup.sum_zero"]
)
def test_c06_tokyo_grid_registration_is_limited_to_recorded_keys(
    method: Literal["parts_transport", "time.product", "metric.sum_zero", "state_rollup.sum_zero"],
) -> None:
    declarations = [
        item
        for item in builtin.implementations(MethodKey(method))
        if isinstance(item.qualification, Qualified)
        and item.qualification.implementation_id.startswith("r93.c06.dst_grid.")
    ]
    fixed = method == "state_rollup.sum_zero"
    expected = QualificationKey(
        MethodKey(method),
        (ScalarType("int64" if fixed else "string"),),
        ("entity",),
        FixedShape(TimeShape("instant", "us", "Asia/Tokyo"))
        if fixed
        else SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "Asia/Tokyo")),
        "artifact_python" if fixed else "ibis",
    )
    assert [item.key for item in declarations] == [expected]


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
def test_c06_certified_unequal_calendar_owns_native_boundaries(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    from datetime import date, timedelta

    import marivo.datasource as md

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (
            i + 1,
            amount,
            date(2026, 8, 1) + timedelta(days=i),
            (datetime(2026, 7, 31, 16) + timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"),
            "P1" if i < 2 else "P2",
        )
        for i, amount in enumerate([2, 5, 11, 13, 17])
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32), calendar_day DATE, period VARCHAR(10)",
        ",".join(
            f"({identity},1,'a',{amount},'{instant}',{'DATE ' if backend == 'trino' else ''}'{day}','{period}')"
            for identity, amount, day, instant, period in facts
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened String, calendar_day Date, period String",
        [
            {
                "id": identity,
                "revision": 1,
                "tenant": "a",
                "amount": amount,
                "happened": instant,
                "calendar_day": day,
                "period": period,
            }
            for identity, amount, day, instant, period in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _author_c05_project(
            backend,
            case,
            monkeypatch,
            semantic_project_factory,
            time_parse="string",
            certified_calendar=True,
        )
        catalog = ms.load(workspace_dir=tmp_path)
        calendar = ms.ref.period_calendar("sales.fiscal")
        connections = catalog._project._connection_service()
        source_session = connections.source_session
        certification_owners: list[SourceSession] = []

        def capture_certification_owner(name: str, datasource: DatasourceIR) -> SourceSession:
            owner = source_session(name, datasource)
            if owner not in certification_owners:
                certification_owners.append(owner)
            return owner

        monkeypatch.setattr(connections, "source_session", capture_certification_owner)
        catalog.preview(calendar, scope=md.unpruned(max_rows=5, timeout_seconds=30))
        assert any(
            submission.purpose == "semantic.certified_preview"
            for owner in certification_owners
            for submission in owner.submissions
        )
        for owner in certification_owners:
            if owner not in r93_source_trace.owners:
                r93_source_trace.owners.append(owner)
        assert all(owner._closed for owner in certification_owners)
        deadline_controls = [
            {
                "statement_id": item.statement_id,
                "purpose": item.purpose,
                "sql": item.sql,
                "state": item.state,
            }
            for owner in certification_owners
            for item in provider_statement_log(owner._backend)
            if item.statement_id.startswith("mysql.authoring.")
        ]
        if backend == "mysql":
            assert [item["statement_id"] for item in deadline_controls] == [
                "mysql.authoring.install_select_deadline",
                "mysql.authoring.read_select_deadline",
            ]
            assert all(
                item["state"] == "succeeded"
                and item["purpose"] == "semantic.certified_preview.deadline"
                for item in deadline_controls
            )
        entry = catalog.period_calendars.get(calendar)
        assert entry.details().snapshot_status == "current"
        grain = entry.grain("period")
        scope = mv.time_scope(
            start=datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
            end=datetime(2026, 8, 5, 16, tzinfo=timezone.utc),
        )
        grid = mv.time_grid(during=scope, grain=grain)
        session = mv.session.get_or_create("r93-certified-calendar", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        with pytest.raises(AnalysisError, match="conflicts"):
            members.each(mv.time_grid(during=scope, grain=grain, timezone="UTC"))
        fixed = (
            members.each(grid).observe(ms.ref.metric("sales.total"), during=grid.window).execute()
        )
        assert len(fixed.to_pandas()) == 10
        r93_source_trace.record(fixed)
        grouped = fixed.group_by(grid).rollup().execute()
        assert grouped.to_pandas().value.tolist() == [7, 41]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c06-source-{backend}-certified-calendar",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [7, 41],
                "period_days": [2, 3],
                "product_rows": 10,
                "calendar_timezone": "Asia/Shanghai",
                "report_timezone": "UTC",
                "snapshot_current": True,
                "deadline_controls": deadline_controls,
                "conflicting_zone_rejected": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
def test_c06_civil_date_grid_keeps_date_keys_on_foreign_zone(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    from datetime import date

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (1, 2, date(2026, 8, 1)),
        (2, 5, date(2026, 8, 2)),
        (3, 99, date(2026, 8, 3)),
        (4, 77, date(2026, 7, 31)),
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened DATE",
        ",".join(
            f"({identity},1,'a',{amount},{'DATE ' if backend == 'trino' else ''}'{day}')"
            for identity, amount, day in facts
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened Date",
        [
            {"id": identity, "revision": 1, "tenant": "a", "amount": amount, "happened": day}
            for identity, amount, day in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _author_c05_project(backend, case, monkeypatch, semantic_project_factory, time_parse="date")
        session = mv.session.get_or_create("r93-civil-date", report_timezone="UTC")
        zone = ZoneInfo("America/New_York")
        grid = mv.time_grid(
            during=mv.time_scope(
                start=datetime(2026, 8, 1, tzinfo=zone), end=datetime(2026, 8, 3, tzinfo=zone)
            ),
            grain=mv.grain("day"),
            timezone=str(zone),
        )
        fixed = (
            session.members(ms.ref.entity("sales.facts"))
            .each(grid)
            .observe(
                ms.ref.metric("sales.total"),
                during=grid.window,
            )
            .execute()
        )
        frame = fixed.to_pandas()
        assert len(frame) == 8 and set(frame.cell_tag) == {"defined"}
        r93_source_trace.record(fixed)
        grouped = fixed.group_by(grid).rollup().execute()
        assert grouped.to_pandas().value.tolist() == [2, 5]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c06-source-{backend}-civil-date-grid",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [2, 5],
                "product_rows": 8,
                "source_role": "civil_date",
                "report_timezone": "UTC",
                "grid_timezone": str(zone),
                "end_exclusive": True,
                "before_start_excluded": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
def test_c06_native_timestamp_retains_reader_and_grid_authority(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    import pyarrow as pa

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TZ", "UTC")
    instants = [
        "2026-08-01 03:59:59.999999",
        "2026-08-01 04:00:00.000000",
        "2026-08-02 03:59:59.999999",
        "2026-08-02 04:00:00.000000",
        "2026-08-03 04:00:00.000000",
    ]
    facts = list(
        zip(range(9007199254740992, 9007199254740997), [77, 2, 5, 11, 99], instants, strict=True)
    )
    physical = (
        "DATETIME(6)"
        if backend == "mysql"
        else "TIMESTAMP(6)"
        if backend == "trino"
        else "TIMESTAMP"
    )
    data = SourceData(
        f"id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened {physical}",
        ",".join(
            f"({identity},1,'a',{amount},{'TIMESTAMP ' if backend == 'trino' else ''}'{instant}')"
            for identity, amount, instant in facts
        ),
        "id Int64, revision Int64, tenant String, amount Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "revision": 1,
                "tenant": "a",
                "amount": amount,
                "happened": datetime.fromisoformat(instant),
            }
            for identity, amount, instant in facts
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        native = case.session.bind(case.source, source_identity="c06.native-time")
        native_type = native.facts.schema.field("happened").type
        assert pa.types.is_timestamp(native_type)
        assert native_type.unit == "us"
        _author_c05_project(
            backend,
            case,
            monkeypatch,
            semantic_project_factory,
            time_parse="native" if backend == "sqlite" else "native_resolved",
        )
        session = mv.session.get_or_create("r93-native-time", report_timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = mv.time_scope(
            start=datetime(2026, 8, 1, tzinfo=zone),
            end=datetime(2026, 8, 3, tzinfo=zone),
        )
        grid = mv.time_grid(during=scope, grain=mv.grain("day"), timezone=str(zone))
        members = session.members(ms.ref.entity("sales.facts"))
        reader_zone = members._node._live().graph.entity_schema.engine_timezone
        fixed = (
            members.each(grid).observe(ms.ref.metric("sales.total"), during=grid.window).execute()
        )
        frame = fixed.to_pandas()
        assert len(frame) == 10 and set(frame.cell_tag) == {"defined"}
        r93_source_trace.record(fixed)
        grouped = fixed.group_by(grid).rollup().execute()
        assert grouped.to_pandas().value.tolist() == [7, 11]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c06-source-{backend}-native-time",
            {**case.environment, "profile": profile},
            {"columns": data.columns, "values": data.values},
            {
                "values": [7, 11],
                "product_rows": 10,
                "native_time_type": str(native_type),
                "reader_timezone": reader_zone,
                "parser": "timestamp UTC" if backend == "sqlite" else None,
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": str(zone),
                "last_microsecond_included": True,
                "exact_start_included": True,
                "exact_end_excluded": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.runtime
def test_c06_sqlite_native_time_requires_explicit_reader_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    from marivo.analysis.datasets.errors import DatasetConstructionError
    from tests.test_r93_source_deadline import receipt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TZ", "UTC")
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        "(1,1,'a',2,'2026-08-01 04:00:00'),(2,1,'a',5,'2026-08-02 03:59:59.999999')",
        "",
        [],
    )
    with source_case("sqlite", "table", tmp_path, monkeypatch, data) as case:
        _author_c05_project(
            "sqlite", case, monkeypatch, semantic_project_factory, time_parse="native_resolved"
        )
        session = mv.session.get_or_create("r93-native-unresolved", report_timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = mv.time_scope(
            start=datetime(2026, 8, 1, tzinfo=zone), end=datetime(2026, 8, 3, tzinfo=zone)
        )
        grid = mv.time_grid(during=scope, grain=mv.grain("day"), timezone=str(zone))
        members = session.members(ms.ref.entity("sales.facts"))
        assert members._node._live().graph.entity_schema.engine_timezone is None
        with pytest.raises(
            DatasetConstructionError, match="declared or driver-reported read timezone"
        ):
            members.each(grid).observe(ms.ref.metric("sales.total"), during=grid.window)
        assert session.runs().items == ()
        assert session._runtime.last_run_ref is None
        assert not any(owner.submissions for owner in r93_source_trace.owners)
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
    receipt(
        "c06-sqlite-native-time-unresolved",
        {
            "backend": "sqlite",
            "parser": None,
            "engine_timezone": None,
            "host_timezone": "UTC",
            "report_timezone": "Asia/Tokyo",
            "grid_timezone": "America/New_York",
            "unresolved_reader_rejected": True,
            "business_submissions": 0,
            "run_count": 0,
            "resources": 0,
        },
    )


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ["duckdb", "postgres"])
def test_c06_aware_native_repeated_hour_keeps_distinct_instants(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    import pyarrow as pa

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    facts = [
        (9007199254740992, 2, "2026-11-01 01:30:00-04:00"),
        (9007199254740993, 5, "2026-11-01 01:30:00-05:00"),
        (9007199254740994, 11, "2026-11-01 00:00:00-04:00"),
        (9007199254740995, 99, "2026-11-02 00:00:00-05:00"),
    ]
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMPTZ",
        ",".join(f"({identity},1,'a',{amount},'{instant}')" for identity, amount, instant in facts),
        "",
        [],
    )
    with source_case(backend, "table", tmp_path, monkeypatch, data) as case:
        native = case.session.bind(case.source, source_identity="c06.aware-time")
        native_type = native.facts.schema.field("happened").type
        assert pa.types.is_timestamp(native_type) and native_type.tz == "UTC"
        _author_c05_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="native_resolved"
        )
        session = mv.session.get_or_create("r93-aware-time", report_timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = mv.time_scope(
            start=datetime(2026, 11, 1, tzinfo=zone), end=datetime(2026, 11, 2, tzinfo=zone)
        )
        grid = mv.time_grid(during=scope, grain=mv.grain("day"), timezone=str(zone))
        fixed = (
            session.members(ms.ref.entity("sales.facts"))
            .each(grid)
            .observe(ms.ref.metric("sales.total"), during=grid.window)
            .execute()
        )
        frame = fixed.to_pandas()
        assert len(frame) == 4 and set(frame.cell_tag) == {"defined"}
        assert sorted(frame.value.tolist()) == [0, 2, 5, 11]
        r93_source_trace.record(fixed)
        grouped = fixed.group_by(grid).rollup().execute()
        assert grouped.to_pandas().value.tolist() == [18]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c06-source-{backend}-aware-repeated-hour",
            {**case.environment, "profile": "table"},
            {"columns": data.columns, "values": data.values},
            {
                "values": [18],
                "product_rows": 4,
                "native_time_type": str(native_type),
                "parser": None,
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": str(zone),
                "distinct_repeated_hour_values": [2, 5],
                "exact_start_included": True,
                "exact_end_excluded": True,
                "resources": 0,
            },
            grouped,
            (case.session,),
        )


@pytest.mark.runtime
@pytest.mark.parametrize(
    ("backend", "wall", "start", "end"),
    [
        ("duckdb", "2026-03-08 02:30:00", "2026-03-08", "2026-03-09"),
        ("postgres", "2026-11-01 01:30:00", "2026-11-01", "2026-11-02"),
    ],
)
def test_c06_native_wall_gap_or_fold_rejects_before_publication(
    backend: str,
    wall: str,
    start: str,
    end: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    from tests.test_r93_source_deadline import receipt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        f"(1,1,'a',2,'{wall}')",
        "",
        [],
    )
    with source_case(backend, "table", tmp_path, monkeypatch, data) as case:
        _author_c05_project(
            backend,
            case,
            monkeypatch,
            semantic_project_factory,
            read_timezone="America/New_York",
        )
        session = mv.session.get_or_create("r93-invalid-native-wall", report_timezone="Asia/Tokyo")
        zone = ZoneInfo("America/New_York")
        scope = mv.time_scope(
            start=datetime.fromisoformat(start).replace(tzinfo=zone),
            end=datetime.fromisoformat(end).replace(tzinfo=zone),
        )
        grid = mv.time_grid(during=scope, grain=mv.grain("day"), timezone=str(zone))
        logical = (
            session.members(ms.ref.entity("sales.facts"))
            .each(grid)
            .observe(ms.ref.metric("sales.total"), during=grid.window)
        )
        with pytest.raises(MaterializationError) as failure:
            logical.execute()
        assert failure.value.stage == "source_time_validation"
        assert failure.value.expected is not None
        assert failure.value.received is not None
        assert "sales.facts.happened" in failure.value.expected
        assert datetime.fromisoformat(wall).isoformat() in failure.value.received
        assert session._runtime.last_run_ref is not None
        run = session._runtime.store._graph_run(session._runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed"
        with session._runtime.store._connection() as connection:
            assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        product_owners = [owner for owner in r93_source_trace.owners if owner is not case.session]
        assert product_owners and all(owner._closed for owner in product_owners)
        assert any(owner.submissions for owner in product_owners)
        assert all(
            submission.connection_disconnected
            for owner in product_owners
            for submission in owner.submissions
        )
        receipt(
            f"c06-{backend}-native-wall-rejected",
            {
                "backend": backend,
                "wall": wall,
                "read_timezone": str(zone),
                "report_timezone": "Asia/Tokyo",
                "grid_timezone": str(zone),
                "error": str(failure.value),
                "run_lifecycle": run.lifecycle,
                "published_artifacts": 0,
                "resources": 0,
            },
        )
