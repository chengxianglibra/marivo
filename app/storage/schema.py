"""DDL definitions for the Factum metadata store.

All SQL uses dialect-neutral types (TEXT timestamps, no DuckDB-specific casts)
so the same DDL works across SQLite, MySQL, and PostgreSQL.
"""

METADATA_DDL: list[str] = [
    # -- Existing control-plane tables --
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id      TEXT PRIMARY KEY,
        goal            TEXT NOT NULL,
        constraints_json TEXT NOT NULL,
        budget_json     TEXT NOT NULL,
        policy_json     TEXT NOT NULL,
        status          TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS steps (
        step_id         TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        step_type       TEXT NOT NULL,
        status          TEXT NOT NULL,
        summary         TEXT NOT NULL,
        result_json     TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id     TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        step_id         TEXT NOT NULL,
        artifact_type   TEXT NOT NULL,
        name            TEXT NOT NULL,
        content_json    TEXT NOT NULL,
        lifecycle       TEXT NOT NULL DEFAULT 'committed',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observations (
        observation_id  TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        step_id         TEXT NOT NULL,
        observation_type TEXT NOT NULL,
        subject_json    TEXT NOT NULL,
        payload_json    TEXT NOT NULL,
        significance_json TEXT NOT NULL,
        quality_json    TEXT NOT NULL,
        observed_window_json TEXT,
        temporal_order  INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claims (
        claim_id        TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        claim_type      TEXT NOT NULL,
        text            TEXT NOT NULL,
        scope_json      TEXT NOT NULL,
        confidence      REAL NOT NULL,
        status          TEXT NOT NULL,
        supporting_observation_ids_json TEXT NOT NULL,
        contradicting_observation_ids_json TEXT NOT NULL,
        confidence_breakdown_json TEXT NOT NULL,
        inference_level TEXT NOT NULL DEFAULT 'L0',
        inference_justification_json TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_edges (
        edge_id         TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        from_node_id    TEXT NOT NULL,
        from_node_type  TEXT NOT NULL,
        to_node_id      TEXT NOT NULL,
        to_node_type    TEXT NOT NULL,
        -- edge_type: basic layer: supports, contradicts, justifies
        --            causal layer (M-07): correlates_with, temporally_precedes,
        --            mechanistically_explains, eliminates_alternative, experimentally_confirms
        edge_type       TEXT NOT NULL,
        weight          REAL NOT NULL,
        explanation     TEXT NOT NULL,
        match_basis_json TEXT NOT NULL DEFAULT '{}',
        score_components_json TEXT NOT NULL DEFAULT '{}',
        supporting_observation_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recommendations (
        rec_id          TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        claim_id        TEXT NOT NULL,
        action_text     TEXT NOT NULL,
        template_id     TEXT,
        priority        TEXT NOT NULL,
        expected_impact TEXT NOT NULL,
        risk            TEXT NOT NULL,
        validation_metric_json TEXT NOT NULL,
        causal_basis_json TEXT,
        entity_patch_json TEXT,
        supporting_claims_json TEXT,
        type            TEXT NOT NULL DEFAULT 'action_required',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # -- New semantic layer tables --
    """
    CREATE TABLE IF NOT EXISTS sources (
        source_id       TEXT PRIMARY KEY,
        source_type     TEXT NOT NULL,
        display_name    TEXT NOT NULL,
        connection_json TEXT NOT NULL,
        capabilities_json TEXT NOT NULL,
        sync_mode       TEXT NOT NULL DEFAULT 'all',
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_objects (
        object_id       TEXT PRIMARY KEY,
        source_id       TEXT NOT NULL REFERENCES sources(source_id),
        object_type     TEXT NOT NULL,
        parent_id       TEXT,
        native_name     TEXT NOT NULL,
        native_id       TEXT,
        fqn             TEXT NOT NULL,
        properties_json TEXT NOT NULL DEFAULT '{}',
        sync_version    TEXT,
        synced_at       TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_entities (
        entity_id       TEXT PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        display_name    TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        keys_json       TEXT NOT NULL,
        level           TEXT,
        join_constraints_json TEXT NOT NULL DEFAULT '{}',
        upstream_dependencies_json TEXT NOT NULL DEFAULT '[]',
        lineage_json    TEXT NOT NULL DEFAULT '[]',
        quality_expectations_json TEXT NOT NULL DEFAULT '{}',
        properties_json TEXT NOT NULL DEFAULT '{}',
        status          TEXT NOT NULL DEFAULT 'draft',
        revision        INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_metrics (
        metric_id       TEXT PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        display_name    TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        definition_sql  TEXT NOT NULL,
        dimensions_json TEXT NOT NULL,
        entity_id       TEXT,
        grain           TEXT,
        measure_type    TEXT,
        allowed_dimensions_json TEXT NOT NULL DEFAULT '[]',
        lineage_json    TEXT NOT NULL DEFAULT '[]',
        quality_expectations_json TEXT NOT NULL DEFAULT '{}',
        properties_json TEXT NOT NULL DEFAULT '{}',
        desired_direction TEXT,
        status          TEXT NOT NULL DEFAULT 'draft',
        revision        INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_mappings (
        mapping_id      TEXT PRIMARY KEY,
        semantic_type   TEXT NOT NULL,
        semantic_id     TEXT NOT NULL,
        object_id       TEXT NOT NULL REFERENCES source_objects(object_id),
        mapping_type    TEXT NOT NULL,
        mapping_json    TEXT NOT NULL DEFAULT '{}',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_jobs (
        job_id          TEXT PRIMARY KEY,
        source_id       TEXT NOT NULL REFERENCES sources(source_id),
        job_type        TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        started_at      TEXT,
        finished_at     TEXT,
        objects_synced  INTEGER DEFAULT 0,
        error_message   TEXT,
        created_at      TEXT NOT NULL
    )
    """,
    # -- Engine registry --
    """
    CREATE TABLE IF NOT EXISTS engines (
        engine_id         TEXT PRIMARY KEY,
        engine_type       TEXT NOT NULL,
        display_name      TEXT NOT NULL,
        connection_json   TEXT NOT NULL,
        capabilities_json TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'active',
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    )
    """,
    # -- Source-engine bindings --
    """
    CREATE TABLE IF NOT EXISTS source_engine_bindings (
        binding_id    TEXT PRIMARY KEY,
        source_id     TEXT NOT NULL REFERENCES sources(source_id),
        engine_id     TEXT NOT NULL REFERENCES engines(engine_id),
        priority      INTEGER NOT NULL DEFAULT 0,
        namespace_json TEXT NOT NULL DEFAULT '{}',
        status        TEXT NOT NULL DEFAULT 'active',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        UNIQUE(source_id, engine_id)
    )
    """,
    # -- Plans --
    """
    CREATE TABLE IF NOT EXISTS plans (
        plan_id         TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL REFERENCES sessions(session_id),
        status          TEXT NOT NULL DEFAULT 'draft',
        steps_json      TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    # -- Sync selections --
    """
    CREATE TABLE IF NOT EXISTS sync_selections (
        selection_id  TEXT PRIMARY KEY,
        source_id     TEXT NOT NULL REFERENCES sources(source_id),
        schema_name   TEXT NOT NULL,
        table_name    TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        UNIQUE(source_id, schema_name, table_name)
    )
    """,
    # -- Governance policies --
    """
    CREATE TABLE IF NOT EXISTS policies (
        policy_id       TEXT PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        policy_type     TEXT NOT NULL,
        definition_json TEXT NOT NULL,
        scope_json      TEXT NOT NULL DEFAULT '{}',
        enabled         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    # -- Quality rules --
    """
    CREATE TABLE IF NOT EXISTS quality_rules (
        rule_id         TEXT PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        rule_type       TEXT NOT NULL,
        table_name      TEXT NOT NULL,
        threshold_json  TEXT NOT NULL,
        severity        TEXT NOT NULL DEFAULT 'warn',
        enabled         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    # -- Async jobs --
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id          TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        job_type        TEXT NOT NULL,
        payload_json    TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        result_json     TEXT,
        error_message   TEXT,
        submitted_at    TEXT NOT NULL,
        started_at      TEXT,
        completed_at    TEXT
    )
    """,
    # -- Approval requests --
    """
    CREATE TABLE IF NOT EXISTS approval_requests (
        request_id      TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        rec_id          TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        reason          TEXT NOT NULL DEFAULT '',
        reviewer        TEXT NOT NULL DEFAULT '',
        submitted_at    TEXT NOT NULL,
        decided_at      TEXT
    )
    """,
    # -- Governance audit events --
    """
    CREATE TABLE IF NOT EXISTS governance_events (
        event_id        TEXT PRIMARY KEY,
        session_id      TEXT,
        subject_type    TEXT NOT NULL,
        subject_id      TEXT,
        event_type      TEXT NOT NULL,
        actor           TEXT NOT NULL DEFAULT 'system',
        detail_json     TEXT NOT NULL DEFAULT '{}',
        created_at      TEXT NOT NULL
    )
    """,
]

# Migrations that add columns to existing tables.  Each is tried
# individually — if the column already exists the ALTER will raise
# an error that is silently ignored by the caller.
METADATA_MIGRATIONS: list[str] = [
    "ALTER TABLE sources ADD COLUMN sync_mode TEXT NOT NULL DEFAULT 'all'",
    "ALTER TABLE source_engine_bindings ADD COLUMN namespace_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE steps ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE semantic_entities ADD COLUMN level TEXT",
    "ALTER TABLE semantic_entities ADD COLUMN join_constraints_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE semantic_entities ADD COLUMN upstream_dependencies_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE semantic_entities ADD COLUMN lineage_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE semantic_entities ADD COLUMN quality_expectations_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE semantic_metrics ADD COLUMN grain TEXT",
    "ALTER TABLE semantic_metrics ADD COLUMN measure_type TEXT",
    "ALTER TABLE semantic_metrics ADD COLUMN allowed_dimensions_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE semantic_metrics ADD COLUMN lineage_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE semantic_metrics ADD COLUMN quality_expectations_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE claims ADD COLUMN inference_level TEXT NOT NULL DEFAULT 'L0'",
    "ALTER TABLE claims ADD COLUMN inference_justification_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE sessions ADD COLUMN raw_filter TEXT",
    "ALTER TABLE observations ADD COLUMN observed_window_json TEXT",
    "ALTER TABLE observations ADD COLUMN temporal_order INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE recommendations ADD COLUMN causal_basis_json TEXT",
    "ALTER TABLE recommendations ADD COLUMN template_id TEXT",
    "ALTER TABLE evidence_edges ADD COLUMN match_basis_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE evidence_edges ADD COLUMN score_components_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE evidence_edges ADD COLUMN supporting_observation_ids_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE artifacts ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'committed'",
]
