"""One native setup binds all licensed C10 distributions to offline recovery."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import NoReturn

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.distribution_fixtures import distribution_data
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import Case, datasource, source_case
from tests.shared_fixtures import terminal_runs
from tests.support.json import Json, encode, obj, read
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import SourceTrace


@pytest.mark.slow
def test_native_distributions_independent_fixed_and_cold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    datasets = [
        distribution_data(backend, [(1, "a", 0), (2, "b", 0), (3, "c", 0)]),
        distribution_data(
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
            models += f"{name}_time=ms.time_dimension_column(name='time',entity={name},column='happened',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
        models += "identity=ms.measure_column(name='identity',entity=facts,column='id',additivity=ms.additive_all())\n"
        models += "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
        models += "facts_subject=ms.relationship(name='facts_subject',from_entity=facts,to_entity=subjects,keys=[ms.join_on(facts_owner,subjects_owner)])\n"
        models += "frozen = ms.finalize()\n"
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
        if backend != "duckdb":

            def forbid_staging(*args: object, **kwargs: object) -> NoReturn:
                pytest.fail("C10 cannot create or drop backend tables")

            monkeypatch.setattr(type(subject.session._backend), "create_table", forbid_staging)
            monkeypatch.setattr(type(subject.session._backend), "drop_table", forbid_staging)
        session = mv.session.create("r94-distribution", timezone="UTC")
        outputs: dict[str, mv.AnalysisFrame] = {}
        for kind in ("approx_distinct", "distinct", "identity", "quantile", "approx_quantile"):
            field = session.catalog.fields.get(
                "sales.facts.identity" if kind == "identity" else "sales.facts.amount"
            )
            time = session.catalog.fields.get("sales.facts.time")
            metric = (
                field.count_distinct(time=time)
                if kind in ("distinct", "identity")
                else field.approx_nunique(time=time)
                if kind == "approx_distinct"
                else field.quantile(0.25, time=time)
                if kind == "quantile"
                else field.approx_quantile(0.25, time=time)
            )
            query = session.observe(
                {"value": metric},
                by=mv.product(
                    session.time_scope(start="2026-08-01", end="2026-08-02"),
                    session.members(session.catalog.entities.get("sales.subjects")),
                ),
            )
            if kind == "approx_quantile":
                before = terminal_runs(session)
                with pytest.raises((AnalysisError, SemanticExpressionError)) as refused:
                    query.persist()
                error = refused.value
                assert isinstance(error, (AnalysisError, SemanticExpressionError))
                assert error.expected and error.received and error.repair
                assert terminal_runs(session) == before
                continue
            value = query.persist()
            frame = value.to_pandas()
            mapped = dict(zip(frame.iloc[:, 0], frame[("value", "value")], strict=True))
            if kind in ("distinct", "identity", "approx_distinct"):
                assert mapped == {"a": 4 if kind == "identity" else 2, "b": 1, "c": 0}
            else:
                assert mapped["a"] == 4 and mapped["b"] == 6 and pd.isna(mapped["c"])
            outputs[kind] = value
            source_trace.save(
                "distribution-source-" + backend + "-" + kind,
                {**subject.environment, "profile": profile},
                {"facts": datasets[1].values, "identity_types": ["int64", "string"]},
                {
                    "kind": kind,
                    "empty_cell": "null" if "quantile" in kind else "defined",
                    "a_exact": 4
                    if kind in ("identity", "quantile")
                    else 2
                    if "quantile" not in kind
                    else None,
                },
                value,
                (subject.session, facts.session),
            )
        state: dict[str, Json] = {
            "session": session.id,
            "outputs": {
                name: {"artifact": value.artifact.id, "snapshot": snapshot(value)}
                for name, value in outputs.items()
            },
        }
        (tmp_path / "r94-distribution.json").write_bytes(encode(state))
    shutil.rmtree(tmp_path / "models")
    for pattern in ("source.duckdb*",):
        for path in tmp_path.glob(pattern):
            path.unlink()
    script = r"""
import os
import sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import marivo.analysis as mv
from tests.shared_fixtures import terminal_runs
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.support.json import encode, obj, read
root, phase, output = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
state = read(root / 'r94-distribution.json')
with patch.object(ms, 'load', forbidden), patch.object(SemanticProject, 'load', forbidden), patch.object(SourceSession, '__enter__', forbidden), patch.object(SourceSession, 'execute', forbidden):
    session = mv.session.resume(state['session'])
    before = len(terminal_runs(session))
    for name, item in obj(state['outputs']).items():
        value = session.persisted_frame(item['artifact'])
        assert snapshot(value) == item['snapshot']
        frame = value.to_pandas()
        values = dict(zip(frame.iloc[:,0], frame[('value','value')], strict=True))
        if name == 'quantile':
            assert values['a'] == 4 and values['b'] == 6 and pd.isna(values['c'])
        else:
            assert values == {'a':4 if name=='identity' else 2,'b':1,'c':0}
    assert len(terminal_runs(session)) == before
output.write_bytes(encode({'pid':os.getpid(), 'phase':phase}))
"""
    reports: list[Json] = []
    for phase in ("offline", "cold"):
        output = tmp_path / (phase + ".json")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), phase, str(output)],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(read(output))
    assert len({os.getpid(), *(obj(report)["pid"] for report in reports)}) == 3
