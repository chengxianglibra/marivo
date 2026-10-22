"""Long governed observation routes through real native source consumers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.execution_fixtures import source_graph
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.datasource.source_cases import Case, SourceData, datasource, source_case
from tests.shared_fixtures import terminal_runs
from tests.support.source_trace import capture_source


@pytest.mark.slow
@pytest.mark.parametrize(
    "depth,key_type",
    [(4, "int64"), (4, "compound")],
)
def test_long_path_sum_and_count(
    depth: int,
    key_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    instant = datetime(2026, 8, 1, tzinfo=timezone.utc)
    compound = key_type == "compound"
    owner_values = (1, 1, 2) if compound else ("a", "b", "c") if key_type == "string" else (1, 2, 3)
    owner_type = "VARCHAR(10)" if key_type == "string" else "BIGINT"
    native_type = "String" if key_type == "string" else "Int64"
    members = SourceData(
        f"owner {owner_type}",
        ",".join(f"({value!r})" for value in owner_values),
        f"owner {native_type}",
        [{"owner": value} for value in owner_values],
    )
    entries = [
        (1, owner_values[0], 2**53 + 1, 10, instant),
        (2, owner_values[0], 2, 20, instant),
        (3, owner_values[1], 7, 10, instant),
        (4, owner_values[0], 99, 30, datetime(2026, 7, 31, tzinfo=timezone.utc)),
    ]
    facts = SourceData(
        f"id BIGINT, owner {owner_type}, amount BIGINT, sequence BIGINT, happened "
        + ("TIMESTAMP(6) WITH TIME ZONE" if backend == "trino" else "TIMESTAMP(6)"),
        ",".join(
            f"({identity},{owner!r},{amount},{sequence},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + repr(point.replace(tzinfo=None).isoformat(sep=" "))
            + ")"
            for identity, owner, amount, sequence, point in entries
        ),
        f"id Int64, owner {native_type}, amount Int64, sequence Int64, happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "owner": owner,
                "amount": amount,
                "sequence": sequence,
                "happened": point,
            }
            for identity, owner, amount, sequence, point in entries
        ],
    )
    if compound:
        members = replace(
            members,
            columns=members.columns + ", segment VARCHAR(10)",
            values=",".join(
                f"({owner!r},{segment!r})"
                for owner, segment in zip(owner_values, ("a", "b", "c"), strict=True)
            ),
            clickhouse_columns=members.clickhouse_columns + ", segment String",
            rows=[
                {**row, "segment": segment}
                for row, segment in zip(members.rows, ("a", "b", "c"), strict=True)
            ],
        )
        facts = replace(
            facts,
            columns=facts.columns + ", segment VARCHAR(10)",
            values=",".join(
                f"({identity},{owner!r},{amount},{sequence},"
                + repr(point.replace(tzinfo=None).isoformat(sep=" "))
                + f",{segment!r})"
                for (identity, owner, amount, sequence, point), segment in zip(
                    entries, ("a", "a", "b", "a"), strict=True
                )
            ),
            clickhouse_columns=facts.clickhouse_columns + ", segment String",
            rows=[
                {**row, "segment": segment}
                for row, segment in zip(facts.rows, ("a", "a", "b", "a"), strict=True)
            ],
        )
    outputs: dict[str, mv.AnalysisFrame] = {}
    with ExitStack() as stack:
        names = ("facts", *(f"bridge{i}" for i in range(depth - 1)), "subjects")
        datasets = (facts, *(members for _ in range(depth)))
        if backend == "duckdb":
            database = tmp_path / "source.duckdb"
            admin = ibis.duckdb.connect(database)
            try:
                for name, data in zip(names, datasets, strict=True):
                    admin.raw_sql(f"CREATE TABLE {name} ({data.columns})")
                    admin.raw_sql(f"INSERT INTO {name} VALUES {data.values}")
            finally:
                admin.disconnect()
            connection = stack.enter_context(
                provider_for(backend).open(
                    datasource(backend, {"path": str(database), "read_only": True})
                )
            )
            cases = [Case(connection, TableSourceIR(name), {"backend": backend}) for name in names]
        else:
            cases = [
                stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
                for data in datasets
            ]
        args = dict(cases[0].session.datasource.fields)
        args.update(
            {key + "_env": value for key, value in cases[0].session.datasource.env_refs.items()}
        )
        if "user" in args:
            monkeypatch.setenv("MARIVO_PATH_READER", str(args.pop("user")))
            args["user_env"] = "MARIVO_PATH_READER"
        models = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, case in zip(names, cases, strict=True):
            assert isinstance(case.source, TableSourceIR)
            keys = ["id"] if name == "facts" else ["owner", "segment"] if compound else ["owner"]
            models += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key={keys!r})\n"
            models += (
                f"{name}_owner=ms.dimension_column(name='owner',entity={name},column='owner')\n"
            )
            if compound:
                models += f"{name}_segment=ms.dimension_column(name='segment',entity={name},column='segment')\n"
        models += "instant=ms.time_dimension_column(name='instant',entity=facts,column='happened',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
        models += "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
        for index, (left, right) in enumerate(pairwise(names)):
            extra = f",ms.join_on({left}_segment,{right}_segment)" if compound else ""
            models += f"hop{index}=ms.relationship(name='hop{index}',from_entity={left},to_entity={right},keys=[ms.join_on({left}_owner,{right}_owner){extra}])\n"
        models += (
            "amount_value=amount\n"
            "time_value=closed.resolve(instant)\nfacts_value=closed.resolve(facts)\n"
            "@ms.metric()\ndef total():\n    return amount_value.sum(time=time_value)\n"
            "@ms.metric()\ndef number():\n    return facts_value.count(time=time_value)\n"
        )
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in args.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
                "sales/models.py": models,
            }
        )
        trace = capture_source(monkeypatch)
        session = mv.session.create("long-path", timezone="UTC")
        population = session.members(session.catalog.entities.get("sales.subjects"))
        expected_keys = ((1, "a"), (1, "b"), (2, "c")) if compound else owner_values
        for metric, expected in (("total", [2**53 + 3, 7, 0]), ("number", [2, 1, 0])):
            expression = session.catalog.metrics.get("sales." + metric)
            logical = session.observe(
                {"value": expression},
                by=mv.product(session.time_scope(start="2026-08-01", end="2026-08-02"), population),
            )
            binding = source_graph(logical).signature.observation
            assert binding is not None and len(binding.contributions) == 1
            assert {
                tuple(step.ref.path for step in read.path)
                for read in binding.contributions[0].coordinates
            } == {tuple(f"sales.hop{index}" for index in range(depth))}
            result = logical.persist()
            frame = result.to_pandas()
            coordinate = ordinary_columns(frame)[0]
            assert dict(zip(frame[coordinate], frame[("value", "value")], strict=True)) == dict(
                zip(expected_keys, expected, strict=True)
            )
            outputs[metric] = result
            if metric == "number":
                field = session.catalog.fields.get(
                    "sales.facts.segment" if compound else "sales.facts.owner"
                )
                sliced = expression.where(field == ("a" if compound else owner_values[0]))
                selected = session.observe(
                    {"value": sliced},
                    by=mv.product(
                        session.time_scope(start="2026-08-01", end="2026-08-02"), population
                    ),
                ).persist()
                selected_frame = selected.to_pandas()
                coordinate = ordinary_columns(selected_frame)[0]
                assert dict(
                    zip(selected_frame[coordinate], selected_frame[("value", "value")], strict=True)
                ) == dict(zip(expected_keys, (2, 0, 0), strict=True))
                outputs["sliced"] = selected
        assert trace.native_sql
        assert trace.owners and all(owner._closed for owner in trace.owners)
        before = terminal_runs(session)
        snapshots = {name: snapshot(value) for name, value in outputs.items()}
        with monkeypatch.context() as offline:
            offline.setattr(ms, "load", forbidden)
            offline.setattr("marivo.datasource.adapters.SourceSession.execute", forbidden)
            for name, value in outputs.items():
                assert snapshot(session.persisted_frame(value.artifact.id)) == snapshots[name]
        assert terminal_runs(session) == before
        assert session._runtime.store.resources(session.id) == ()
