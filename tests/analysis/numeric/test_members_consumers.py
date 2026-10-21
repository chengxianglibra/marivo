"""Public source-origin complete-identity and exact-version C03 vectors."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData, datasource, source_case
from tests.shared_fixtures import terminal_runs
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize("kind", ["snapshot", "duplicate", "null_tenant"])
def test_complete_versioned_members(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    trace = source_trace
    rows: list[tuple[int, str | None, str, str | None, int]] = [
        (9007199254740992, "a", "2026-08-01", "2026-09-01", 10),
        (9007199254740993, "a", "2026-08-01", None, 20),
        (9007199254740993, "b", "2026-08-01", None, 30),
        (9007199254740992, "a", "2026-09-01", None, 40),
    ]
    if kind == "duplicate":
        rows.append(rows[0])
    if kind == "null_tenant":
        rows.append((9007199254740994, None, "2026-08-01", None, 50))
    data = SourceData(
        "id BIGINT, tenant VARCHAR(10), beginning DATE, ending DATE, amount BIGINT, enabled BOOLEAN",
        ",".join(
            f"({identity},"
            + ("NULL" if tenant is None else f"'{tenant}'")
            + f",'{start}',"
            + ("NULL" if end is None else f"'{end}'")
            + f",{amount},{str(amount > 15).upper()})"
            for identity, tenant, start, end, amount in rows
        ),
        "id Int64"
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
    profile = "table"
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
        version = "ms.snapshot(partition_field=ms.ref.time_dimension('sales.subjects.beginning'),timezone='UTC')"
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
                + "beginning=ms.time_dimension_column(name='beginning',entity=subjects,column='beginning')\nending=ms.time_dimension_column(name='ending',entity=subjects,column='ending')\n"
                + "amount=ms.measure_column(name='amount',entity=subjects,column='amount',additivity=ms.additive_all())\n"
                + "enabled=ms.dimension_column(name='enabled',entity=subjects,column='enabled')\n"
                + (
                    "@ms.dimension(entity=subjects)\ndef enabled_predicate(subjects):\n    return subjects.enabled == 1\n"
                    if backend == "mysql"
                    else ""
                ),
            }
        )
        session = mv.session.create("c03-versions", timezone="UTC")
        entity = session.catalog.entities.get("sales.subjects")
        august = session.instant("2026-08-01")
        before_runs = len(terminal_runs(session))
        calls: list[str] = []

        def no_construction_read(*args: object, **kwargs: object) -> None:
            calls.append("business-read")
            raise AssertionError("Member construction attempted a business read")

        with monkeypatch.context() as construction:
            construction.setattr(SourceSession, "bind", no_construction_read)
            construction.setattr(SourceSession, "execute", no_construction_read)
            unanchored = session.members(entity)
            unanchored_read = session.read(
                unanchored, {"amount": session.catalog.fields.get("sales.subjects.amount")}
            )
            current = session.members(entity, at=august)
        assert calls == [] and len(terminal_runs(session)) == before_runs
        with monkeypatch.context() as preparation:
            preparation.setattr(SourceSession, "execute", no_construction_read)
            with pytest.raises(AnalysisError) as missing_version:
                unanchored_read.compute()
        assert calls == [] and len(terminal_runs(session)) == before_runs
        assert (
            missing_version.value.expected
            and missing_version.value.received
            and missing_version.value.repair
        )
        expected = {(9007199254740992, "a"), (9007199254740993, "a"), (9007199254740993, "b")}
        if kind == "null_tenant":
            with pytest.raises(AnalysisError):
                session.frame(current).compute()
            return
        projected = session.frame(current).compute()
        frame = projected.to_pandas()
        assert set(frame.iloc[:, 0]) == expected
        assert len(frame) == 3
        fields = {
            name: session.catalog.fields.get(f"sales.subjects.{name}").at(august)
            for name in ("amount", "tenant", "enabled", "beginning")
        }
        values = session.read(current, fields)
        if kind == "duplicate":
            with pytest.raises(AnalysisError):
                values.compute()
            assert session._runtime.store.resources(session.id) == ()
            return
        result = values.persist()
        read = result.to_pandas()
        keyed = read.set_index(read.columns[0])
        assert keyed[("value", "amount")].to_dict() == {
            (9007199254740992, "a"): 10,
            (9007199254740993, "a"): 20,
            (9007199254740993, "b"): 30,
        }
        assert keyed[("value", "tenant")].to_dict() == {
            (9007199254740992, "a"): "a",
            (9007199254740993, "a"): "a",
            (9007199254740993, "b"): "b",
        }
        assert keyed[("value", "enabled")].to_dict() == {
            (9007199254740992, "a"): False,
            (9007199254740993, "a"): True,
            (9007199254740993, "b"): True,
        }
        assert set(read[("value", "beginning")]) == {date(2026, 8, 1)}
        enabled = True
        selected_values = values.where(values["enabled"] == enabled)
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.entities.get("sales.subjects")),),
        )
        assert set(session.frame(selected).compute().to_pandas().iloc[:, 0]) == {
            (9007199254740993, "a"),
            (9007199254740993, "b"),
        }
        selected_values = values.where(values["tenant"] == "b")
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.entities.get("sales.subjects")),),
        )
        assert session.frame(selected).compute().to_pandas().iloc[:, 0].tolist() == [
            (9007199254740993, "b")
        ]
        selected_values = values.where(values["beginning"] == date(2026, 8, 1))
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.entities.get("sales.subjects")),),
        )
        assert set(session.frame(selected).compute().to_pandas().iloc[:, 0]) == expected
        for point, expected_count in (
            ("2026-08-31T23:59:59.999999+00:00", 0),
            ("2026-08-01T23:59:59.999999+00:00", 3),
            ("2026-09-01", 1),
            ("2026-08-02", 0),
        ):
            assert (
                len(
                    session.frame(session.members(entity, at=session.instant(point)))
                    .compute()
                    .to_pandas()
                )
                == expected_count
            )
        assert session._runtime.store.resources(session.id) == ()
        assert trace.native_sql
        assert session.persisted_frame(result.artifact.id).to_pandas().equals(read)


def test_scalar_path_scopes_complete_keys_and_trusts_cardinality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    from contextlib import ExitStack

    backend = "duckdb"
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

    profile = "table"
    with ExitStack() as stack:
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
            provider_for(backend).open(datasource(backend, {"path": str(path), "read_only": True}))
        )
        root = Case(connection, TableSourceIR("subjects"), {"backend": backend, "read_only": True})
        target = Case(
            connection, TableSourceIR("attributes"), {"backend": backend, "read_only": True}
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
        session = mv.session.create("c03-scalar-path", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.subjects"))
        field = session.catalog.fields.get("sales.attributes.amount")
        via = session.catalog.relationships.get("sales.attributes_path")
        inferred = session.read(members, {"value": field})
        explicit = session.read(members, {"value": field.via((via,))})
        inferred_backing, explicit_backing = inferred._backing, explicit._backing
        assert isinstance(inferred_backing, ExpressionFrameBacking)
        assert isinstance(explicit_backing, ExpressionFrameBacking)
        assert isinstance(inferred_backing.root, SourceFrameBinding)
        assert isinstance(explicit_backing.root, SourceFrameBinding)
        assert inferred_backing.root.graph.fingerprint == explicit_backing.root.graph.fingerprint
        category = session.read(
            members, {"tenant": session.catalog.fields.get("sales.subjects.tenant")}
        )
        selected_values = category.where(category["tenant"] == "a")
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.entities.get("sales.subjects")),),
        )
        scoped_result = session.read(selected, {"value": field.via((via,))}).compute()
        scoped = scoped_result.to_pandas()
        assert len(scoped) == 2
        assert scoped[("value", "value")].dropna().tolist() == [10]
        assert scoped.loc[scoped[("value", "value")].isna()].iloc[:, 0].tolist() == [
            (9007199254740993, "a")
        ]
        with pytest.raises(AnalysisError):
            explicit.compute()
        assert session._runtime.store.resources(session.id) == ()
        assert trace.native_sql
