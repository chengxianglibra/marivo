"""Public explicit time predicates over ordered encodings on native backends."""

from collections.abc import Callable
from pathlib import Path

import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject
from tests.analysis.temporal.test_calendar_native_keys import _maria
from tests.analysis.temporal.test_wall_consumer_runtime import _model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("mysql", "mariadb"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_encoded_comparisons_in_public_observation(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    _maria(profile, monkeypatch)
    data = SourceData(
        "id BIGINT, stamp VARCHAR(8), amount BIGINT",
        "(1,'20260801',10),(2,'20260802',20),(3,'20260803',30),(4,'20260804',40),(5,NULL,50)",
        "id Int64, stamp Nullable(String), amount Int64",
        [
            {"id": 1, "stamp": "20260801", "amount": 10},
            {"id": 2, "stamp": "20260802", "amount": 20},
            {"id": 3, "stamp": "20260803", "amount": 30},
            {"id": 4, "stamp": "20260804", "amount": 40},
            {"id": 5, "stamp": None, "amount": 50},
        ],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _model(
            case,
            semantic_project_factory,
            "stamp=ms.time_dimension_column(name='stamp',entity=events,column='stamp',parse=ms.strptime('%Y%m%d'))\n",
            monkeypatch,
        )
        monkeypatch.chdir(tmp_path)
        session = mv.session.get_or_create("encoded-boundaries", timezone="UTC")
        stamp = session.catalog.get("time_dimension:sales.events.stamp")
        amount = session.catalog.get("measure:sales.events.amount")
        assert isinstance(stamp, ms.TimeDimension) and isinstance(amount, ms.Measure)
        bound = session.instant("2026-08-03", timezone="UTC")
        conditions = (
            (stamp >= bound, 70),
            (stamp > bound, 40),
            (stamp <= bound, 60),
            (stamp < bound, 30),
            (stamp == bound, 30),
            (stamp != bound, 70),
            (
                (stamp >= session.instant("2026-08-02")) & (stamp < session.instant("2026-08-04")),
                50,
            ),
            ((stamp >= bound) | stamp.isnull(), 120),
        )
        dialect = {"postgres": "postgres", "clickhouse": "clickhouse"}.get(backend, backend)
        for condition, expected in conditions:
            source_trace.native_sql.clear()
            result = session.observe({"value": amount.sum()}, where=condition).compute()
            assert result.to_pandas()["value"].tolist() == [expected]
            statements = [sqlglot.parse_one(sql, read=dialect) for sql in source_trace.native_sql]
            assert any(
                isinstance(atom.this, sge.Column)
                and atom.this.name == "stamp"
                and isinstance(atom.expression, sge.Literal)
                and atom.expression.this in {"20260802", "20260803", "20260804"}
                for statement in statements
                for where in statement.find_all(sge.Where)
                for atom in where.find_all(sge.GTE, sge.GT, sge.LTE, sge.LT, sge.EQ, sge.NEQ)
            ), source_trace.native_sql
