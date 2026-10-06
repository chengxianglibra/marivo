"""Native definition-owned C10 distribution consumers."""

import json
import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NoReturn

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession, provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.json_support import key_json
from tests.r9_source_cases import Case, SourceData, datasource, source_case
from tests.r93_source_trace import SourceTrace


def _data(backend: str, rows: list[tuple[int, str, int | None]]) -> SourceData:
    return SourceData(
        "id BIGINT, owner VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join(
            f"({identity},'{owner}',{number if number is not None else 'NULL'},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + "'2026-08-01 00:00:00')"
            for identity, owner, number in rows
        ),
        "id Int64, owner String, amount Nullable(Int64), happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "owner": owner,
                "amount": number,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for identity, owner, number in rows
        ],
    )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend,kind",
    [
        (backend, kind)
        for backend in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
        for kind in ("distinct", "identity", "quantile", "approx_distinct", "approx_quantile")
    ]
    + [("sqlite", "float_distinct"), ("sqlite", "unbounded")],
)
def test_c10_native_distribution(
    backend: str,
    kind: Literal[
        "distinct",
        "identity",
        "quantile",
        "approx_distinct",
        "approx_quantile",
        "float_distinct",
        "unbounded",
    ],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    r93_source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    datasets = [
        _data(backend, [(1, "a", 0), (2, "b", 0), (3, "c", 0)]),
        _data(
            backend,
            [
                (9007199254740992, "a", 2),
                (9007199254740993, "a", 6),
                (9007199254740994, "a", 6),
                (9007199254740995, "a", None),
                (9007199254740993, "b", 6),
            ],
        ),
    ]
    if kind == "float_distinct":
        datasets = [
            replace(data, columns=data.columns.replace("amount BIGINT", "amount DOUBLE"))
            for data in datasets
        ]
    with ExitStack() as stack:
        if backend == "duckdb":
            path = tmp_path / "source.duckdb"
            admin = ibis.duckdb.connect(path)
            for name, data in zip(("subjects", "facts"), datasets, strict=True):
                admin.raw_sql(f"CREATE TABLE {name} ({data.columns})")
                admin.raw_sql(f"INSERT INTO {name} VALUES {data.values}")
            admin.disconnect()
            connection = stack.enter_context(
                provider_for(backend).open(
                    datasource(backend, {"path": str(path), "read_only": True})
                )
            )
            subject, facts = [
                Case(connection, TableSourceIR(name), {"backend": backend, "read_only": True})
                for name in ("subjects", "facts")
            ]
        else:
            subject, facts = [
                stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
                for data in datasets
            ]
        args = {
            **subject.session.datasource.fields,
            **{key + "_env": value for key, value in subject.session.datasource.env_refs.items()},
        }
        if "user" in args:
            monkeypatch.setenv("MARIVO_R93_READER", str(args.pop("user")))
            args["user_env"] = "MARIVO_R93_READER"
        models = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, case in (("subjects", subject), ("facts", facts)):
            assert isinstance(case.source, TableSourceIR)
            keys = ["owner"] if name == "subjects" else ["id", "owner"]
            models += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key={keys!r})\n"
            models += (
                f"{name}_owner=ms.dimension_column(name='owner',entity={name},column='owner')\n"
            )
            models += f"{name}_time=ms.time_dimension_column(name='time',entity={name},column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
        models += "identity=ms.measure_column(name='identity',entity=facts,column='id',additivity=ms.additive_all())\n"
        models += "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
        models += "facts_subject=ms.relationship(name='facts_subject',from_entity=facts,to_entity=subjects,keys=[ms.join_on(facts_owner,subjects_owner)])\n"
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in args.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                "sales/models.py": models,
            }
        )
        if backend not in ("duckdb", "sqlite"):

            def forbid_staging(*args: object, **kwargs: object) -> NoReturn:
                pytest.fail("C10 cannot create or drop backend tables")

            monkeypatch.setattr(type(subject.session._backend), "create_table", forbid_staging)
            monkeypatch.setattr(type(subject.session._backend), "drop_table", forbid_staging)
        session = mv.session.get_or_create("r93-distribution", report_timezone="UTC")
        metric = mv.runtime_metric.aggregate(
            ms.ref.measure("sales.facts.identity")
            if kind == "identity"
            else ms.ref.measure("sales.facts.amount"),
            agg="count_distinct"
            if kind in ("distinct", "identity", "float_distinct", "unbounded")
            else "approx_count_distinct"
            if kind == "approx_distinct"
            else ("percentile", 0.25)
            if kind == "quantile"
            else ("approx_percentile", 0.25),
            label="distribution",
        )
        submissions: list[dict[str, str]] = []
        original_batches = SourceSession.batches

        def traced_batches(
            self: SourceSession, read: CompiledRead, *, chunk_size: int
        ) -> SourceBatchStream:
            submissions.append(
                {"sql": read.sql, "purpose": read.purpose, "source_identity": read.source_identity}
            )
            return original_batches(self, read, chunk_size=chunk_size)

        monkeypatch.setattr(SourceSession, "batches", traced_batches)
        native_before = len(r93_source_trace.native_sql)
        refuses = (
            (kind in ("distinct", "identity") and backend == "clickhouse")
            or (kind == "quantile" and backend not in ("duckdb", "postgres"))
            or (kind == "approx_quantile" and backend in ("sqlite", "mysql"))
        )
        if refuses:
            before = set((tmp_path / ".marivo").rglob("*.parquet"))
            with pytest.raises(DatasetConstructionError, match="declared exactness"):
                session.members(ms.ref.entity("sales.subjects")).observe(
                    metric,
                    during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                    via=ms.ref.relationship("sales.facts_subject"),
                )
            assert not submissions
            assert len(r93_source_trace.native_sql) == native_before
            assert set((tmp_path / ".marivo").rglob("*.parquet")) == before
            return
        if kind in ("float_distinct", "unbounded"):
            before = set((tmp_path / ".marivo").rglob("*.parquet"))
            with pytest.raises(
                MethodRegistrationError, match="bounded int64 direct-column distribution"
            ):
                session.members(ms.ref.entity("sales.subjects")).observe(
                    metric,
                    during=None
                    if kind == "unbounded"
                    else mv.time_scope(start="2026-08-01", end="2026-08-02"),
                    via=ms.ref.relationship("sales.facts_subject"),
                ).execute()
            assert not submissions
            assert len(r93_source_trace.native_sql) == native_before
            assert set((tmp_path / ".marivo").rglob("*.parquet")) == before
            return
        observed = session.members(ms.ref.entity("sales.subjects")).observe(
            metric,
            during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            via=ms.ref.relationship("sales.facts_subject"),
        )
        fixed = observed.execute()
        r93_source_trace.record(fixed)
        frame = fixed.to_pandas().set_index("member")
        expected = (
            {"a": 4, "b": 1, "c": 0}
            if kind == "identity"
            else {"a": 2, "b": 1, "c": 0}
            if kind in ("distinct", "approx_distinct")
            else {"a": 4, "b": 6}
        )
        for owner, value in expected.items():
            if kind == "approx_quantile" and owner == "a":
                observed_percentile = frame.loc[owner, "value"]
                assert isinstance(observed_percentile, (int, float))
                assert 2 <= observed_percentile <= 6
            else:
                assert frame.loc[owner, "value"] == value
        if kind in ("quantile", "approx_quantile"):
            assert frame.loc["c", "cell_tag"] == "null"
        assert not any(action.call == "relation.rollup()" for action in fixed.contract().actions)
        for relation in (observed, fixed):
            with pytest.raises(AnalysisError):
                relation.rollup()
            with pytest.raises(AnalysisError):
                relation.compare(relation).attribute(axes=(ms.ref.dimension("sales.facts.owner"),))

        def forbid_source(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Fixed C10 continuation read source")

        native_before_fixed = len(r93_source_trace.native_sql)
        monkeypatch.setattr(SourceSession, "batches", forbid_source)
        restored = session.artifact(fixed.state.artifact_ref)
        assert isinstance(restored, mv.MaterializedNumericRelation)
        assert restored.to_pandas().set_index("member").equals(frame)
        selected = restored.where(
            restored.value.is_defined()
            if kind in ("quantile", "approx_quantile")
            else restored.value.gt(0)
        ).execute()
        selected_frame = selected.to_pandas().set_index("member")
        assert selected_frame.equals(frame.loc[["a", "b"]])
        assert len(r93_source_trace.native_sql) == native_before_fixed
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        r93_source_trace.save(
            f"c10-source-{backend}-{kind}",
            {**subject.environment, "profile": profile},
            {"columns": datasets[1].columns, "values": datasets[1].values},
            {
                "kind": kind,
                "input_identity_types": ["int64", "string"],
                "values": [4, 1, 0]
                if kind == "identity"
                else [2, 1, 0]
                if kind in ("distinct", "approx_distinct")
                else [4, 6, None]
                if kind == "quantile"
                else ["bounded_2_through_6", 6, None],
                "empty_cell": "defined"
                if kind in ("distinct", "identity", "approx_distinct")
                else "null",
                "selected_members": ["a", "b"],
                "fixed_source_reads_forbidden": True,
                "no_additional_native_submission": True,
                "nonadditive_rollup_and_attribution_refused": True,
                "resources": 0,
            },
            selected,
            (subject.session, facts.session),
        )

        assert submissions
        if kind in ("quantile", "approx_quantile"):
            assert any("0.25" in read["sql"] for read in submissions)
        evidence = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence:
            assert fixed._dataset is not None
            Path(evidence, f"supporting-c10-{kind}-{backend}.json").write_text(
                json.dumps(
                    {
                        "backend": backend,
                        "kind": kind,
                        "environment": subject.environment,
                        "input_amounts": [2, 6, 6, None],
                        "source_and_fixed_oracle_passed": True,
                        "nonadditive_rollup_and_attribution_refused": True,
                        "fixed_source_reads_forbidden": True,
                        "issued_submissions": submissions,
                        "physical_keys": [
                            key_json(item.key)
                            for item in descriptor_plan(
                                fixed._dataset.artifact.descriptor, fixed._node.definition
                            ).physical_requirements
                        ],
                        "boundary": "Bounded native int64 distributions; no complete C10 qualification",
                    },
                    sort_keys=True,
                )
            )
