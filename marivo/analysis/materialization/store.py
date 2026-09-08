"""One normalized v3 SQLite authority with short atomic metadata transactions."""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from marivo._compat import UTC
from marivo.analysis.evidence._dataset_types import Finding
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactMetadata,
    ArtifactRecord,
    EvidenceRecord,
    ResourceRecord,
    RunDatasetInput,
    RunFailure,
    RunRecord,
    SessionRecord,
    canonical_json,
    decode_descriptor,
    decode_failure,
    decode_run_input,
    encode_descriptor,
    evidence_for,
    failure_payload,
    invalid,
    parse_timestamp,
    run_input_payload,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.object_termination import forget_object_termination
from marivo.analysis.materialization.ownership import (
    object_artifact_prefix,
    owns_resource,
    validate_receipt_owner,
)

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
 resource_kind TEXT NOT NULL CHECK(resource_kind IN ('planner_temporary_relation','backend_execution','private_parquet_staging','local_storage_staging','engine_storage_staging','object_storage_staging')),
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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    """One selected producer and its obligations from a single Store snapshot."""

    run: RunRecord
    resources: tuple[ResourceRecord, ...]


@contextmanager
def _recovery_metadata(run_ref: str | None = None) -> Iterator[None]:
    """Give selected nested decoders the recovery operation's diagnostic context."""
    try:
        yield
    except IntegrityError as error:
        if error.stage == "reconciliation":
            raise
        raise IntegrityError(
            expected="valid admissions, terminals, Artifacts, Evidence and resource obligations for Session recovery",
            received=error.received or "invalid selected recovery metadata",
            repair="Preserve the selected Session's metadata, outputs and resource journal; inspect and repair the inconsistent metadata before retrying Session recovery.",
            stage="reconciliation",
            run_ref=run_ref,
        ) from None


