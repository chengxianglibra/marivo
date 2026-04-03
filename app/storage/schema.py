"""DDL definitions for the Factum metadata store.

All SQL uses dialect-neutral types (TEXT timestamps, no DuckDB-specific casts)
so the same DDL works across SQLite, MySQL, and PostgreSQL.
"""

METADATA_DDL: list[str] = [
    # -- Existing control-plane tables --
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id               TEXT PRIMARY KEY,
        goal                     TEXT NOT NULL,
        constraints_json         TEXT NOT NULL,
        budget_json              TEXT NOT NULL,
        policy_json              TEXT NOT NULL,
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
    CREATE TABLE IF NOT EXISTS steps (
        step_id         TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL,
        step_type       TEXT NOT NULL,
        status          TEXT NOT NULL,
        summary         TEXT NOT NULL,
        result_json     TEXT NOT NULL,
        provenance_json TEXT NOT NULL DEFAULT '{}',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
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
    # -------------------------------------------------------------------------
    # Canonical Evidence Pipeline (Phase 4)
    # artifact -> finding -> proposition -> assessment -> action proposal
    # -------------------------------------------------------------------------
    # -- findings: canonical fact units extracted from artifacts --
    """
    CREATE TABLE IF NOT EXISTS findings (
        finding_id          TEXT PRIMARY KEY,
        session_id          TEXT NOT NULL REFERENCES sessions(session_id),  -- denorm: session_id also lives in step_ref_json; kept for efficient indexed queries
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
    "CREATE INDEX IF NOT EXISTS idx_findings_session_type ON findings(session_id, finding_type)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_artifact_type_key ON findings(artifact_id, finding_type, canonical_item_key)",
    # -- propositions: judgment-layer canonical objects --
    """
    CREATE TABLE IF NOT EXISTS propositions (
        proposition_id          TEXT PRIMARY KEY,
        session_id              TEXT NOT NULL REFERENCES sessions(session_id),
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
    "CREATE INDEX IF NOT EXISTS idx_propositions_session_type ON propositions(session_id, proposition_type)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_propositions_session_type_identity ON propositions(session_id, proposition_type, identity_key) WHERE identity_key != ''",
    # -- assessments: immutable evaluation snapshots --
    """
    CREATE TABLE IF NOT EXISTS assessments (
        assessment_id                   TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL REFERENCES sessions(session_id),
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
    "CREATE INDEX IF NOT EXISTS idx_assessments_proposition ON assessments(proposition_id)",
    "CREATE INDEX IF NOT EXISTS idx_assessments_proposition_seq ON assessments(proposition_id, snapshot_seq)",
    # -- evidence_gaps: missing-evidence tracking per proposition --
    """
    CREATE TABLE IF NOT EXISTS evidence_gaps (
        gap_id                          TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL REFERENCES sessions(session_id),
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
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition ON evidence_gaps(proposition_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition_status ON evidence_gaps(proposition_id, status)",
    # -- inference_records: rule-process records per assessment snapshot --
    """
    CREATE TABLE IF NOT EXISTS inference_records (
        inference_record_id             TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL REFERENCES sessions(session_id),
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
    "CREATE INDEX IF NOT EXISTS idx_inference_records_assessment ON inference_records(assessment_id)",
    # -- action_proposals: planning shortcut snapshots --
    """
    CREATE TABLE IF NOT EXISTS action_proposals (
        action_proposal_id              TEXT PRIMARY KEY,
        session_id                      TEXT NOT NULL REFERENCES sessions(session_id),
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
    "CREATE INDEX IF NOT EXISTS idx_action_proposals_session_rank ON action_proposals(session_id, priority_rank)",
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
]
