"""One generation-bound SQLite authority with atomic metadata transactions."""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal

from marivo._compat import UTC
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunFailure,
    SessionRecord,
    canonical_json,
    decode_failure,
    failure_payload,
    invalid,
    parse_timestamp,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_store import GraphRun


_SCHEMA = """
CREATE TABLE sessions (
 session_ref TEXT PRIMARY KEY NOT NULL CHECK(length(session_ref)>0),
 name TEXT NOT NULL UNIQUE CHECK(length(name)>0), question TEXT,
 report_timezone_name TEXT NOT NULL CHECK(length(report_timezone_name)>0),
 report_timezone_resolution TEXT NOT NULL CHECK(report_timezone_resolution IN ('iana','fixed_offset')),
 created_at TEXT NOT NULL CHECK(length(created_at)>0), updated_at TEXT NOT NULL CHECK(length(updated_at)>0)
) STRICT;
CREATE TABLE runtime_state (
 singleton_key INTEGER PRIMARY KEY CHECK(singleton_key=1),
 current_session_ref TEXT REFERENCES sessions(session_ref) ON DELETE SET NULL
) STRICT;
CREATE TABLE analysis_action_runs (
 run_ref TEXT PRIMARY KEY NOT NULL CHECK(length(run_ref)>0),
 session_ref TEXT NOT NULL REFERENCES sessions(session_ref) ON DELETE RESTRICT,
 execution_key_digest TEXT NOT NULL CHECK(length(execution_key_digest)>0),
 admitted_at TEXT NOT NULL CHECK(length(admitted_at)>0),
 dataset_input_payload TEXT NOT NULL CHECK(length(dataset_input_payload)>0),
 UNIQUE(session_ref,run_ref)
) STRICT;
CREATE TABLE dataset_artifacts (
 artifact_ref TEXT PRIMARY KEY NOT NULL CHECK(length(artifact_ref)>0),
 session_ref TEXT NOT NULL REFERENCES sessions(session_ref) ON DELETE RESTRICT,
 execution_key_digest TEXT NOT NULL CHECK(length(execution_key_digest)>0),
 descriptor_payload TEXT NOT NULL CHECK(length(descriptor_payload)>0),
 committed_at TEXT NOT NULL CHECK(length(committed_at)>0),
 UNIQUE(session_ref,artifact_ref), UNIQUE(session_ref,execution_key_digest)
) STRICT;
CREATE TABLE analysis_action_run_terminals (
 run_ref TEXT PRIMARY KEY NOT NULL CHECK(length(run_ref)>0),
 session_ref TEXT NOT NULL CHECK(length(session_ref)>0),
 outcome TEXT NOT NULL CHECK(outcome IN ('succeeded','failed')),
 terminal_at TEXT NOT NULL CHECK(length(terminal_at)>0),
 output_artifact_ref TEXT, failure_payload TEXT,
 FOREIGN KEY(session_ref,run_ref) REFERENCES analysis_action_runs(session_ref,run_ref) ON DELETE RESTRICT,
 FOREIGN KEY(session_ref,output_artifact_ref) REFERENCES dataset_artifacts(session_ref,artifact_ref) ON DELETE RESTRICT,
 UNIQUE(session_ref,output_artifact_ref),
 CHECK((outcome='succeeded' AND output_artifact_ref IS NOT NULL AND failure_payload IS NULL)
    OR (outcome='failed' AND output_artifact_ref IS NULL AND failure_payload IS NOT NULL))
) STRICT;
CREATE TABLE analysis_action_run_inputs (
 run_ref TEXT NOT NULL CHECK(length(run_ref)>0),
 session_ref TEXT NOT NULL CHECK(length(session_ref)>0),
 input_ordinal INTEGER NOT NULL CHECK(input_ordinal>=0),
 artifact_ref TEXT NOT NULL REFERENCES dataset_artifacts(artifact_ref) ON DELETE RESTRICT,
 PRIMARY KEY(run_ref,input_ordinal),
 FOREIGN KEY(session_ref,run_ref) REFERENCES analysis_action_runs(session_ref,run_ref) ON DELETE RESTRICT
) STRICT;
CREATE TABLE dataset_evidence (
 artifact_ref TEXT PRIMARY KEY NOT NULL REFERENCES dataset_artifacts(artifact_ref) ON DELETE RESTRICT,
 evidence_digest TEXT NOT NULL CHECK(length(evidence_digest)>0),
 finding_count INTEGER NOT NULL CHECK(finding_count>=0),
 finding_set_digest TEXT NOT NULL CHECK(length(finding_set_digest)>0),
 extractor_contract_versions_payload TEXT NOT NULL CHECK(length(extractor_contract_versions_payload)>0)
) STRICT;
CREATE TABLE findings (
 finding_ref TEXT PRIMARY KEY NOT NULL CHECK(length(finding_ref)>0),
 artifact_ref TEXT NOT NULL REFERENCES dataset_evidence(artifact_ref) ON DELETE RESTRICT,
 finding_ordinal INTEGER NOT NULL CHECK(finding_ordinal>=0),
 finding_identity_digest TEXT NOT NULL CHECK(length(finding_identity_digest)>0),
 finding_body_payload TEXT NOT NULL CHECK(length(finding_body_payload)>0),
 UNIQUE(artifact_ref,finding_ordinal), UNIQUE(artifact_ref,finding_identity_digest)
) STRICT;
CREATE TABLE action_resource_journal (
 run_ref TEXT NOT NULL REFERENCES analysis_action_runs(run_ref) ON DELETE RESTRICT,
 resource_kind TEXT NOT NULL CHECK(resource_kind IN ('planner_temporary_relation','backend_execution','private_parquet_staging','local_storage_staging')),
 execution_domain_id TEXT NOT NULL CHECK(length(execution_domain_id)>0),
 ownership_nonce TEXT NOT NULL CHECK(length(ownership_nonce)>0),
 cleanup_capability_id TEXT NOT NULL CHECK(length(cleanup_capability_id)>0),
 safe_locator TEXT NOT NULL CHECK(length(safe_locator)>0),
 PRIMARY KEY(run_ref,resource_kind,execution_domain_id,safe_locator)
) STRICT;
CREATE INDEX sessions_recency ON sessions(updated_at,session_ref);
CREATE INDEX run_admission_recency ON analysis_action_runs(session_ref,admitted_at,run_ref);
CREATE INDEX terminal_recency ON analysis_action_run_terminals(outcome,terminal_at,run_ref);
CREATE INDEX run_inputs_artifact ON analysis_action_run_inputs(artifact_ref,run_ref);
CREATE INDEX artifact_recency ON dataset_artifacts(session_ref,committed_at,artifact_ref);
"""