def _forget_termination(resources: tuple[ResourceRecord, ...]) -> None:
    from marivo.analysis.materialization.resources import forget_local_termination

    forget_local_termination(resources)
    forget_object_termination(resources)


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
                raise invalid("v3 Store requires WAL durability")
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

    def __init__(self, project_root: str | Path) -> None:
        self.layout = MaterializationLayout(Path(project_root))
        self._initialize()

    @classmethod
    def open_existing(cls, project_root: str | Path) -> SessionStore:
        """Open only an existing complete v3 authority without initializing state."""
        result = cls.__new__(cls)
        result.layout = MaterializationLayout(Path(project_root))
        unavailable = False
        try:
            result._initialize(existing_only=True)
        except (sqlite3.Error, OSError):
            unavailable = True
        if unavailable:
            raise invalid("selected v3 Store is unavailable")
        return result

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
        if version != 3:
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
                if version not in (0, 3) or (version == 0 and (tables or existing_only)):
                    raise invalid("unsupported Store generation")
                if version == 3:
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
                        raise invalid("incomplete v3 schema")
                    strict = read.execute("PRAGMA table_list").fetchall()
                    if any(row[5] != 1 for row in strict if row[1] in expected):
                        raise invalid("non-STRICT v3 relation")
                    if immutable:
                        # Immutable SQLite reports its local journal mode as delete.
                        # The durable header remains the authority for a clean WAL Store.
                        with self.db_path.open("rb") as source:
                            wal_header = source.read(20)[18:20]
                        if wal_header != b"\x02\x02":
                            raise invalid("v3 Store requires WAL durability")
                    else:
                        journal: object = read.execute("PRAGMA journal_mode").fetchone()[0]
                        if journal != "wal":
                            raise invalid("v3 Store requires WAL durability")
            finally:
                read.close()
            if version == 3:
                return
        if existing_only:
            raise invalid("selected v3 Store is absent")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=0")
            _enable_wal(conn)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if tables:
                    raise invalid("unversioned existing Store")
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute("PRAGMA user_version=3")
            elif version != 3:
                raise invalid("unsupported Store generation")
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

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

    def _run(self, conn: sqlite3.Connection, run_ref: str) -> RunRecord | None:
        row = _one(conn, "SELECT * FROM analysis_action_runs WHERE run_ref=?", (run_ref,))
        if row is None:
            return None
        session_ref = _text(row, "session_ref")
        input_rows = _rows(
            conn,
            "SELECT * FROM analysis_action_run_inputs WHERE run_ref=? ORDER BY input_ordinal",
            (run_ref,),
        )
        for ordinal, input_row in enumerate(input_rows):
            if (
                _cell(input_row, "input_ordinal") != ordinal
                or _text(input_row, "session_ref") != session_ref
            ):
                raise invalid("invalid Run input order or owner")
        inputs = tuple(_text(item, "artifact_ref") for item in input_rows)
        terminal = _one(
            conn, "SELECT * FROM analysis_action_run_terminals WHERE run_ref=?", (run_ref,)
        )
        arguments = (
            _text(row, "run_ref"),
            session_ref,
            _text(row, "execution_key_digest"),
            _text(row, "admitted_at"),
            decode_run_input(_text(row, "dataset_input_payload")),
            inputs,
        )
        admitted_at = parse_timestamp(arguments[3])
        if terminal is None:
            return RunRecord(*arguments, lifecycle="incomplete")
        if _text(terminal, "session_ref") != session_ref:
            raise invalid("foreign Run terminal owner")
        outcome = _text(terminal, "outcome")
        output = _optional(terminal, "output_artifact_ref")
        failure = _optional(terminal, "failure_payload")
        terminal_at = _text(terminal, "terminal_at")
        if parse_timestamp(terminal_at) < admitted_at:
            raise invalid("Run terminal predates admission")
        if outcome == "succeeded" and output is not None and failure is None:
            return RunRecord(
                *arguments,
                lifecycle="succeeded",
                terminal_at=terminal_at,
                output_artifact_ref=output,
            )
        if outcome == "failed" and output is None and failure is not None:
            return RunRecord(
                *arguments,
                lifecycle="failed",
                terminal_at=terminal_at,
                failure=decode_failure(failure),
            )
        raise invalid("contradictory Run terminal")

    def run(self, run_ref: str) -> RunRecord | None:
        with self._read() as conn:
            run = self._run(conn, run_ref)
            if (
                run is not None
                and run.output_artifact_ref is not None
                and self._artifact(conn, run.output_artifact_ref) is None
            ):
                raise invalid("succeeded Run has no Artifact")
            return run

    def incomplete(self, session_ref: str) -> tuple[RunRecord, ...]:
        with self._read() as conn:
            rows = _rows(
                conn,
                "SELECT a.run_ref FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? AND t.run_ref IS NULL ORDER BY a.admitted_at,a.run_ref",
                (session_ref,),
            )
            if len(rows) > 1:
                raise invalid("multiple incomplete Runs in one Session")
            result = []
            for row in rows:
                run = self._run(conn, _text(row, "run_ref"))
                if run is None:
                    raise invalid("selected incomplete Run is absent")
                result.append(run)
            return tuple(result)

    def admit(
        self,
        session_ref: str,
        execution_key_digest: str,
        dataset_input: RunDatasetInput,
        *,
        input_artifact_refs: tuple[str, ...] = (),
        run_ref: str | None = None,
    ) -> RunRecord:
        payload = canonical_json(run_input_payload(dataset_input))
        decode_run_input(payload)
        ref = run_ref or "run_" + secrets.token_hex(12)
        with self._write() as conn:
            pending = _one(
                conn,
                "SELECT a.run_ref FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? AND t.run_ref IS NULL",
                (session_ref,),
            )
            if pending is not None:
                raise invalid("Session already has an incomplete Run")
            if (
                _one(
                    conn,
                    "SELECT artifact_ref FROM dataset_artifacts WHERE session_ref=? AND execution_key_digest=?",
                    (session_ref, execution_key_digest),
                )
                is not None
            ):
                raise invalid("execution key already has an Artifact")
            for artifact_ref in input_artifact_refs:
                if self._artifact(conn, artifact_ref) is None:
                    raise invalid("Run input Artifact is absent")
            conn.execute(
                "INSERT INTO analysis_action_runs VALUES(?,?,?,?,?)",
                (ref, session_ref, execution_key_digest, _now(), payload),
            )
            conn.executemany(
                "INSERT INTO analysis_action_run_inputs VALUES(?,?,?,?)",
                (
                    (ref, session_ref, ordinal, artifact_ref)
                    for ordinal, artifact_ref in enumerate(input_artifact_refs)
                ),
            )
            result = self._run(conn, ref)
            if result is None:
                raise invalid("admitted Run is absent")
        return result

    def reserve(self, resource: ResourceRecord) -> None:
        with self._write() as conn:
            run = self._run(conn, resource.run_ref)
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

    def recovery_snapshot(self, session_ref: str) -> tuple[RecoveryEntry, ...]:
        """Validate only this Session's incomplete or still-obligated producers."""
        with _recovery_metadata(), self._read() as conn:
            if (
                _one(conn, "SELECT session_ref FROM sessions WHERE session_ref=?", (session_ref,))
                is None
            ):
                raise IntegrityError(
                    expected="an existing Session selected for recovery",
                    received="missing recovery Session",
                    repair="Select an existing Session or restore its metadata before retrying recovery.",
                    stage="reconciliation",
                )
            rows = _rows(
                conn,
                "SELECT a.run_ref FROM analysis_action_runs a LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? AND (t.run_ref IS NULL OR EXISTS (SELECT 1 FROM action_resource_journal j WHERE j.run_ref=a.run_ref)) ORDER BY a.admitted_at,a.run_ref",
                (session_ref,),
            )
            resources = self._resources(conn, session_ref)
            entries: list[RecoveryEntry] = []
            incomplete_count = 0
            for row in rows:
                run_ref = _text(row, "run_ref")
                with _recovery_metadata(run_ref):
                    run = self._run(conn, run_ref)
                    if run is None or run.session_ref != session_ref:
                        raise IntegrityError(
                            expected="the selected producer owned by the recovering Session",
                            received="selected recovery producer is absent or foreign",
                            repair="Preserve the resource journal and repair the selected producer's Session ownership before retrying recovery.",
                            stage="reconciliation",
                            run_ref=run_ref,
                        )
                    owned = tuple(item for item in resources if item.run_ref == run.run_ref)
                    if run.lifecycle == "incomplete":
                        incomplete_count += 1
                        if incomplete_count > 1:
                            raise IntegrityError(
                                expected="at most one incomplete producer in a serialized Session",
                                received="multiple incomplete Runs in one Session",
                                repair="Preserve the Run records and resource journal; repair the conflicting admissions before retrying Session recovery.",
                                stage="reconciliation",
                                run_ref=run_ref,
                            )
                        if (
                            _one(
                                conn,
                                "SELECT artifact_ref FROM dataset_artifacts WHERE session_ref=? AND execution_key_digest=?",
                                (session_ref, run.execution_key_digest),
                            )
                            is not None
                        ):
                            raise IntegrityError(
                                expected="an incomplete producer without a committed output",
                                received="incomplete producer has a committed output",
                                repair="Preserve the Run and output; inspect their terminal and ownership metadata before retrying recovery. Do not reconstruct publication.",
                                stage="reconciliation",
                                run_ref=run_ref,
                            )
                    elif run.output_artifact_ref is not None:
                        output = self._artifact(conn, run.output_artifact_ref)
                        if output is None:
                            raise IntegrityError(
                                expected="the selected succeeded producer's complete output",
                                received="succeeded Run has no Artifact",
                                repair="Preserve the Run and resource journal; restore the complete committed output metadata before retrying Session recovery.",
                                stage="reconciliation",
                                run_ref=run_ref,
                            )
                        receipts = (
                            output.descriptor.storage_receipt,
                            *(part.storage_receipt for part in output.descriptor.retained_parts),
                        )
                        if any(
                            owns_resource(receipt, item) for item in owned for receipt in receipts
                        ):
                            raise IntegrityError(
                                expected="Session recovery with output ownership transferred in the publication transaction",
                                received="committed output remains a cleanup obligation",
                                repair="Preserve the committed outputs; inspect and repair their conflicting resource journal ownership before retrying Session recovery.",
                                stage="reconciliation",
                                run_ref=run_ref,
                            )
                    entries.append(RecoveryEntry(run, owned))
            return tuple(entries)

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
        _forget_termination((resource,))

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
            run = self._run(conn, run_ref)
            if run is None or run.lifecycle != "incomplete":
                raise invalid("terminal history cannot be rewritten")
            conn.execute(
                "INSERT INTO analysis_action_run_terminals VALUES(?,?,?,?,?,?)",
                (run_ref, run.session_ref, "failed", _now(), None, payload),
            )
            self._delete_resources(conn, run_ref, resolved_resources)
        _forget_termination(resolved_resources)

    def _artifact_metadata(
        self, conn: sqlite3.Connection, artifact_ref: str
    ) -> ArtifactMetadata | None:
        row = _one(conn, "SELECT * FROM dataset_artifacts WHERE artifact_ref=?", (artifact_ref,))
        if row is None:
            return None
        descriptor = decode_descriptor(_text(row, "descriptor_payload"))
        session_ref = _text(row, "session_ref")
        execution_key = _text(row, "execution_key_digest")
        terminals = _rows(
            conn,
            "SELECT * FROM analysis_action_run_terminals WHERE output_artifact_ref=?",
            (artifact_ref,),
        )
        if len(terminals) != 1:
            raise invalid("Artifact has no unique producer")
        producer = self._run(conn, _text(terminals[0], "run_ref"))
        if (
            producer is None
            or producer.lifecycle != "succeeded"
            or producer.session_ref != session_ref
            or producer.execution_key_digest != execution_key
        ):
            raise invalid("Artifact producer identity mismatch")
        definition = producer.dataset_input
        if producer.terminal_at is None or parse_timestamp(
            _text(row, "committed_at")
        ) != parse_timestamp(producer.terminal_at):
            raise invalid("Artifact publication time disagrees with its producer terminal")
        if (
            definition.definition_fingerprint != descriptor.definition_fingerprint
            or definition.row_contract_fingerprint != descriptor.row_contract_fingerprint
            or definition.row_set_contract_fingerprint != descriptor.row_set_contract_fingerprint
            or definition.shape_id != descriptor.row_contract.shape_id
        ):
            raise invalid("Artifact contracts disagree with admission")
        prefix = (
            self.layout.artifact_dir(session_ref, artifact_ref)
            .relative_to(self.project_root)
            .as_posix()
        )
        receipts = (
            descriptor.storage_receipt,
            *(part.storage_receipt for part in descriptor.retained_parts),
        )
        for receipt in receipts:
            validate_receipt_owner(
                receipt, prefix, object_artifact_prefix(session_ref, artifact_ref)
            )
        return ArtifactMetadata(
            artifact_ref,
            session_ref,
            execution_key,
            descriptor,
            _text(row, "committed_at"),
            producer.run_ref,
        )

    @staticmethod
    def _artifact_evidence(conn: sqlite3.Connection, metadata: ArtifactMetadata) -> EvidenceRecord:
        evidence_row = _one(
            conn, "SELECT * FROM dataset_evidence WHERE artifact_ref=?", (metadata.artifact_ref,)
        )
        evidence = evidence_for(metadata.descriptor)
        if (
            evidence_row is None
            or _text(evidence_row, "evidence_digest") != evidence.evidence_digest
            or _cell(evidence_row, "finding_count") != evidence.finding_count
            or _text(evidence_row, "finding_set_digest") != evidence.finding_set_digest
            or _text(evidence_row, "extractor_contract_versions_payload")
            != canonical_json(evidence.extractor_contract_versions)
        ):
            raise invalid("incomplete or inconsistent Evidence summary")
        return evidence

    def _artifact(self, conn: sqlite3.Connection, artifact_ref: str) -> ArtifactRecord | None:
        metadata = self._artifact_metadata(conn, artifact_ref)
        if metadata is None:
            return None
        evidence = self._artifact_evidence(conn, metadata)
        return ArtifactRecord(
            metadata.artifact_ref,
            metadata.session_ref,
            metadata.execution_key_digest,
            metadata.descriptor,
            metadata.committed_at,
            metadata.producing_run_ref,
            evidence,
        )

    def artifact(self, artifact_ref: str) -> ArtifactRecord | None:
        with self._read() as conn:
            return self._artifact(conn, artifact_ref)

    def lookup(self, session_ref: str, execution_key_digest: str) -> ArtifactRecord | None:
        with self._read() as conn:
            row = _one(
                conn,
                "SELECT artifact_ref FROM dataset_artifacts WHERE session_ref=? AND execution_key_digest=?",
                (session_ref, execution_key_digest),
            )
            return None if row is None else self._artifact(conn, _text(row, "artifact_ref"))

    def publish(
        self,
        run_ref: str,
        artifact_ref: str,
        descriptor: ArtifactDescriptor,
        *,
        resolved_resources: tuple[ResourceRecord, ...] = (),
        event: Callable[[str], None] | None = None,
        findings: tuple[Finding, ...] = (),
    ) -> ArtifactRecord:
        payload = encode_descriptor(descriptor)
        checked = decode_descriptor(payload)
        evidence = evidence_for(checked)
        from marivo.analysis.evidence._dataset_codec import (
            encode_finding_body,
            finding_identity,
            finding_set_digest,
        )
        from marivo.analysis.evidence._dataset_reads import _validate
        from marivo.analysis.materialization.attribution_publication import (
            finding_registration,
        )

        if (
            len(findings) != evidence.finding_count
            or finding_set_digest(findings) != evidence.finding_set_digest
        ):
            raise invalid("Finding publication differs from its complete Evidence envelope")
        with self._write() as conn:
            run = self._run(conn, run_ref)
            if run is None or run.lifecycle != "incomplete":
                raise invalid("publication requires one incomplete producer")
            now = _now()
            registration = finding_registration(checked)
            envelope = ArtifactRecord(
                artifact_ref,
                run.session_ref,
                run.execution_key_digest,
                checked,
                now,
                run_ref,
                evidence,
            )
            for item in findings:
                if registration is None:
                    raise invalid("unregistered nonzero Finding publication")
                _validate(item, envelope, registration)
            conn.execute(
                "INSERT INTO dataset_artifacts VALUES(?,?,?,?,?)",
                (artifact_ref, run.session_ref, run.execution_key_digest, payload, now),
            )
            if event is not None:
                event("insert_artifact")
            conn.execute(
                "INSERT INTO dataset_evidence VALUES(?,?,?,?,?)",
                (
                    artifact_ref,
                    evidence.evidence_digest,
                    evidence.finding_count,
                    evidence.finding_set_digest,
                    canonical_json(evidence.extractor_contract_versions),
                ),
            )
            if event is not None:
                event("insert_evidence")
            conn.executemany(
                "INSERT INTO findings VALUES(?,?,?,?,?)",
                (
                    (
                        item.finding_id,
                        artifact_ref,
                        ordinal,
                        finding_identity(item),
                        encode_finding_body(item),
                    )
                    for ordinal, item in enumerate(findings)
                ),
            )
            if event is not None:
                event("insert_findings")
            conn.execute(
                "INSERT INTO analysis_action_run_terminals VALUES(?,?,?,?,?,?)",
                (run_ref, run.session_ref, "succeeded", now, artifact_ref, None),
            )
            if event is not None:
                event("insert_terminal")
            self._delete_resources(conn, run_ref, resolved_resources)
            result = self._artifact(conn, artifact_ref)
            if result is None:
                raise invalid("newly published Artifact is absent")
            receipts = (
                result.descriptor.storage_receipt,
                *(part.storage_receipt for part in result.descriptor.retained_parts),
            )
            if any(
                owns_resource(receipt, resource)
                for resource in self._run_resources(conn, run_ref)
                for receipt in receipts
            ):
                raise invalid("committed output remains a cleanup obligation")
            if event is not None:
                event("before_commit")
        _forget_termination(resolved_resources)
        if event is not None:
            event("after_commit")
        return result
