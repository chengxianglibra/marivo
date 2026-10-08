"""Public source-origin complete-identity and exact-version C03 vectors."""

from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData, datasource, source_case
from tests.support.source_trace import SourceTrace


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
@pytest.mark.parametrize(
    "kind", ["snapshot", "validity", "duplicate", "overlap", "null_id", "null_tenant"]
)
def test_complete_versioned_members(
    backend: str,
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    trace = source_trace
    rows: list[tuple[int | None, str | None, str, str | None, int]] = [
        (9007199254740992, "a", "2026-08-01", "2026-09-01", 10),
        (9007199254740993, "a", "2026-08-01", None, 20),
        (9007199254740993, "b", "2026-08-01", None, 30),
        (9007199254740992, "a", "2026-09-01", None, 40),
    ]
    if kind == "duplicate":
        rows.append(rows[0])
    if kind == "overlap":
        rows.append((9007199254740992, "a", "2026-07-31", None, 99))
    if kind == "null_id":
        rows.append((None, "a", "2026-08-01", None, 50))
    if kind == "null_tenant":
        rows.append((9007199254740994, None, "2026-08-01", None, 50))
    date_prefix = "DATE " if backend == "trino" else ""
    data = SourceData(
        "id BIGINT, tenant VARCHAR(10), beginning DATE, ending DATE, amount BIGINT, enabled BOOLEAN",
        ",".join(
            f"({identity if identity is not None else 'NULL'},"
            + ("NULL" if tenant is None else f"'{tenant}'")
            + f",{date_prefix}'{start}',"
            + ("NULL" if end is None else f"{date_prefix}'{end}'")
            + f",{amount},{str(amount > 15).upper()})"
            for identity, tenant, start, end, amount in rows
        ),
        ("id Nullable(Int64)" if kind == "null_id" else "id Int64")
        + (", tenant Nullable(String)" if kind == "null_tenant" else ", tenant String")
        + ", beginning Date, ending Nullable(Date), amount Int64, enabled Bool",
        [
            {
                "id": identity,
                "tenant": tenant,
                "beginning": date.fromisoformat(start),
                "ending": None if end is None else date.fromisoformat(end),
                "amount": amount,
                "enabled": amount > 15,
            }
            for identity, tenant, start, end, amount in rows
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        case.environment["profile"] = profile
        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_R93_READER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_R93_READER"
        version = (
            "ms.snapshot(partition_field=ms.ref.time_dimension('sales.subjects.beginning'),grain='day',timezone='UTC')"
            if kind in ("snapshot", "duplicate", "null_id", "null_tenant")
            else "ms.validity(valid_from=ms.ref.time_dimension('sales.subjects.beginning'),valid_to=ms.ref.time_dimension('sales.subjects.ending'),interval='closed_open',open_end=(None,),timezone='UTC')"
        )
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"subjects=ms.entity(name='subjects',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id','tenant'],versioning={version})\n"
                + "subject_id=ms.dimension_column(name='id',entity=subjects,column='id')\ntenant=ms.dimension_column(name='tenant',entity=subjects,column='tenant')\n"
                + "beginning=ms.time_dimension_column(name='beginning',entity=subjects,column='beginning',granularity='day')\nending=ms.time_dimension_column(name='ending',entity=subjects,column='ending',granularity='day')\n"
                + "amount=ms.measure_column(name='amount',entity=subjects,column='amount',additivity=ms.additive_all())\n"
                + "enabled=ms.dimension_column(name='enabled',entity=subjects,column='enabled')\n"
                + (
                    "@ms.dimension(entity=subjects)\ndef enabled_predicate(subjects):\n    return subjects.enabled == 1\n"
                    if backend == "mysql"
                    else ""
                ),
            }
        )
        session = mv.session.get_or_create("c03-versions", report_timezone="UTC")
        entity = ms.ref.entity("sales.subjects")
        august = datetime(2026, 8, 1, tzinfo=timezone.utc)
        before_runs = len(session.runs().items)
        construction_calls: list[str] = []

        def no_construction_read(*args: object, **kwargs: object) -> None:
            construction_calls.append("read-or-admit")
            raise AssertionError("Missing version attempted a business read or Run admission")

        with monkeypatch.context() as construction:
            for name in ("bind", "compile", "batches"):
                construction.setattr(SourceSession, name, no_construction_read)
            from marivo.analysis.materialization import graph_store

            construction.setattr(graph_store, "admit", no_construction_read)
            with pytest.raises(AnalysisError) as missing_version:
                session.members(entity)
        assert construction_calls == [] and len(session.runs().items) == before_runs
        assert missing_version.value.expected and missing_version.value.received
        assert missing_version.value.repair is not None and missing_version.value.repair.action
        if kind in ("duplicate", "overlap", "null_id", "null_tenant"):
            from tests.analysis.materialization.publication_fixtures import publication_counts

            before_publication = publication_counts(session)
            before_files = set(tmp_path.rglob("*.parquet"))
            with pytest.raises(
                AnalysisError, match=r"unique|non-overlapping|duplicate|identity|null"
            ) as invalid_identity:
                session.members(entity, at=august).execute()
            assert invalid_identity.value.expected and invalid_identity.value.received
            assert (
                invalid_identity.value.repair is not None and invalid_identity.value.repair.action
            )
            assert set(tmp_path.rglob("*.parquet")) == before_files
            assert publication_counts(session) == before_publication == [0, 0, 0]
            assert len(session.runs().items) == before_runs + 1
            assert session._runtime.last_run_ref is not None
            failed = session._runtime.store._graph_run(session._runtime.last_run_ref)
            assert failed is not None and failed.lifecycle == "failed"
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
            trace.save(
                f"c03-members-{kind}-{backend}",
                case.environment,
                {"columns": data.columns, "values": data.values},
                {
                    "rejected_invalid_identity": True,
                    "published_parquet": 0,
                    "resources": 0,
                    "missing_version_pre_read_pre_run": True,
                    "construction_calls": list(construction_calls),
                    "run_counts_before": before_runs,
                    "run_counts_after": len(session.runs().items),
                    "run_lifecycle": failed.lifecycle,
                    "publication_counts_before": before_publication,
                    "publication_counts_after": publication_counts(session),
                    "missing_version_error": {
                        "type": type(missing_version.value).__name__,
                        "expected": missing_version.value.expected,
                        "received": missing_version.value.received,
                        "repair": missing_version.value.repair.action,
                    },
                    "invalid_identity_error": {
                        "type": type(invalid_identity.value).__name__,
                        "expected": invalid_identity.value.expected,
                        "received": invalid_identity.value.received,
                        "repair": invalid_identity.value.repair.action,
                    },
                },
                None,
                (case.session,),
            )
            return
        with monkeypatch.context() as construction:

            def forbidden(*args: object, **kwargs: object) -> None:
                raise AssertionError("Member construction submitted business rows")

            construction.setattr(SourceSession, "batches", forbidden)
            construction.setattr(SourceSession, "compile", forbidden)
            current = session.members(entity, at=august)
            with pytest.raises(AnalysisError):
                current.read(ms.ref.measure("sales.subjects.amount"))
        fixed = current.execute()
        observed = fixed.to_pandas()
        assert set(zip(observed.member, observed.coord_0, strict=True)) == {
            (9007199254740992, "a"),
            (9007199254740993, "a"),
            (9007199254740993, "b"),
        }
        numeric_result = current.read(ms.ref.measure("sales.subjects.amount"), at=august).execute()
        trace.record(numeric_result)
        read = numeric_result.to_pandas()
        assert dict(zip(zip(read.member, read.coord_0, strict=True), read.value, strict=True)) == {
            (9007199254740992, "a"): 10,
            (9007199254740993, "a"): 20,
            (9007199254740993, "b"): 30,
        }
        categories = current.read(ms.ref.dimension("sales.subjects.tenant"), at=august)
        booleans = None
        if backend == "mysql":
            with monkeypatch.context() as unsupported:

                def no_business_rows(*args: object, **kwargs: object) -> None:
                    raise AssertionError("Unsupported Boolean submitted business rows")

                unsupported.setattr(SourceSession, "batches", no_business_rows)
                with pytest.raises(AnalysisError, match="enabled: int8"):
                    current.read(ms.ref.dimension("sales.subjects.enabled"), at=august)
            booleans = current.read(ms.ref.dimension("sales.subjects.enabled_predicate"), at=august)
        else:
            booleans = current.read(ms.ref.dimension("sales.subjects.enabled"), at=august)
        dates = current.read(ms.ref.time_dimension("sales.subjects.beginning"), at=august)
        assert isinstance(categories, mv.LogicalCategoryRelation)
        if booleans is not None:
            assert isinstance(booleans, mv.LogicalBooleanRelation)
        assert isinstance(dates, mv.LogicalTemporalRelation)
        for relation, expected in (
            (
                categories,
                {
                    (9007199254740992, "a"): "a",
                    (9007199254740993, "a"): "a",
                    (9007199254740993, "b"): "b",
                },
            ),
            (
                booleans,
                {
                    (9007199254740992, "a"): False,
                    (9007199254740993, "a"): True,
                    (9007199254740993, "b"): True,
                },
            ),
            (
                dates,
                {
                    (9007199254740992, "a"): date(2026, 8, 1),
                    (9007199254740993, "a"): date(2026, 8, 1),
                    (9007199254740993, "b"): date(2026, 8, 1),
                },
            ),
        ):
            if relation is None:
                continue
            attribute_result = relation.execute()
            trace.record(attribute_result)
            values = attribute_result.to_pandas()
            assert (
                dict(
                    zip(zip(values.member, values.coord_0, strict=True), values.value, strict=True)
                )
                == expected
            )
        if booleans is not None:
            selected_result = booleans.where(booleans.value.eq(True)).members().execute()
            trace.record(selected_result)
            selected = selected_result.to_pandas()
            assert set(zip(selected.member, selected.coord_0, strict=True)) == {
                (9007199254740993, "a"),
                (9007199254740993, "b"),
            }
        selected_category_result = categories.where(categories.value.eq("b")).members().execute()
        trace.record(selected_category_result)
        selected_category = selected_category_result.to_pandas()
        assert set(zip(selected_category.member, selected_category.coord_0, strict=True)) == {
            (9007199254740993, "b")
        }
        selected_dates_result = dates.where(dates.value.eq(date(2026, 8, 1))).members().execute()
        trace.record(selected_dates_result)
        selected_dates = selected_dates_result.to_pandas()
        assert set(zip(selected_dates.member, selected_dates.coord_0, strict=True)) == set(
            zip(observed.member, observed.coord_0, strict=True)
        )
        if kind == "snapshot":
            grid = mv.time_grid(
                during=mv.time_scope(
                    start="2026-08-01T00:00:00+00:00", end="2026-08-01T02:00:00+00:00"
                ),
                grain=mv.grain("hour"),
            )
            product = current.read(
                ms.ref.measure("sales.subjects.amount"), at=grid.before_end
            ).execute()
            trace.record(product)
            product_rows = product.to_pandas()
            assert set(product_rows.coord_1) == {
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T01:00:00+00:00",
            }
            assert product_rows.groupby(["member", "coord_0"]).size().to_dict() == {
                (9007199254740992, "a"): 2,
                (9007199254740993, "a"): 2,
                (9007199254740993, "b"): 2,
            }
        before = mv.time_scope(start="2026-08-01", end="2026-09-01").before_end
        left = session.members(entity, at=before).execute()
        trace.record(left)
        assert len(left.to_pandas()) == (0 if kind == "snapshot" else 3)
        end_first = mv.time_scope(start="2026-08-01", end="2026-08-02").before_end
        first_period = session.members(entity, at=end_first).execute()
        trace.record(first_period)
        assert set(
            zip(first_period.to_pandas().member, first_period.to_pandas().coord_0, strict=True)
        ) == set(zip(observed.member, observed.coord_0, strict=True))
        if kind == "validity":
            left_values_result = (
                session.members(entity, at=before)
                .read(ms.ref.measure("sales.subjects.amount"), at=before)
                .execute()
            )
            trace.record(left_values_result)
            left_values = left_values_result.to_pandas()
            assert dict(
                zip(
                    zip(left_values.member, left_values.coord_0, strict=True),
                    left_values.value,
                    strict=True,
                )
            ) == {
                (9007199254740992, "a"): 10,
                (9007199254740993, "a"): 20,
                (9007199254740993, "b"): 30,
            }
        september = datetime(2026, 9, 1, tzinfo=timezone.utc)
        later = session.members(entity, at=september).execute().to_pandas()
        assert len(later) == (1 if kind == "snapshot" else 3)
        missing = (
            session.members(entity, at=datetime(2026, 8, 2, tzinfo=timezone.utc))
            .execute()
            .to_pandas()
        )
        assert len(missing) == (0 if kind == "snapshot" else 3)
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        trace.save(
            f"c03-members-{kind}-{backend}",
            case.environment,
            {"columns": data.columns, "values": data.values},
            {
                "pairs": [
                    [9007199254740992, "a"],
                    [9007199254740993, "a"],
                    [9007199254740993, "b"],
                ],
                "numeric": [10, 20, 30],
                "before_end_count": 0 if kind == "snapshot" else 3,
                "first_period_before_end_count": 3,
                "missing_period_count": 0 if kind == "snapshot" else 3,
                "later_period_count": 1 if kind == "snapshot" else 3,
                "boolean_int8_rejected": backend == "mysql",
                "computed_boolean_consumed": backend == "mysql",
                "resources": 0,
            },
            fixed,
            (case.session,),
        )


def test_no_time_key_does_not_specialize_to_float() -> None:
    from dataclasses import replace

    from marivo.analysis.methods import builtin
    from marivo.analysis.methods.physical import Qualified, ScalarType
    from marivo.analysis.methods.semantics import MethodKey

    for method in ("parts_transport", "bind_project", "time.product"):
        candidates = [
            item
            for item in builtin.implementations(MethodKey(method))
            if isinstance(item.qualification, Qualified)
            and item.qualification.implementation_id.startswith("r93.c03.")
        ]
        assert candidates
        for item in candidates:
            key = replace(item.key, input_types=(ScalarType("float64"),))
            assert builtin.specialize_numeric(item, key).key != key


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]
)
@pytest.mark.parametrize("mode", ["complete", "missing", "multiple"])
def test_scalar_path_requires_scoped_complete_keys(
    backend: str,
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    from contextlib import ExitStack

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    trace = source_trace
    population: list[tuple[int, str, int | None]] = [
        (9007199254740992, "a", 0),
        (9007199254740993, "a", 0),
        (9007199254740993, "b", 0),
    ]
    targets: list[tuple[int, str, int | None]] = [
        (9007199254740992, "a", 10),
        (9007199254740993, "a", None),
        (9007199254740993, "b", 30),
        (9, "unrelated", 1),
        (9, "unrelated", 2),
    ]
    if mode == "missing":
        targets = [row for row in targets if row[:2] != (9007199254740993, "a")]
    if mode == "multiple":
        targets.append((9007199254740993, "b", 99))

    def data(rows: list[tuple[int, str, int | None]]) -> SourceData:
        return SourceData(
            "id BIGINT, tenant VARCHAR(16), amount BIGINT",
            ",".join(
                f"({identity},'{tenant}',{amount if amount is not None else 'NULL'})"
                for identity, tenant, amount in rows
            ),
            "id Int64, tenant String, amount Nullable(Int64)",
            [
                {"id": identity, "tenant": tenant, "amount": amount}
                for identity, tenant, amount in rows
            ],
        )

    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with ExitStack() as stack:
        if backend == "duckdb":
            path = tmp_path / "source.duckdb"
            admin = ibis.duckdb.connect(path)
            try:
                for name, rows in (("subjects", population), ("attributes", targets)):
                    source_data = data(rows)
                    admin.raw_sql(f"CREATE TABLE {name} ({source_data.columns})")
                    admin.raw_sql(f"INSERT INTO {name} VALUES {source_data.values}")
            finally:
                admin.disconnect()
            connection = stack.enter_context(
                provider_for(backend).open(
                    datasource(backend, {"path": str(path), "read_only": True})
                )
            )
            root = Case(
                connection, TableSourceIR("subjects"), {"backend": backend, "read_only": True}
            )
            target = Case(
                connection, TableSourceIR("attributes"), {"backend": backend, "read_only": True}
            )
        else:
            root = stack.enter_context(
                source_case(backend, profile, tmp_path, monkeypatch, data(population))
            )
            target = stack.enter_context(
                source_case(backend, profile, tmp_path, monkeypatch, data(targets))
            )
        arguments = {
            **root.session.datasource.fields,
            **{key + "_env": value for key, value in root.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_R93_READER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_R93_READER"
        definitions = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, case in (("subjects", root), ("attributes", target)):
            assert isinstance(case.source, TableSourceIR)
            definitions += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id','tenant'])\n"
            definitions += f"{name}_id=ms.dimension_column(name='id',entity={name},column='id')\n{name}_tenant=ms.dimension_column(name='tenant',entity={name},column='tenant')\n{name}_amount=ms.measure_column(name='amount',entity={name},column='amount',additivity=ms.additive_all())\n"
        definitions += "attributes_path=ms.relationship(name='attributes_path',from_entity=subjects,to_entity=attributes,keys=[ms.join_on(subjects_id,attributes_id),ms.join_on(subjects_tenant,attributes_tenant)])\n"
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in arguments.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                "sales/models.py": definitions,
            }
        )
        root.environment["profile"] = profile
        session = mv.session.get_or_create("c03-scalar-path", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.subjects"))
        field = ms.ref.measure("sales.attributes.amount")
        via = ms.ref.relationship("sales.attributes_path")
        with pytest.raises(AnalysisError, match="explicit path"):
            members.read(field)
        read = members.read(field, via=via)
        if mode != "complete":
            before_files = set(tmp_path.rglob("*.parquet"))
            with pytest.raises(
                AnalysisError, match=r"coverage|single field value|single_value|identity"
            ):
                read.execute()
            assert set(tmp_path.rglob("*.parquet")) == before_files
            assert session._runtime.store.resources(session._runtime.session_ref) == ()
        category = members.read(ms.ref.dimension("sales.subjects.tenant"))
        assert isinstance(category, mv.LogicalCategoryRelation)
        selected = category.where(category.value.eq("b" if mode == "missing" else "a")).members()
        assert isinstance(selected, mv.LogicalAnalysisDomain)
        scoped_result = selected.read(field, via=via).execute()
        scoped = scoped_result.to_pandas()
        assert len(scoped) == (1 if mode == "missing" else 2)
        numeric = scoped.loc[scoped.cell_tag == "defined"]
        assert numeric.value.tolist() == ([30] if mode == "missing" else [10])
        if mode != "missing":
            null = scoped.loc[scoped.cell_tag == "null"]
            assert list(zip(null.member, null.coord_0, strict=True)) == [(9007199254740993, "a")]
        if mode == "complete":
            complete = read.execute().to_pandas()
            assert len(complete) == 3
            assert complete.loc[complete.cell_tag == "defined", "value"].tolist() == [10, 30]
        assert session._runtime.store.resources(session._runtime.session_ref) == ()

        trace.save(
            f"c03-path-{mode}-{backend}",
            root.environment,
            {
                "columns": data(targets).columns,
                "root_values": data(population).values,
                "target_values": data(targets).values,
            },
            {
                "mode": mode,
                "scoped_rows": 1 if mode == "missing" else 2,
                "numeric": [30] if mode == "missing" else [10],
                "null_pairs": [] if mode == "missing" else [[9007199254740993, "a"]],
                "rejected_invalid_full_domain": mode != "complete",
                "resources": 0,
            },
            scoped_result,
            (root.session, target.session),
        )
