"""Long governed observation routes through real native source consumers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.methods.physical import Backend, ScalarType, SourceShape, TimeShape
from marivo.analysis.methods.registry import REGISTRY
from marivo.datasource.adapters import provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import Case, SourceData, datasource, source_case
from tests.support.documentation import _blocks
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
@pytest.mark.parametrize(
    "backend,depth,key_type",
    [
        (backend, 4, "int64")
        for backend in ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
    ]
    + [(backend, depth, "int64") for backend in ("duckdb", "sqlite") for depth in (3, 9)]
    + [(backend, 4, "string") for backend in ("postgres", "mysql", "trino", "clickhouse")]
    + [("postgres", 4, "compound")],
)
def test_long_path_sum_and_count(
    backend: Backend,
    depth: int,
    key_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
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
    outputs = {}
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
        models += "instant=ms.time_dimension_column(name='instant',entity=facts,column='happened',granularity='second',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
        models += "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
        models += "total=ms.aggregate(name='total',measure=amount,agg='sum',time=instant,empty=ms.empty.zero())\n"
        models += "number=ms.count(name='number',entity=facts,time=instant)\n"
        for index, (left, right) in enumerate(pairwise(names)):
            extra = f",ms.join_on({left}_segment,{right}_segment)" if compound else ""
            models += f"hop{index}=ms.relationship(name='hop{index}',from_entity={left},to_entity={right},keys=[ms.join_on({left}_owner,{right}_owner){extra}])\n"
        models += "sequence=ms.dimension_column(name='sequence',entity=facts,column='sequence')\n"
        models += "event_id=ms.dimension_column(name='id',entity=facts,column='id')\n"
        models += (
            "@ms.event(name='entry',identity=(event_id,),occurred_at=instant,participants=(ms.participant(name='subject',path=("
            + ",".join(f"hop{i}" for i in range(depth))
            + ",),cardinality='one'),))\ndef entry(rows):\n    return ms.all_rows()\n"
        )
        models += "order=ms.business_order(name='order',subject=subjects,sequences=(ms.event_sequence(entry,sequence,order='integer'),),ai_context=ms.ai_context(business_definition='Per Subject native event sequence.'))\n"
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
        session = mv.session.get_or_create("long-path", report_timezone="UTC")
        population = session.members(ms.ref.entity("sales.subjects"))
        routes = mv.routes(
            mv.route(
                ms.ref.entity("sales.facts"),
                through=tuple(ms.ref.relationship(f"sales.hop{i}") for i in range(depth)),
            )
        )
        for metric, expected in (("total", [2**53 + 3, 7, 0]), ("number", [2, 1, 0])):
            logical = population.observe(
                ms.ref.metric("sales." + metric),
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                via=routes,
            )
            if metric == "number":
                node = logical._node.root
                assert isinstance(node, MethodNode)
                declaration = next(
                    item
                    for item in REGISTRY.lookup(node.method).implementations
                    if item.key.shape
                    == SourceShape(backend, "table", "native", TimeShape("instant", "us", "UTC"))
                    and item.key.input_types
                    == (ScalarType("string" if key_type == "string" else "int64"),)
                    and item.key.input_domains == ("entity",)
                    and item.key.route == "ibis"
                )
                selected_method = REGISTRY.select(
                    declaration.key,
                    tuple(edge.node.signature for edge in node.inputs),
                    node.parameters,
                )
                assert selected_method.implementation == declaration
            result = logical.execute()
            if compound:
                assert result._dataset is not None
                records = result._dataset.verified().primary.to_pylist()
                assert {(row["key_0"], row["key_1"]): row["value"] for row in records} == dict(
                    zip(((1, "a"), (1, "b"), (2, "c")), expected, strict=True)
                )
                assert all(row["cell_tag"] == "defined" for row in records)
            else:
                frame = result.to_pandas().sort_values("member")
                assert frame.member.tolist() == list(owner_values)
                assert frame.value.tolist() == expected
                assert frame.cell_tag.tolist() == ["defined"] * 3
            outputs[metric] = snapshot(result)
            if metric == "number":
                assert result._dataset is not None
                state = next(
                    part.table
                    for part in result._dataset.verified().parts
                    if part.role == "original_state"
                )
                counts = dict(
                    zip(
                        list(
                            zip(state["key_0"].to_pylist(), state["key_1"].to_pylist(), strict=True)
                        )
                        if compound
                        else state["key_0"].to_pylist(),
                        state["original_state__count"].to_pylist(),
                        strict=True,
                    )
                )
                expected_keys = ((1, "a"), (1, "b"), (2, "c")) if compound else owner_values
                assert counts == dict(zip(expected_keys, (2, 1, 0), strict=True))
                sliced = mv.runtime_metric.slice(
                    ms.ref.metric("sales.number"),
                    by={
                        ms.ref.dimension(
                            "sales.facts.segment" if compound else "sales.facts.owner"
                        ): "a" if compound else owner_values[0]
                    },
                    label="only_one",
                )
                selected = population.observe(
                    sliced, during=mv.time_scope(start="2026-08-01", end="2026-08-02"), via=routes
                ).execute()
                if compound:
                    assert selected._dataset is not None
                    assert {
                        (row["key_0"], row["key_1"]): row["value"]
                        for row in selected._dataset.verified().primary.to_pylist()
                    } == {(1, "a"): 2, (1, "b"): 0, (2, "c"): 0}
                else:
                    assert selected.to_pandas().sort_values("member").value.tolist() == [2, 0, 0]
        if depth == 4:
            namespace = {"mv": mv, "ms": ms, "population": population}
            code = next(
                block
                for block in _blocks("en", "analysis-workflow")
                if block.startswith("long_routes =")
            )
            exec(compile(code, "latest-long-route-example", "exec"), namespace)
            documented = namespace["long_count"]
            assert isinstance(documented, mv.MaterializedNumericRelation)
            assert sorted(documented.to_pandas().value.tolist()) == [0, 1, 2]
        if backend in ("duckdb", "sqlite"):
            anchors = session.anchors(
                ms.participant_role(event=ms.ref.event("sales.entry"), name="subject"),
                population=population,
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                business_order=ms.ref.business_order("sales.order"),
            )
            for metric, expected in (("total", [2, 0, 0]), ("number", [1, 0, 0])):
                result = anchors.observe(
                    ms.ref.metric("sales." + metric),
                    within=mv.elapsed(mv.duration(seconds=10)),
                    via=routes,
                ).execute()
                assert result.to_pandas().value.tolist() == expected
        assert session._runtime.store.resources(session.id) == ()

    if depth == 4:
        (tmp_path / "observations.json").write_text(
            json.dumps({"session": session.id, "inputs": outputs})
        )
        shutil.rmtree(tmp_path / "models")
        for database in (*tmp_path.glob("*.sqlite"), *tmp_path.glob("*.duckdb")):
            database.unlink()
        for phase in ("fixed", "cold"):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.analysis.graph.observation_recovery_worker",
                    str(tmp_path),
                    phase,
                ],
                cwd=PROJECT_ROOT,
                env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
