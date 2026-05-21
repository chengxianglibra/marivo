"""DDL definitions for the Marivo metadata store."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

METADATA_SCHEMA_VERSION = "metadata.decomposition_semantics.v4"
METADATA_SCHEMA_MARKER_TABLE = "metadata_schema_marker"

METADATA_DDL: list[str] = [
    # -- Existing control-plane tables --
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id               TEXT PRIMARY KEY,
        goal                     TEXT NOT NULL,
        constraints_json         TEXT NOT NULL,
        budget_json              TEXT NOT NULL,
        owner_user              TEXT NOT NULL DEFAULT '',
        status                   TEXT NOT NULL,
        raw_filter               TEXT,
        terminal_reason          TEXT,
        ended_at                 TEXT,
        rollover_from_session_id TEXT,
        created_at               TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_events (
        event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT NOT NULL,
        seq          INTEGER NOT NULL,
        event_type   TEXT NOT NULL,
        timestamp    TEXT NOT NULL,
        actor        TEXT,
        payload_json TEXT NOT NULL,
        UNIQUE(session_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_session_events_sid ON session_events(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_session_events_owner ON session_events(event_type, actor)",
    """
    CREATE TABLE IF NOT EXISTS steps (
        step_id         TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        step_type       TEXT NOT NULL,
        status          TEXT NOT NULL,
        summary         TEXT NOT NULL,
        result_json     TEXT NOT NULL,
        provenance_json TEXT NOT NULL DEFAULT '{}',
        reasoning       TEXT,
        sql_texts       TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS step_metadata (
        step_id                TEXT PRIMARY KEY REFERENCES steps(step_id) ON DELETE CASCADE,
        metadata_kind          TEXT NOT NULL,
        semantic_snapshot_json TEXT NOT NULL,
        created_at             TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id             TEXT PRIMARY KEY,
        session_id              TEXT NOT NULL,
        step_id                 TEXT NOT NULL,
        artifact_type           TEXT NOT NULL,
        name                    TEXT NOT NULL,
        content_json            TEXT NOT NULL,
        lifecycle               TEXT NOT NULL DEFAULT 'committed',
        artifact_schema_version TEXT,
        created_at              TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC, session_id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_status_created ON sessions(status, created_at DESC, session_id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_steps_session_type_created ON steps(session_id, step_type, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_session_step_lifecycle_created ON artifacts(session_id, step_id, lifecycle, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_session_type_created ON artifacts(session_id, artifact_type, created_at DESC)",
    # -- New semantic layer tables --
    """
    CREATE TABLE IF NOT EXISTS datasources (
        datasource_id   TEXT PRIMARY KEY,
        datasource_type TEXT NOT NULL,
        display_name    TEXT NOT NULL,
        connection_json TEXT NOT NULL DEFAULT '{}',
        owner_user      TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_datasources_owner ON datasources(owner_user)",
    # -------------------------------------------------------------------------
    # OSI-aligned semantic layer tables (v2)
    # -------------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS semantic_models (
        model_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT,
        ai_context  TEXT,
        visibility  TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
        owner_user  TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_models_visibility_owner ON semantic_models(visibility, owner_user)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_models_name_visibility_owner ON semantic_models(name, visibility, owner_user)",
    """
    CREATE TABLE IF NOT EXISTS semantic_datasets (
        dataset_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id       INTEGER NOT NULL REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        name           TEXT NOT NULL,
        source         TEXT NOT NULL,
        primary_key    TEXT,
        unique_keys    TEXT,
        description    TEXT,
        ai_context     TEXT,
        datasource_id  TEXT,
        created_at     TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(model_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_fields (
        field_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id   INTEGER NOT NULL REFERENCES semantic_datasets(dataset_id) ON DELETE CASCADE,
        name         TEXT NOT NULL,
        expression   TEXT NOT NULL,
        is_time      INTEGER NOT NULL DEFAULT 0,
        is_dimension INTEGER NOT NULL DEFAULT 0,
        label        TEXT,
        description  TEXT,
        ai_context   TEXT,
        data_type    TEXT CHECK (data_type IS NULL OR data_type IN ('date', 'timestamp', 'string', 'integer')),
        format       TEXT,
        required_prefix TEXT,
        support_min_granularity TEXT CHECK (
            support_min_granularity IS NULL OR support_min_granularity IN (
                'hour', 'day', 'week', 'month', 'quarter', 'year'
            )
        ),
        position     INTEGER NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(dataset_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_relationships (
        relationship_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id         INTEGER NOT NULL REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        name             TEXT NOT NULL,
        from_dataset     TEXT NOT NULL,
        to_dataset       TEXT NOT NULL,
        from_columns     TEXT NOT NULL,
        to_columns       TEXT NOT NULL,
        ai_context       TEXT,
        cardinality      TEXT,
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(model_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_metrics (
        metric_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id            INTEGER NOT NULL REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        name                TEXT NOT NULL,
        expression          TEXT NOT NULL,
        description         TEXT,
        ai_context          TEXT,
        numerator           TEXT,
        denominator         TEXT,
        weight              TEXT,
        decomposition_semantics TEXT NOT NULL DEFAULT 'sum',
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(model_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_readiness_status (
        model_id    INTEGER PRIMARY KEY REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        status      TEXT NOT NULL CHECK (status IN ('ready', 'not_ready')),
        blockers    TEXT,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # -- Engine registry --
    # -- Plans --
    """
    CREATE TABLE IF NOT EXISTS plans (
        plan_id         TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'draft',
        steps_json      TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    # -------------------------------------------------------------------------
    # Canonical Evidence Pipeline (Phase 4)
    # artifact -> finding -> proposition -> assessment -> action proposal
    # -------------------------------------------------------------------------
    # -- findings: canonical fact units extracted from artifacts --
    """
    CREATE TABLE IF NOT EXISTS findings (
        finding_id          TEXT PRIMARY KEY,
        session_id          TEXT NOT NULL,  -- denorm: session_id also lives in step_ref_json; kept for efficient indexed queries
        artifact_id         TEXT NOT NULL REFERENCES artifacts(artifact_id),
        step_ref_json       TEXT NOT NULL,
        finding_type        TEXT NOT NULL,
        canonical_item_key  TEXT NOT NULL DEFAULT '',  -- stable key for replay/idempotency; part of finding_id inputs
        subject_json        TEXT NOT NULL,
        observed_window_json TEXT,
        quality_json        TEXT NOT NULL,
        provenance_json     TEXT NOT NULL,
        payload_json        TEXT NOT NULL,
        schema_version      TEXT NOT NULL DEFAULT 'v1',
        invalidated_at      TEXT,
        invalidation_reason TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_findings_session ON findings(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_findings_artifact ON findings(artifact_id)",
    "CREATE INDEX IF NOT EXISTS idx_findings_artifact_created ON findings(artifact_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_findings_session_type ON findings(session_id, finding_type)",
    "CREATE INDEX IF NOT EXISTS idx_findings_session_created ON findings(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_findings_session_type_created ON findings(session_id, finding_type, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_artifact_type_key ON findings(artifact_id, finding_type, canonical_item_key)",
    # -- propositions: judgment-layer canonical objects --
    """
    CREATE TABLE IF NOT EXISTS propositions (
        proposition_id          TEXT PRIMARY KEY,
        session_id              TEXT NOT NULL,
        proposition_type        TEXT NOT NULL,
        subject_json            TEXT NOT NULL,
        origin_json             TEXT NOT NULL,
        assessment_anchor_json  TEXT NOT NULL,
        lineage_json            TEXT NOT NULL,
        seed_finding_refs_json           TEXT NOT NULL DEFAULT '[]',
        payload_json                     TEXT NOT NULL DEFAULT '{}',  -- subtype payload extension; not in PropositionBase contract
        schema_version                   TEXT NOT NULL DEFAULT 'v1',
        identity_key                     TEXT NOT NULL DEFAULT '',
        externally_visible_assessment_id TEXT,
        invalidated_at                   TEXT,
        invalidation_reason              TEXT,
        created_at                       TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_propositions_session ON propositions(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_propositions_session_created ON propositions(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_propositions_session_type ON propositions(session_id, proposition_type)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_propositions_session_type_identity ON propositions(session_id, proposition_type, identity_key) WHERE identity_key != ''",
    # -- assessments: immutable evaluation snapshots --
    """
    CREATE TABLE IF NOT EXISTS assessments (
        assessment_id                   TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL,
        proposition_id                  TEXT NOT NULL REFERENCES propositions(proposition_id),
        assessment_type                 TEXT NOT NULL,
        snapshot_seq                    INTEGER NOT NULL,
        status                          TEXT NOT NULL,
        confidence_grade                TEXT NOT NULL,
        confidence_rationale_json       TEXT NOT NULL,
        supporting_finding_ids_json     TEXT NOT NULL DEFAULT '[]',
        opposing_finding_ids_json       TEXT NOT NULL DEFAULT '[]',
        gap_memberships_json            TEXT NOT NULL DEFAULT '[]',
        applied_inference_record_ids_json TEXT NOT NULL DEFAULT '[]',
        supersedes_assessment_id        TEXT REFERENCES assessments(assessment_id),  -- nullable self-ref: previous snapshot this supersedes
        payload_json                    TEXT NOT NULL DEFAULT '{}',  -- subtype payload extension; not in AssessmentBase contract
        schema_version                  TEXT NOT NULL DEFAULT 'v1',
        created_at                      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(proposition_id, snapshot_seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_assessments_session ON assessments(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_assessments_session_created ON assessments(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_assessments_proposition ON assessments(proposition_id)",
    "CREATE INDEX IF NOT EXISTS idx_assessments_proposition_seq ON assessments(proposition_id, snapshot_seq)",
    # -- evidence_gaps: missing-evidence tracking per proposition --
    """
    CREATE TABLE IF NOT EXISTS evidence_gaps (
        gap_id                          TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL,
        proposition_id                  TEXT NOT NULL REFERENCES propositions(proposition_id),
        gap_kind                        TEXT NOT NULL,
        title                           TEXT NOT NULL DEFAULT '',
        description                     TEXT NOT NULL DEFAULT '',
        status                          TEXT NOT NULL DEFAULT 'open',
        missing_requirement_json        TEXT NOT NULL,
        satisfiable_by_json             TEXT NOT NULL DEFAULT '[]',
        related_finding_ids_json        TEXT NOT NULL DEFAULT '[]',
        opened_by_inference_record_id   TEXT NOT NULL REFERENCES inference_records(inference_record_id),
        resolved_by_inference_record_id TEXT         REFERENCES inference_records(inference_record_id),
        schema_version                  TEXT NOT NULL DEFAULT 'v1',
        created_at                      TEXT NOT NULL DEFAULT (datetime('now')),
        resolved_at                     TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_session ON evidence_gaps(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_session_status_created ON evidence_gaps(session_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition ON evidence_gaps(proposition_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition_status ON evidence_gaps(proposition_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition_status_created ON evidence_gaps(proposition_id, status, created_at)",
    # -- inference_records: rule-process records per assessment snapshot --
    """
    CREATE TABLE IF NOT EXISTS inference_records (
        inference_record_id             TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL,
        proposition_id                  TEXT NOT NULL REFERENCES propositions(proposition_id),
        assessment_id                   TEXT NOT NULL REFERENCES assessments(assessment_id),
        rule_id                         TEXT NOT NULL,
        rule_version                    TEXT NOT NULL DEFAULT 'v1',
        result                          TEXT NOT NULL,
        input_finding_ids_json          TEXT NOT NULL DEFAULT '[]',
        input_assessment_ids_json       TEXT NOT NULL DEFAULT '[]',
        opened_gap_ids_json             TEXT NOT NULL DEFAULT '[]',
        resolved_gap_ids_json           TEXT NOT NULL DEFAULT '[]',
        produced_status_transition_json TEXT,
        confidence_contribution_json    TEXT NOT NULL DEFAULT '{}',
        justification_json              TEXT NOT NULL DEFAULT '{}',
        schema_version                  TEXT NOT NULL DEFAULT 'v1',
        created_at                      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_inference_records_session ON inference_records(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_inference_records_proposition ON inference_records(proposition_id)",
    "CREATE INDEX IF NOT EXISTS idx_inference_records_proposition_created ON inference_records(proposition_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_inference_records_assessment ON inference_records(assessment_id)",
    "CREATE INDEX IF NOT EXISTS idx_inference_records_assessment_created ON inference_records(assessment_id, created_at)",
    # -- action_proposals: planning shortcut snapshots --
    """
    CREATE TABLE IF NOT EXISTS action_proposals (
        action_proposal_id              TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL,
        action_kind                     TEXT NOT NULL,
        primary_assessment_ref_json     TEXT NOT NULL,
        related_assessment_refs_json    TEXT NOT NULL DEFAULT '[]',
        target_proposition_ref_json     TEXT NOT NULL,
        proposal_context_json           TEXT NOT NULL,
        priority_axes_json              TEXT NOT NULL,
        priority_rank                   REAL NOT NULL,
        rationale_json                  TEXT NOT NULL,
        payload_json                    TEXT NOT NULL,
        policy_version                  TEXT NOT NULL DEFAULT 'v1',
        schema_version                  TEXT NOT NULL DEFAULT 'v1',
        created_at                      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_action_proposals_session ON action_proposals(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_action_proposals_session_kind ON action_proposals(session_id, action_kind)",
    "CREATE INDEX IF NOT EXISTS idx_action_proposals_session_kind_rank_created ON action_proposals(session_id, action_kind, priority_rank, created_at, action_proposal_id)",
    "CREATE INDEX IF NOT EXISTS idx_action_proposals_session_rank ON action_proposals(session_id, priority_rank)",
    "CREATE INDEX IF NOT EXISTS idx_action_proposals_session_rank_created ON action_proposals(session_id, priority_rank, created_at, action_proposal_id)",
    # -- proposition_seed_finding_refs: junction table for finding → proposition seed lookups --
    # Invariant (Phase 4a-3): seed refs are creation-time only; runtime evidence membership
    # (supporting / opposing findings) lives exclusively in assessment snapshots, never here.
    # Design split (two authoritative surfaces):
    #   propositions.seed_finding_refs_json — written at creation time (PropositionRepository.create);
    #     authoritative for single-object reads of the proposition's original seed set.
    #   proposition_seed_finding_refs (this table) — the live index; use for reverse lookups
    #     ("which propositions were seeded by finding X?") and for seeding-run tracking (Phase 4e).
    # These two are NOT kept in sync after creation.  Call PropositionRepository.add_seed_finding_refs
    # separately to populate this table; it does NOT modify seed_finding_refs_json.
    """
    CREATE TABLE IF NOT EXISTS proposition_seed_finding_refs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        proposition_id  TEXT NOT NULL REFERENCES propositions(proposition_id),
        finding_id      TEXT NOT NULL REFERENCES findings(finding_id),
        role            TEXT NOT NULL DEFAULT 'primary',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(proposition_id, finding_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prop_seed_refs_proposition ON proposition_seed_finding_refs(proposition_id)",
    "CREATE INDEX IF NOT EXISTS idx_prop_seed_refs_finding ON proposition_seed_finding_refs(finding_id)",
    """
    CREATE TABLE IF NOT EXISTS calendar (
        calendar_date              TEXT NOT NULL,
        holiday_group_id           TEXT NOT NULL DEFAULT '',
        day_kind                   TEXT NOT NULL CHECK (day_kind IN ('holiday', 'adjusted_workday')),
        holiday_name               TEXT,
        year_relative_holiday_key  TEXT,
        PRIMARY KEY (calendar_date, day_kind, holiday_group_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar(calendar_date)",
    """
    CREATE TABLE IF NOT EXISTS metadata_schema_marker (
        backend         TEXT NOT NULL PRIMARY KEY CHECK (backend IN ('sqlite', 'mysql')),
        schema_version  TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        ddl_fingerprint TEXT NOT NULL
    )
    """,
]

MetadataBackend = Literal["sqlite", "mysql"]
MetadataSchemaState = Literal["empty", "current", "invalid"]


@dataclass(frozen=True)
class MetadataSchemaCheck:
    state: MetadataSchemaState
    reason: str


def metadata_ddl_for_backend(backend: MetadataBackend) -> list[str]:
    """Return executable metadata DDL for a backend."""

    if backend == "sqlite":
        return list(METADATA_DDL)
    if backend == "mysql":
        return list(MYSQL_METADATA_DDL)
    raise ValueError(f"Unsupported metadata backend: {backend}")


def metadata_ddl_fingerprint(backend: MetadataBackend) -> str:
    ddl = "\n".join(_normalize_ddl(stmt) for stmt in metadata_ddl_for_backend(backend))
    return hashlib.sha256(ddl.encode("utf-8")).hexdigest()


def metadata_schema_marker_row(backend: MetadataBackend) -> dict[str, str]:
    return {
        "backend": backend,
        "schema_version": METADATA_SCHEMA_VERSION,
        "ddl_fingerprint": metadata_ddl_fingerprint(backend),
    }


def expected_metadata_tables(backend: MetadataBackend) -> set[str]:
    return {
        table
        for stmt in metadata_ddl_for_backend(backend)
        if (table := _table_name(stmt)) is not None
    }


def evaluate_metadata_schema_state(
    backend: MetadataBackend,
    table_names: set[str],
    marker_row: dict[str, Any] | None,
) -> MetadataSchemaCheck:
    """Classify a fresh-init metadata schema without mutating it."""

    expected_tables = expected_metadata_tables(backend)
    if not table_names:
        return MetadataSchemaCheck("empty", "schema is empty")

    unknown_tables = table_names - expected_tables
    if unknown_tables:
        return MetadataSchemaCheck(
            "invalid", f"unknown tables: {', '.join(sorted(unknown_tables))}"
        )

    if METADATA_SCHEMA_MARKER_TABLE not in table_names:
        return MetadataSchemaCheck("invalid", "metadata schema marker table is missing")

    missing_tables = expected_tables - table_names
    if missing_tables:
        return MetadataSchemaCheck(
            "invalid", f"missing tables: {', '.join(sorted(missing_tables))}"
        )

    if marker_row is None:
        return MetadataSchemaCheck("invalid", "metadata schema marker row is missing")

    expected_marker = metadata_schema_marker_row(backend)
    for key, expected_value in expected_marker.items():
        if marker_row.get(key) != expected_value:
            return MetadataSchemaCheck("invalid", f"metadata schema marker {key} mismatch")

    return MetadataSchemaCheck("current", "metadata schema matches current fresh-init contract")


def _normalize_ddl(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).lower()


def _table_name(sql: str) -> str | None:
    match = re.match(r"\s*CREATE TABLE IF NOT EXISTS\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql)
    return match.group(1) if match else None


def _mysql_metadata_ddl() -> list[str]:
    ddl: list[str] = []
    foreign_keys: list[str] = []
    indexed_columns = _mysql_indexed_columns()
    for statement in METADATA_DDL:
        stripped = statement.strip()
        if stripped.startswith("CREATE TRIGGER"):
            continue
        if stripped.startswith("CREATE TABLE"):
            table_name = _table_name(stripped)
            table_sql, table_foreign_keys = _mysql_table_ddl(
                stripped,
                indexed_columns.get(table_name or "", set()),
            )
            ddl.append(table_sql)
            foreign_keys.extend(table_foreign_keys)
        elif "WHERE identity_key != ''" in stripped:
            ddl.append(
                "CREATE UNIQUE INDEX idx_propositions_session_type_identity "
                "ON propositions(session_id, proposition_type, identity_key_unique)"
            )
        elif stripped.startswith("CREATE INDEX") or stripped.startswith("CREATE UNIQUE INDEX"):
            ddl.append(stripped.replace(" IF NOT EXISTS", ""))
        else:
            ddl.append(stripped)
    ddl.extend(foreign_keys)
    return ddl


def _mysql_table_ddl(sql: str, indexed_columns: set[str]) -> tuple[str, list[str]]:
    lines = sql.splitlines()
    converted: list[str] = []
    foreign_keys: list[str] = []
    table_name = _table_name(sql)
    for line in lines:
        converted_lines, line_foreign_keys = _mysql_table_line(
            line,
            table_name or "",
            indexed_columns,
        )
        converted.extend(converted_lines)
        foreign_keys.extend(line_foreign_keys)
    table_sql = "\n".join(converted)
    table_sql = table_sql.replace("DEFAULT (datetime('now'))", "DEFAULT CURRENT_TIMESTAMP(6)")
    table_sql = table_sql.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT",
        "BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY",
    )
    table_sql = re.sub(r"\bINTEGER\b", "INT", table_sql)
    table_sql = re.sub(r"\bREAL\b", "DOUBLE", table_sql)
    table_sql = re.sub(r"\bsubstr\(", "SUBSTR(", table_sql)
    return (
        f"{table_sql}\nENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
        foreign_keys,
    )


def _mysql_table_line(
    line: str, table_name: str, indexed_columns: set[str]
) -> tuple[list[str], list[str]]:
    stripped = line.strip()
    column_match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+TEXT\b(.*)", stripped)
    if column_match is None:
        # Also handle INTEGER (or INT) columns with inline REFERENCES
        int_column_match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:INTEGER|INT)\b(.*)", stripped)
        if int_column_match is not None:
            col_name, col_suffix = int_column_match.groups()
            int_reference_match = re.search(
                r"\s+REFERENCES\s+([a-zA-Z_][a-zA-Z0-9_]*)\(([^)]+)\)(\s+ON DELETE CASCADE)?",
                col_suffix,
            )
            if int_reference_match is not None:
                ref_table, ref_column, on_delete = int_reference_match.groups()
                constraint_name = f"fk_{table_name}_{col_name}"
                fk = (
                    f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
                    f"FOREIGN KEY ({col_name}) "
                    f"REFERENCES {ref_table}({ref_column})"
                )
                if on_delete:
                    fk = f"{fk}{on_delete}"
                int_foreign_keys = [fk]
                new_suffix = f"{col_suffix[: int_reference_match.start()]}{col_suffix[int_reference_match.end() :]}"
                leading = line[: len(line) - len(line.lstrip())]
                converted_line = f"{leading}{col_name}                            INT{new_suffix}"
                return [converted_line], int_foreign_keys
        return [line], []

    column_name, suffix = column_match.groups()
    foreign_keys: list[str] = []
    reference_match = re.search(
        r"\s+REFERENCES\s+([a-zA-Z_][a-zA-Z0-9_]*)\(([^)]+)\)(\s+ON DELETE CASCADE)?",
        suffix,
    )
    if reference_match is not None:
        ref_table, ref_column, on_delete = reference_match.groups()
        constraint_name = f"fk_{table_name}_{column_name}"
        foreign_key = (
            f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
            f"FOREIGN KEY ({column_name}) "
            f"REFERENCES {ref_table}({ref_column})"
        )
        if on_delete:
            foreign_key = f"{foreign_key}{on_delete}"
        foreign_keys.append(foreign_key)
        suffix = f"{suffix[: reference_match.start()]}{suffix[reference_match.end() :]}"

    mysql_type = _mysql_text_type(column_name, suffix, indexed_columns)
    if mysql_type in {"TEXT", "LONGTEXT"}:
        suffix = _strip_mysql_large_text_default(suffix)
    leading = line[: len(line) - len(line.lstrip())]
    converted = f"{leading}{column_name:<31} {mysql_type}{suffix}"
    if column_name == "identity_key":
        comma = "," if stripped.endswith(",") else ""
        generated = (
            f"{leading}identity_key_unique             {_MYSQL_KEY_TEXT_TYPE} "
            f"GENERATED ALWAYS AS (NULLIF(identity_key, '')) STORED{comma}"
        )
        return [converted, generated], foreign_keys
    return [converted], foreign_keys


def _strip_mysql_large_text_default(suffix: str) -> str:
    return re.sub(r"\s+DEFAULT\s+(?:'[^']*'|\"[^\"]*\")", "", suffix)


_MYSQL_KEY_TEXT_TYPE = "VARCHAR(128)"


def _mysql_text_type(column_name: str, suffix: str, indexed_columns: set[str]) -> str:
    if "PRIMARY KEY" in suffix or "REFERENCES" in suffix or "UNIQUE" in suffix:
        return _MYSQL_KEY_TEXT_TYPE
    if column_name.endswith("_json") or column_name in {
        "content_json",
        "definition_json",
        "payload_json",
        "detail_json",
        "result_json",
        "steps_json",
        "connection_json",
        "auth_json",
    }:
        return "LONGTEXT"
    if column_name.endswith("_id") or column_name.endswith("_ref") or column_name.endswith("_key"):
        return _MYSQL_KEY_TEXT_TYPE
    if column_name in {
        "created_at",
        "updated_at",
        "ended_at",
        "submitted_at",
        "started_at",
        "completed_at",
        "finished_at",
        "decided_at",
        "synced_at",
        "resolved_at",
        "invalidated_at",
        "snapshot_frozen_at",
        "frozen_at",
    }:
        return "DATETIME(6)"
    if column_name in indexed_columns:
        return _MYSQL_KEY_TEXT_TYPE
    if column_name in {
        "status",
        "datasource_type",
        "object_type",
        "job_type",
        "step_type",
        "artifact_type",
        "finding_type",
        "proposition_type",
        "assessment_type",
        "action_kind",
        "decomposition_semantics",
        "fqn",
        "lifecycle",
        "native_name",
        "schema_version",
        "sync_version",
        "backend",
        "ddl_fingerprint",
    }:
        return _MYSQL_KEY_TEXT_TYPE
    return "TEXT"


def _mysql_indexed_columns() -> dict[str, set[str]]:
    indexed_columns: dict[str, set[str]] = {}
    current_table: str | None = None
    for statement in METADATA_DDL:
        stripped = statement.strip()
        table_name = _table_name(stripped)
        if table_name is not None:
            current_table = table_name
            indexed_columns.setdefault(current_table, set())
            for line in stripped.splitlines():
                line_stripped = line.strip()
                for prefix in ("UNIQUE", "PRIMARY KEY"):
                    if re.match(rf"{prefix}\s*\(", line_stripped):
                        indexed_columns[current_table].update(
                            _column_list(
                                line_stripped[
                                    line_stripped.find("(") + 1 : line_stripped.rfind(")")
                                ]
                            )
                        )
            continue

        index_match = re.match(
            r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+ ON ([a-zA-Z_][a-zA-Z0-9_]*)\((.*?)\)",
            stripped,
            re.S,
        )
        if index_match is not None:
            indexed_columns.setdefault(index_match.group(1), set()).update(
                _column_list(index_match.group(2))
            )
    return indexed_columns


def _column_list(columns_sql: str) -> list[str]:
    return [part.strip().split()[0] for part in columns_sql.split(",") if part.strip()]


MYSQL_METADATA_DDL: list[str] = _mysql_metadata_ddl()