def _generation_error(version: object, expected: int = 8) -> IntegrityError:
    return IntegrityError(
        expected=f"an existing complete Session Store with user_version={expected}",
        received=f"Session Store user_version={version}",
        repair="Preserve the old Store unchanged and create a new named Session in a fresh project; old Stores cannot be resumed or migrated.",
        stage="store_generation",
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _new_run_ref() -> str:
    """Allocate the Store's Run reference format before key construction."""
    return "run_" + secrets.token_hex(12)


def _enable_wal(conn: sqlite3.Connection) -> None:
    # SQLite does not consistently invoke its busy handler when changing the
    # journal mode. Concurrent generation creators must retry that transition.
    deadline = time.monotonic() + 5
    while True:
        try:
            mode: object = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        except sqlite3.OperationalError as error:
            # Python 3.10 does not expose sqlite_errorcode. Match only SQLite's
            # primary SQLITE_BUSY message; other failures retain their cause.
            if str(error) != "database is locked" or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
        else:
            if mode != "wal":
                raise invalid("Store 8 requires WAL durability")
            return


def _cell(row: sqlite3.Row, name: str) -> object:
    value: object = row[name]
    return value


def _text(row: sqlite3.Row, name: str) -> str:
    value = _cell(row, name)
    if type(value) is not str or not value:
        raise invalid(f"invalid {name} relation field")
    return value


def _optional(row: sqlite3.Row, name: str) -> str | None:
    value = _cell(row, name)
    if value is None:
        return None
    if type(value) is not str:
        raise invalid(f"invalid nullable {name} relation field")
    return value


def _rows(
    conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> tuple[sqlite3.Row, ...]:
    result: list[sqlite3.Row] = []
    for value in conn.execute(sql, parameters):
        if not isinstance(value, sqlite3.Row):
            raise invalid("invalid SQLite row decoder")
        result.append(value)
    return tuple(result)


def _one(
    conn: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> sqlite3.Row | None:
    rows = _rows(conn, sql, parameters)
    if len(rows) > 1:
        raise invalid("duplicate selected metadata")
    return None if not rows else rows[0]


class SessionStore:
    """Private Store; the caller holds the owning guard around all mutations."""

    def __init__(self, project_root: str | Path, *, existing_only: bool = False) -> None:
        self.layout = MaterializationLayout(Path(project_root))
        generations = self.layout.generation_dir.parent
        if generations.exists() and any(p.name != "v8" for p in generations.iterdir()):
            raise _generation_error("old generation directory")
        self._initialize(existing_only=existing_only)

    @classmethod
    def open_existing(cls, project_root: str | Path) -> SessionStore:
        """Open an existing complete Store 8 without initializing state."""
        try:
            return cls(project_root, existing_only=True)
        except (sqlite3.Error, OSError):
            raise invalid("selected Store 8 is unavailable") from None

    @property
    def project_root(self) -> Path:
        return self.layout.project_root

    @property
    def db_path(self) -> Path:
        return self.layout.store_db

    @property
    def store_id(self) -> str:
        from marivo.analysis.materialization.contracts import digest

        return "store_" + digest(str(self.db_path))

    def _readonly_uri(self) -> tuple[str, bool]:
        """Use immutable SQLite only for a nonwritable, WAL-absent database."""
        immutable = (
            not os.access(self.db_path, os.W_OK)
            and not os.access(self.db_path.parent, os.W_OK)
            and not os.path.lexists(str(self.db_path) + "-wal")
        )
        return self.db_path.as_uri() + (
            "?mode=ro&immutable=1" if immutable else "?mode=ro"
        ), immutable

    def _connection(self, *, readonly: bool = False) -> sqlite3.Connection:
        uri = self._readonly_uri()[0] if readonly else self.db_path.as_uri() + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        if not readonly:
            conn.execute("PRAGMA synchronous=FULL")
        version: object = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != self.layout.generation:
            conn.close()
            raise invalid("unsupported Store generation")
        return conn

    def _initialize(self, *, existing_only: bool = False) -> None:
        if self.db_path.exists():
            uri, immutable = self._readonly_uri()
            read = sqlite3.connect(uri, uri=True)
            try:
                read.execute("PRAGMA foreign_keys=ON")
                read.execute("BEGIN")
                version: object = read.execute("PRAGMA user_version").fetchone()[0]
                tables = read.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if version != self.layout.generation:
                    raise _generation_error(version, self.layout.generation)
                if version == self.layout.generation:
                    expected = {
                        "sessions",
                        "runtime_state",
                        "analysis_action_runs",
                        "analysis_action_run_terminals",
                        "analysis_action_run_inputs",
                        "dataset_artifacts",
                        "dataset_evidence",
                        "findings",
                        "action_resource_journal",
                    }
                    if {row[0] for row in tables} != expected:
                        raise invalid(f"incomplete v{self.layout.generation} schema")
                    strict = read.execute("PRAGMA table_list").fetchall()
                    if any(row[5] != 1 for row in strict if row[1] in expected):
                        raise invalid(f"non-STRICT v{self.layout.generation} relation")
                    if immutable:
                        # Immutable SQLite reports its local journal mode as delete.
                        # The durable header remains the authority for a clean WAL Store.
                        with self.db_path.open("rb") as source:
                            wal_header = source.read(20)[18:20]
                        if wal_header != b"\x02\x02":
                            raise invalid("Store 8 requires WAL durability")
                    else:
                        journal: object = read.execute("PRAGMA journal_mode").fetchone()[0]
                        if journal != "wal":
                            raise invalid("Store 8 requires WAL durability")
            finally:
                read.close()
            if version == self.layout.generation:
                return
        if existing_only:
            raise invalid(f"selected v{self.layout.generation} Store is absent")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Publish only a complete, closed generation file. Competing creators never see
        # an empty generation-zero database or perform a migration in place.
        with TemporaryDirectory(prefix="store-init-", dir=self.db_path.parent) as directory:
            staged = Path(directory) / "session_store.db"
            conn = sqlite3.connect(staged, timeout=5, isolation_level=None)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                _enable_wal(conn)
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version={self.layout.generation}")
                conn.commit()
            finally:
                conn.close()
            with staged.open("rb") as source:
                os.fsync(source.fileno())
            try:
                os.link(staged, self.db_path)
            except FileExistsError:
                self._initialize(existing_only=True)
            else:
                directory_fd = os.open(self.db_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connection(readonly=True)
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _session(row: sqlite3.Row) -> SessionRecord:
        if parse_timestamp(_text(row, "updated_at")) < parse_timestamp(_text(row, "created_at")):
            raise invalid("Session update predates creation")
        resolution = _text(row, "report_timezone_resolution")
        if resolution not in ("iana", "fixed_offset"):
            raise invalid("unknown timezone resolution")
        return SessionRecord(
            _text(row, "session_ref"),
            _text(row, "name"),
            _optional(row, "question"),
            _text(row, "report_timezone_name"),
            "iana" if resolution == "iana" else "fixed_offset",
            _text(row, "created_at"),
            _text(row, "updated_at"),
        )

    def session(self, session_ref: str) -> SessionRecord | None:
        with self._read() as conn:
            row = _one(conn, "SELECT * FROM sessions WHERE session_ref=?", (session_ref,))
            return None if row is None else self._session(row)

    def session_by_name(self, name: str) -> SessionRecord | None:
        with self._read() as conn:
            row = _one(conn, "SELECT * FROM sessions WHERE name=?", (name,))
            return None if row is None else self._session(row)

    def create_session(
        self,
        name: str,
        *,
        question: str | None = None,
        session_ref: str | None = None,
        report_timezone_name: str = "UTC",
        report_timezone_resolution: Literal["iana", "fixed_offset"] = "iana",
    ) -> SessionRecord:
        with self._write() as conn:
            row = _one(conn, "SELECT * FROM sessions WHERE name=?", (name,))
            if row is not None:
                return self._session(row)
            ref = session_ref or "sess_" + secrets.token_hex(12)
            now = _now()
            conn.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
                (
                    ref,
                    name,
                    question,
                    report_timezone_name,
                    report_timezone_resolution,
                    now,
                    now,
                ),
            )
            row = _one(conn, "SELECT * FROM sessions WHERE session_ref=?", (ref,))
            if row is None:
                raise invalid("created Session is absent")
            result = self._session(row)
            conn.execute(
                "INSERT INTO runtime_state VALUES(1,?) ON CONFLICT(singleton_key) DO UPDATE SET current_session_ref=excluded.current_session_ref",
                (result.session_ref,),
            )
        return result

    def activate(self, session_ref: str, *, question: str | None = None) -> SessionRecord:
        with self._write() as conn:
            row = _one(conn, "SELECT * FROM sessions WHERE session_ref=?", (session_ref,))
            if row is None:
                raise invalid("missing activation Session")
            current = self._session(row)
            conn.execute(
                "UPDATE sessions SET question=?,updated_at=? WHERE session_ref=?",
                (current.question if question is None else question, _now(), session_ref),
            )
            conn.execute(
                "INSERT INTO runtime_state VALUES(1,?) ON CONFLICT(singleton_key) DO UPDATE SET current_session_ref=excluded.current_session_ref",
                (session_ref,),
            )
            row = _one(conn, "SELECT * FROM sessions WHERE session_ref=?", (session_ref,))
            if row is None:
                raise invalid("activated Session is absent")
            return self._session(row)

    def current(self) -> SessionRecord | None:
        with self._read() as conn:
            row = _one(
                conn,
                "SELECT s.* FROM sessions s JOIN runtime_state r ON r.current_session_ref=s.session_ref WHERE r.singleton_key=1",
            )
            return None if row is None else self._session(row)

    def _graph_run(self, run_ref: str) -> GraphRun | None:
        from marivo.analysis.materialization.graph_store import run

        with self._read() as conn:
            return run(self, conn, run_ref)

    def _resource_run(self, run_ref: str) -> GraphRun | None:
        return self._graph_run(run_ref)

    def reserve(self, resource: ResourceRecord) -> None:
        with self._write() as conn:
            from marivo.analysis.materialization.graph_store import run as graph_run

            run = graph_run(self, conn, resource.run_ref)
            if run is None or run.lifecycle != "incomplete":
                raise invalid("resource reservation requires incomplete admission")
            conn.execute(
                "INSERT INTO action_resource_journal VALUES(?,?,?,?,?,?)",
                (
                    resource.run_ref,
                    resource.resource_kind,
                    resource.execution_domain_id,
                    resource.ownership_nonce,
                    resource.cleanup_capability_id,
                    resource.safe_locator,
                ),
            )

    def resources(self, session_ref: str) -> tuple[ResourceRecord, ...]:
        with self._read() as conn:
            return self._resources(conn, session_ref)

    @staticmethod
    def _run_resources(conn: sqlite3.Connection, run_ref: str) -> tuple[ResourceRecord, ...]:
        return tuple(
            ResourceRecord(
                _text(row, "run_ref"),
                _text(row, "resource_kind"),
                _text(row, "execution_domain_id"),
                _text(row, "ownership_nonce"),
                _text(row, "cleanup_capability_id"),
                _text(row, "safe_locator"),
            )
            for row in _rows(
                conn,
                "SELECT * FROM action_resource_journal WHERE run_ref=? ORDER BY resource_kind,execution_domain_id,safe_locator",
                (run_ref,),
            )
        )

    @staticmethod
    def _resources(conn: sqlite3.Connection, session_ref: str) -> tuple[ResourceRecord, ...]:
        rows = _rows(
            conn,
            "SELECT j.* FROM action_resource_journal j JOIN analysis_action_runs a USING(run_ref) WHERE a.session_ref=? ORDER BY j.run_ref,j.resource_kind,j.execution_domain_id,j.safe_locator",
            (session_ref,),
        )
        return tuple(
            ResourceRecord(
                _text(row, "run_ref"),
                _text(row, "resource_kind"),
                _text(row, "execution_domain_id"),
                _text(row, "ownership_nonce"),
                _text(row, "cleanup_capability_id"),
                _text(row, "safe_locator"),
            )
            for row in rows
        )

    @staticmethod
    def _delete_resources(
        conn: sqlite3.Connection, run_ref: str, resources: tuple[ResourceRecord, ...]
    ) -> None:
        for resource in resources:
            if resource.run_ref != run_ref:
                raise invalid("foreign resource obligation")
            changed = conn.execute(
                "DELETE FROM action_resource_journal WHERE run_ref=? AND resource_kind=? AND execution_domain_id=? AND safe_locator=? AND ownership_nonce=? AND cleanup_capability_id=?",
                (
                    run_ref,
                    resource.resource_kind,
                    resource.execution_domain_id,
                    resource.safe_locator,
                    resource.ownership_nonce,
                    resource.cleanup_capability_id,
                ),
            ).rowcount
            if changed != 1:
                raise invalid("unmatched exact resource obligation")

    def discharge(self, resource: ResourceRecord) -> None:
        with self._write() as conn:
            self._delete_resources(conn, resource.run_ref, (resource,))

    def fail(
        self,
        run_ref: str,
        failure: RunFailure,
        *,
        resolved_resources: tuple[ResourceRecord, ...] = (),
    ) -> None:
        payload = canonical_json(failure_payload(failure))
        decode_failure(payload)
        with self._write() as conn:
            from marivo.analysis.materialization.graph_store import run as graph_run

            run = graph_run(self, conn, run_ref)
            if run is None or run.lifecycle != "incomplete":
                raise invalid("terminal history cannot be rewritten")
            conn.execute(
                "INSERT INTO analysis_action_run_terminals VALUES(?,?,?,?,?,?)",
                (run_ref, run.session_ref, "failed", _now(), None, payload),
            )
            self._delete_resources(conn, run_ref, resolved_resources)
