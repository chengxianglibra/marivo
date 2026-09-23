"""Read-only PostgreSQL Lifecycle replay and retained-history qualification."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pytest
from psycopg import Cursor, ServerCursor, sql
from psycopg.abc import Params, Query

from marivo.analysis.domains.lifecycle_reducers import in_state
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary
from marivo.refs import ref
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_lifecycle_fixtures import END, MODEL, START, history, lifecycle_registry
from tests.lazy_postgres_event_fixtures import event_registry, event_source_tables
from tests.multisource_environment import postgres_analysis as pg

pytestmark = [
    pytest.mark.runtime,
    pytest.mark.skipif(
        os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1", reason="opt-in PostgreSQL service"
    ),
]


def test_complete_history_and_retained_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with event_source_tables() as names:
        with pg.connection(admin=True) as admin:
            for table_key in ("started_rows", "finished_rows"):
                admin.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(names[table_key])))
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (11,1,%s),(21,2,%s)").format(
                    sql.Identifier(names["started_rows"])
                ),
                (START - timedelta(hours=1), START + timedelta(hours=2)),
            )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (12,1,%s),(13,1,%s)").format(
                    sql.Identifier(names["finished_rows"])
                ),
                (START + timedelta(hours=3), START + timedelta(hours=4)),
            )
        base, sidecar = event_registry(names, monkeypatch)
        model, model_sidecar = lifecycle_registry(Path("unused.duckdb"))
        registry = replace(base, state_models=model.state_models)
        registry.freeze()
        sidecar = replace(sidecar, catalog_refs=model_sidecar.catalog_refs)
        runtime = DatasetRuntime.create(tmp_path / "history", "postgres-lifecycle")
        submitted: list[str] = []
        regular_execute = Cursor.execute
        server_execute = ServerCursor.execute

        def regular(
            cursor: Cursor[tuple[object, ...]],
            query: Query,
            params: Params | None = None,
            *,
            prepare: bool | None = None,
            binary: bool | None = None,
        ) -> Cursor[tuple[object, ...]]:
            if cursor.connection.info.user == pg.READER and isinstance(query, str):
                submitted.append(query)
            assert isinstance(query, (str, bytes, sql.SQL, sql.Composed))
            return regular_execute(cursor, query, params, prepare=prepare, binary=binary)

        def server(
            cursor: ServerCursor[tuple[object, ...]],
            query: Query,
            params: Params | None = None,
            *,
            binary: bool | None = None,
        ) -> ServerCursor[tuple[object, ...]]:
            assert cursor.connection.info.user == pg.READER
            if isinstance(query, str):
                submitted.append(query)
            return server_execute(cursor, query, params, binary=binary)

        monkeypatch.setattr(Cursor, "execute", regular)
        monkeypatch.setattr(ServerCursor, "execute", server)
        result = history(runtime.sources(semantic_registry=registry, sidecar=sidecar)).execute()
        frame = result.to_pandas()
        assert frame.model_state.tolist() == ["open", "done", "open"]
        assert frame.interval_status.tolist() == ["completed", "right_censored", "right_censored"]
        assert frame.left_clipped.tolist() == [True, False, False]
        assert frame.valid_from.tolist() == [
            START,
            START + timedelta(hours=3),
            START + timedelta(hours=2),
        ]
        assert frame.valid_to.tolist() == [START + timedelta(hours=3), END, END]
        record = runtime.store.artifact(result.state.artifact_ref.ref)
        assert record is not None
        evidence = record.descriptor.lifecycle_evidence
        assert isinstance(evidence, LifecycleEvidenceSummary)
        assert (
            evidence.row_count,
            evidence.subject_count,
            evidence.seeded_count,
            evidence.not_incepted_count,
            evidence.transition_count,
            evidence.violation_count,
            evidence.left_clipped_count,
        ) == (3, 3, 2, 1, 1, 1, 1)
        assert len(record.descriptor.retained_parts) == 3
        for item in runtime.statistics.submissions:
            if item.domain == "source":
                assert item.sql in submitted
        assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in submitted
        assert not any(
            query.lstrip().upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE"))
            for query in submitted
        )
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; import sys; "
            "from marivo.analysis.materialization.admission import DatasetRuntime; "
            "runtime=DatasetRuntime.open(Path(sys.argv[1]),sys.argv[2]); "
            "history=runtime.artifact(sys.argv[3]); "
            "assert history.to_pandas().model_state.tolist()==['open','done','open']; "
            "assert not runtime.statistics.submissions; "
            "assert history.transitions().execute().to_pandas().transition_count.tolist()==[1]; "
            "assert not any(item.domain=='source' for item in runtime.statistics.submissions)",
            str(tmp_path / "history"),
            runtime.session_ref,
            result.state.artifact_ref.ref,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.parametrize(
    "case",
    [
        "ambiguous",
        "compatible",
        "illegal",
        "empty",
        "empty_duplicate",
        "no_inception",
        "duplicate",
        "null",
        "unknown",
        "stream_failure",
    ],
)
def test_replay_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    with event_source_tables() as names:
        with pg.connection(admin=True) as admin:
            for table_key in ("started_rows", "finished_rows"):
                admin.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(names[table_key])))
            if case == "empty_duplicate":
                admin.execute(
                    sql.SQL("INSERT INTO {} VALUES (1,'EU')").format(
                        sql.Identifier(names["customers"])
                    )
                )
            elif case not in ("empty", "no_inception"):
                admin.execute(
                    sql.SQL("INSERT INTO {} VALUES (%s,1,%s)").format(
                        sql.Identifier(names["started_rows"])
                    ),
                    (None if case == "null" else 1, START),
                )
                if case == "duplicate":
                    admin.execute(
                        sql.SQL("INSERT INTO {} VALUES (1,1,%s)").format(
                            sql.Identifier(names["started_rows"])
                        ),
                        (START + timedelta(hours=1),),
                    )
            if case in ("ambiguous", "compatible", "no_inception", "unknown"):
                instant = START if case == "ambiguous" else START + timedelta(hours=1)
                admin.execute(
                    sql.SQL("INSERT INTO {} VALUES (2,1,%s)").format(
                        sql.Identifier(names["finished_rows"])
                    ),
                    (instant,),
                )
            if case == "illegal":
                admin.execute(
                    sql.SQL("INSERT INTO {} VALUES (3,1,%s)").format(
                        sql.Identifier(names["started_rows"])
                    ),
                    (START + timedelta(hours=1),),
                )
            if case == "compatible":
                instant = START + timedelta(hours=2)
                admin.execute(
                    sql.SQL("INSERT INTO {} VALUES (3,1,%s)").format(
                        sql.Identifier(names["started_rows"])
                    ),
                    (instant,),
                )
                admin.execute(
                    sql.SQL("INSERT INTO {} VALUES (4,1,%s)").format(
                        sql.Identifier(names["finished_rows"])
                    ),
                    (instant,),
                )
        base, sidecar = event_registry(names, monkeypatch)
        model, model_sidecar = lifecycle_registry(Path("unused.duckdb"))
        registry = replace(base, state_models=model.state_models)
        registry.freeze()
        sidecar = replace(sidecar, catalog_refs=model_sidecar.catalog_refs)
        runtime = DatasetRuntime.create(tmp_path / "boundaries", "postgres-lifecycle")
        logical = history(
            runtime.sources(semantic_registry=registry, sidecar=sidecar), complete=case != "unknown"
        )
        if case == "stream_failure":
            from marivo.analysis.materialization.postgres_execution import PostgresBatchStream

            original = PostgresBatchStream.__iter__

            def broken(stream: PostgresBatchStream) -> Iterator[pa.RecordBatch]:
                for batch in original(stream):
                    yield batch
                    if "valid_from" in stream.schema.names:
                        raise RuntimeError("injected late history read failure")

            monkeypatch.setattr(PostgresBatchStream, "__iter__", broken)
            with pytest.raises(RuntimeError, match="injected late history"):
                logical.execute()
            with sqlite3.connect(runtime.store.db_path) as local:
                assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)
        elif case in ("ambiguous", "empty_duplicate", "no_inception", "duplicate", "null"):
            with pytest.raises(MaterializationError, match="Lifecycle validation failed"):
                logical.execute()
            with sqlite3.connect(runtime.store.db_path) as local:
                assert local.execute("SELECT count(*) FROM dataset_artifacts").fetchone() == (0,)
        else:
            result = logical.execute()
            frame = result.to_pandas()
            if case == "empty":
                assert frame.empty
            elif case == "unknown":
                assert frame.interval_status.tolist() == ["coverage_censored", "coverage_censored"]
            elif case == "illegal":
                assert frame.model_state.tolist() == ["open"]
                violations = result.violations().execute().to_pandas()
                assert violations.violation_kind.tolist() == ["illegal_transition"]
            else:
                assert frame.model_state.tolist() == ["open", "done"]
                record = runtime.store.artifact(result.state.artifact_ref.ref)
                assert record is not None
                evidence = record.descriptor.lifecycle_evidence
                assert isinstance(evidence, LifecycleEvidenceSummary)
                assert evidence.violation_count == 2


@pytest.mark.parametrize(
    "method", ["distribution", "grouped", "transitions", "dwell", "violations", "selection"]
)
def test_source_reducers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    with event_source_tables() as names:
        with pg.connection(admin=True) as admin:
            for table_key in ("started_rows", "finished_rows"):
                admin.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(names[table_key])))
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (11,1,%s),(21,2,%s)").format(
                    sql.Identifier(names["started_rows"])
                ),
                (START - timedelta(hours=1), START + timedelta(hours=2)),
            )
            admin.execute(
                sql.SQL("INSERT INTO {} VALUES (12,1,%s),(13,1,%s)").format(
                    sql.Identifier(names["finished_rows"])
                ),
                (START + timedelta(hours=3), START + timedelta(hours=4)),
            )
        base, sidecar = event_registry(names, monkeypatch)
        model, model_sidecar = lifecycle_registry(Path("unused.duckdb"))
        registry = replace(base, state_models=model.state_models)
        registry.freeze()
        sidecar = replace(sidecar, catalog_refs=model_sidecar.catalog_refs)
        runtime = DatasetRuntime.create(tmp_path / "reducer", "postgres-lifecycle")
        logical = history(runtime.sources(semantic_registry=registry, sidecar=sidecar))
        if method in ("distribution", "grouped"):
            frame = (
                logical.distribution(
                    at=(START, END),
                    axes=(ref.dimension("sales.customers.region"),) if method == "grouped" else (),
                )
                .execute()
                .to_pandas()
            )
            assert frame.subject_count.sum() == 3
            assert frame.loc[frame.model_state == "done", "subject_count"].sum() == 1
        elif method == "transitions":
            frame = logical.transitions().execute().to_pandas()
            assert frame.transition_count.tolist() == [1]
            assert frame.share_of_modeled_transitions.tolist() == [1.0]
        elif method == "dwell":
            frame = logical.dwell().execute().to_pandas().set_index("model_state")
            assert frame.loc["open", "mean_duration"] == timedelta(hours=3)
            assert frame.loc["open", "median_duration"] == timedelta(hours=3)
            assert frame.loc["open", "p90_duration"] == timedelta(hours=3)
            assert frame.completed_count.sum() == 1
            assert frame.right_censored_count.sum() == 2
        elif method == "violations":
            frame = logical.violations().execute().to_pandas()
            assert frame.violation_kind.tolist() == ["transition_from_terminal"]
        else:
            frame = (
                logical.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END))
                .execute()
                .to_pandas()
            )
            assert frame.entity_identity.tolist() == [(1,)]
