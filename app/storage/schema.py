"""DDL definitions for the Marivo metadata store."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

METADATA_SCHEMA_VERSION = "metadata.osi_v2_additive.v1"
METADATA_SCHEMA_MARKER_TABLE = "metadata_schema_marker"

METADATA_DDL: list[str] = [
    # -- Existing control-plane tables --
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id               TEXT PRIMARY KEY,
        goal                     TEXT NOT NULL,
        constraints_json         TEXT NOT NULL,
        budget_json              TEXT NOT NULL,
        policy_json              TEXT NOT NULL,
        execution_identity_json  TEXT NOT NULL DEFAULT '{}',
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
        policy_json     TEXT NOT NULL DEFAULT '{}',
        status          TEXT NOT NULL DEFAULT 'active',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    # -------------------------------------------------------------------------
    # OSI-aligned semantic layer tables (v2)
    # Per-model revision replaces global semantic_versions.
    # Destructive DDL: DROP before CREATE for changed schemas.
    # -------------------------------------------------------------------------
    "DROP TABLE IF EXISTS semantic_versions",
    "DROP TABLE IF EXISTS semantic_readiness_status",
    "DROP TABLE IF EXISTS semantic_models",
    """
    CREATE TABLE semantic_models (
        model_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT,
        ai_context  TEXT,
        visibility  TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
        owner_user  TEXT,
        revision    INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_models_visibility_owner ON semantic_models(visibility, owner_user)",
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
        label        TEXT,
        description  TEXT,
        ai_context   TEXT,
        data_type    TEXT,
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
        observed_dataset    TEXT,
        observation_grain   TEXT,
        primary_time_field  TEXT,
        additivity          TEXT,
        filters             TEXT,
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
    """
    CREATE TABLE IF NOT EXISTS semantic_entity_contracts (
        entity_contract_id      TEXT PRIMARY KEY,
        entity_ref              TEXT NOT NULL UNIQUE,
        display_name            TEXT NOT NULL,
        description             TEXT NOT NULL DEFAULT '',
        properties_json         TEXT NOT NULL DEFAULT '{}',
        catalog_metadata_json   TEXT NOT NULL DEFAULT '{}',
        entity_contract_version TEXT NOT NULL,
        entity_kind             TEXT NOT NULL DEFAULT 'business_entity' CHECK (
            entity_kind IN (
                'business_entity',
                'event_entity',
                'fact_entity',
                'snapshot_entity',
                'derived_entity'
            )
        ),
        uniqueness_scope        TEXT NOT NULL CHECK (
            uniqueness_scope IN ('global', 'parent_scoped')
        ),
        id_stability            TEXT NOT NULL CHECK (
            id_stability IN ('stable', 'reassignable', 'ephemeral')
        ),
        nullable_key_policy     TEXT NOT NULL DEFAULT 'reject' CHECK (
            nullable_key_policy IN ('reject', 'allow_partial')
        ),
        parent_entity_ref       TEXT,
        cardinality_to_parent   TEXT,
        ownership_semantics     TEXT,
        primary_time_ref        TEXT,
        fields_json             TEXT NOT NULL DEFAULT '[]',
        binding_json            TEXT,
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK (substr(entity_ref, 1, 7) = 'entity.'),
        CHECK (
            parent_entity_ref IS NULL
            OR substr(parent_entity_ref, 1, 7) = 'entity.'
        ),
        CHECK (primary_time_ref IS NULL OR substr(primary_time_ref, 1, 5) = 'time.'),
        CHECK (
            parent_entity_ref IS NULL
            OR (
                cardinality_to_parent IS NOT NULL
                AND cardinality_to_parent IN ('one_to_one', 'many_to_one')
            )
        ),
        CHECK (
            ownership_semantics IS NULL
            OR ownership_semantics IN ('belongs_to', 'contains', 'derives_from')
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_entity_key_refs (
        entity_contract_id  TEXT NOT NULL REFERENCES semantic_entity_contracts(entity_contract_id) ON DELETE CASCADE,
        position            INTEGER NOT NULL,
        key_ref             TEXT NOT NULL,
        description         TEXT,
        PRIMARY KEY (entity_contract_id, position),
        UNIQUE(entity_contract_id, key_ref),
        CHECK (position > 0),
        CHECK (substr(key_ref, 1, 4) = 'key.')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_entity_stable_descriptors (
        entity_contract_id  TEXT NOT NULL REFERENCES semantic_entity_contracts(entity_contract_id) ON DELETE CASCADE,
        position            INTEGER NOT NULL,
        dimension_ref       TEXT NOT NULL,
        cardinality         TEXT,
        PRIMARY KEY (entity_contract_id, position),
        UNIQUE(entity_contract_id, dimension_ref),
        CHECK (position > 0),
        CHECK (substr(dimension_ref, 1, 10) = 'dimension.'),
        CHECK (cardinality IS NULL OR cardinality IN ('one', 'many'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_metric_contracts (
        metric_contract_id      TEXT PRIMARY KEY,
        metric_ref              TEXT NOT NULL,
        display_name            TEXT NOT NULL,
        description             TEXT NOT NULL DEFAULT '',
        metric_family           TEXT NOT NULL CHECK (
            metric_family IN (
                'count_metric',
                'sum_metric',
                'rate_metric',
                'average_metric',
                'distribution_metric',
                'score_metric',
                'survival_metric'
            )
        ),
        population_subject_ref  TEXT,
        observed_entity_ref     TEXT NOT NULL,
        observation_grain_ref   TEXT NOT NULL,
        sample_kind             TEXT NOT NULL CHECK (
            sample_kind IN ('numeric', 'rate', 'binary', 'survival')
        ),
        value_semantics         TEXT NOT NULL CHECK (
            value_semantics IN (
                'count',
                'sum',
                'ratio',
                'mean',
                'distribution_statistic',
                'score',
                'survival_probability'
            )
        ),
        aggregation_scope       TEXT,
        primary_time_ref        TEXT,
        additivity_constraints_json  TEXT NOT NULL DEFAULT '{}',
        default_predicate_refs_json TEXT NOT NULL DEFAULT '[]',
        metric_contract_version  TEXT NOT NULL,
        family_payload_json      TEXT NOT NULL DEFAULT '{}',
        catalog_metadata_json    TEXT NOT NULL DEFAULT '{}',
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        base_revision           INTEGER CHECK (base_revision IS NULL OR base_revision >= 1),
        change_summary          TEXT NOT NULL DEFAULT '',
        revision_compatibility  TEXT NOT NULL DEFAULT 'compatible' CHECK (
            revision_compatibility IN ('compatible', 'breaking')
        ),
        is_latest_active        INTEGER NOT NULL DEFAULT 0 CHECK (is_latest_active IN (0, 1)),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        UNIQUE(metric_ref, revision),
        CHECK (substr(metric_ref, 1, 7) = 'metric.'),
        CHECK (
            population_subject_ref IS NULL
            OR substr(population_subject_ref, 1, 8) = 'subject.'
        ),
        CHECK (substr(observed_entity_ref, 1, 7) = 'entity.'),
        CHECK (substr(observation_grain_ref, 1, 6) = 'grain.'),
        CHECK (primary_time_ref IS NULL OR substr(primary_time_ref, 1, 5) = 'time.'),
        CHECK (
            aggregation_scope IS NULL
            OR aggregation_scope IN ('subject', 'event', 'session', 'window')
        ),
        CHECK (
            (metric_family = 'count_metric' AND value_semantics = 'count')
            OR (metric_family = 'sum_metric' AND value_semantics = 'sum')
            OR (metric_family = 'rate_metric' AND value_semantics = 'ratio')
            OR (metric_family = 'average_metric' AND value_semantics = 'mean')
            OR (
                metric_family = 'distribution_metric'
                AND value_semantics = 'distribution_statistic'
            )
            OR (metric_family = 'score_metric' AND value_semantics = 'score')
            OR (
                metric_family = 'survival_metric'
                AND value_semantics = 'survival_probability'
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_process_objects (
        process_contract_id     TEXT PRIMARY KEY,
        process_ref             TEXT NOT NULL UNIQUE,
        display_name            TEXT NOT NULL,
        description             TEXT NOT NULL DEFAULT '',
        process_type            TEXT NOT NULL CHECK (
            process_type IN (
                'experiment_context',
                'cohort_definition',
                'funnel_definition',
                'session_contract',
                'path_pattern',
                'lifecycle_state_machine'
            )
        ),
        process_contract_version TEXT NOT NULL,
        contract_mode           TEXT NOT NULL CHECK (
            contract_mode IN ('context_provider', 'entity_stream')
        ),
        context_kind            TEXT,
        population_subject_ref  TEXT NOT NULL,
        membership_cardinality  TEXT,
        entity_ref              TEXT,
        emitted_grain_ref       TEXT,
        subject_cardinality     TEXT,
        anchor_time_ref         TEXT,
        process_payload_json     TEXT NOT NULL DEFAULT '{}',
        catalog_metadata_json    TEXT NOT NULL DEFAULT '{}',
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK (substr(process_ref, 1, 8) = 'process.'),
        CHECK (substr(population_subject_ref, 1, 8) = 'subject.'),
        CHECK (entity_ref IS NULL OR substr(entity_ref, 1, 7) = 'entity.'),
        CHECK (emitted_grain_ref IS NULL OR substr(emitted_grain_ref, 1, 6) = 'grain.'),
        CHECK (anchor_time_ref IS NULL OR substr(anchor_time_ref, 1, 5) = 'time.'),
        CHECK (
            (
                contract_mode = 'context_provider'
                AND context_kind IS NOT NULL
                AND membership_cardinality IS NOT NULL
                AND entity_ref IS NULL
                AND emitted_grain_ref IS NULL
                AND subject_cardinality IS NULL
            )
            OR
            (
                contract_mode = 'entity_stream'
                AND entity_ref IS NOT NULL
                AND emitted_grain_ref IS NOT NULL
                AND subject_cardinality IS NOT NULL
                AND context_kind IS NULL
                AND membership_cardinality IS NULL
            )
        ),
        CHECK (
            context_kind IS NULL OR context_kind IN ('cohort_membership', 'experiment_split')
        ),
        CHECK (
            membership_cardinality IS NULL
            OR membership_cardinality IN ('exclusive_one', 'repeatable_many')
        ),
        CHECK (subject_cardinality IS NULL OR subject_cardinality IN ('one', 'many'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_process_exported_dimension_refs (
        process_contract_id  TEXT NOT NULL REFERENCES semantic_process_objects(process_contract_id) ON DELETE CASCADE,
        position             INTEGER NOT NULL,
        dimension_ref        TEXT NOT NULL,
        PRIMARY KEY (process_contract_id, position),
        UNIQUE(process_contract_id, dimension_ref),
        CHECK (position > 0),
        CHECK (substr(dimension_ref, 1, 10) = 'dimension.')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_dimension_contracts (
        dimension_contract_id     TEXT PRIMARY KEY,
        dimension_ref             TEXT NOT NULL UNIQUE,
        display_name              TEXT NOT NULL,
        description               TEXT NOT NULL DEFAULT '',
        dimension_contract_version TEXT NOT NULL,
        structure_kind            TEXT NOT NULL CHECK (
            structure_kind IN ('flat', 'hierarchical', 'ordinal', 'time_derived')
        ),
        semantic_role             TEXT,
        value_type                TEXT NOT NULL CHECK (
            value_type IN ('string', 'integer', 'number', 'boolean', 'date', 'datetime')
        ),
        domain_kind               TEXT NOT NULL CHECK (
            domain_kind IN ('open', 'enumerated')
        ),
        enum_set_ref              TEXT,
        enum_version              TEXT,
        hierarchy_type            TEXT,
        parent_dimension_ref      TEXT,
        supports_grouping         INTEGER NOT NULL DEFAULT 1 CHECK (supports_grouping IN (0, 1)),
        required_time_anchor_ref  TEXT,
        source_field_ref          TEXT,
        dimension_payload_json     TEXT NOT NULL DEFAULT '{}',
        catalog_metadata_json      TEXT NOT NULL DEFAULT '{}',
        status                    TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                  INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at                TEXT NOT NULL,
        updated_at                TEXT NOT NULL,
        CHECK (substr(dimension_ref, 1, 10) = 'dimension.'),
        CHECK (enum_set_ref IS NULL OR substr(enum_set_ref, 1, 5) = 'enum.'),
        CHECK (parent_dimension_ref IS NULL OR substr(parent_dimension_ref, 1, 10) = 'dimension.'),
        CHECK (required_time_anchor_ref IS NULL OR substr(required_time_anchor_ref, 1, 5) = 'time.'),
        CHECK (
            source_field_ref IS NULL
            OR (
              substr(source_field_ref, 1, 7) = 'entity.'
              AND instr(source_field_ref, '.field.') > 0
            )
        ),
        CHECK (
            (
                domain_kind = 'enumerated'
                AND enum_set_ref IS NOT NULL
                AND enum_version IS NOT NULL
            )
            OR
            (
                domain_kind = 'open'
                AND enum_set_ref IS NULL
                AND enum_version IS NULL
            )
        ),
        CHECK (
            (
                structure_kind = 'time_derived'
                AND required_time_anchor_ref IS NOT NULL
            )
            OR
            (
                structure_kind != 'time_derived'
                AND required_time_anchor_ref IS NULL
            )
        ),
        CHECK (
            semantic_role IS NULL
            OR semantic_role IN ('category', 'label', 'state', 'variant', 'metric')
        ),
        CHECK (
            hierarchy_type IS NULL
            OR hierarchy_type IN ('flat', 'parent_child', 'ordinal', 'calendar_rollup')
        ),
        CHECK (
            parent_dimension_ref IS NULL
            OR (
                hierarchy_type IS NOT NULL
                AND hierarchy_type IN ('parent_child', 'ordinal', 'calendar_rollup')
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_time_objects (
        time_contract_id        TEXT PRIMARY KEY,
        time_ref                TEXT NOT NULL UNIQUE,
        display_name            TEXT NOT NULL,
        description             TEXT NOT NULL DEFAULT '',
        time_contract_version   TEXT NOT NULL,
        business_anchor         INTEGER NOT NULL DEFAULT 0 CHECK (business_anchor IN (0, 1)),
        measurement             INTEGER NOT NULL DEFAULT 0 CHECK (measurement IN (0, 1)),
        operational_support     INTEGER NOT NULL DEFAULT 0 CHECK (operational_support IN (0, 1)),
        source_field_ref        TEXT,
        catalog_metadata_json   TEXT NOT NULL DEFAULT '{}',
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK (substr(time_ref, 1, 5) = 'time.'),
        CHECK (
            source_field_ref IS NULL
            OR (
              substr(source_field_ref, 1, 7) = 'entity.'
              AND instr(source_field_ref, '.field.') > 0
            )
        ),
        CHECK (business_anchor = 1 OR measurement = 1 OR operational_support = 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_enum_sets (
        enum_set_contract_id    TEXT PRIMARY KEY,
        enum_set_ref            TEXT NOT NULL UNIQUE,
        display_name            TEXT NOT NULL,
        description             TEXT NOT NULL DEFAULT '',
        value_type              TEXT NOT NULL CHECK (
            value_type IN ('string', 'integer', 'number', 'boolean')
        ),
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK (substr(enum_set_ref, 1, 5) = 'enum.')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_enum_set_versions (
        enum_set_version_id     TEXT PRIMARY KEY,
        enum_set_contract_id    TEXT NOT NULL REFERENCES semantic_enum_sets(enum_set_contract_id) ON DELETE CASCADE,
        enum_version            TEXT NOT NULL,
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        UNIQUE(enum_set_contract_id, enum_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_enum_set_values (
        enum_set_version_id     TEXT NOT NULL REFERENCES semantic_enum_set_versions(enum_set_version_id) ON DELETE CASCADE,
        position                INTEGER NOT NULL,
        value_key               TEXT NOT NULL,
        raw_value               TEXT NOT NULL,
        label                   TEXT NOT NULL,
        aliases_json            TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (enum_set_version_id, position),
        UNIQUE(enum_set_version_id, value_key),
        UNIQUE(enum_set_version_id, raw_value),
        CHECK (position > 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_predicate_contracts (
        predicate_contract_id      TEXT PRIMARY KEY,
        predicate_ref              TEXT NOT NULL UNIQUE,
        display_name               TEXT NOT NULL,
        description                TEXT NOT NULL DEFAULT '',
        subject_ref                TEXT NOT NULL,
        predicate_contract_version TEXT NOT NULL,
        payload_json               TEXT NOT NULL DEFAULT '{}',
        catalog_metadata_json      TEXT NOT NULL DEFAULT '{}',
        status                     TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                   INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at                 TEXT NOT NULL,
        updated_at                 TEXT NOT NULL,
        CHECK (substr(predicate_ref, 1, 10) = 'predicate.'),
        CHECK (substr(subject_ref, 1, 7) = 'entity.' OR substr(subject_ref, 1, 8) = 'subject.')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_key_refs_entity ON semantic_entity_key_refs(entity_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_stable_descriptors_entity ON semantic_entity_stable_descriptors(entity_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_metric_contracts_status_ref ON semantic_metric_contracts(status, metric_ref)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_metric_contracts_ref_revision ON semantic_metric_contracts(metric_ref, revision)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_metric_contracts_latest_active ON semantic_metric_contracts(metric_ref) WHERE status = 'published' AND is_latest_active = 1",
    "CREATE INDEX IF NOT EXISTS idx_semantic_process_exported_dimension_refs_process ON semantic_process_exported_dimension_refs(process_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_dimension_contracts_status_ref ON semantic_dimension_contracts(status, dimension_ref)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_enum_set_versions_enum_set ON semantic_enum_set_versions(enum_set_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_enum_set_values_version ON semantic_enum_set_values(enum_set_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_predicate_contracts_status_ref ON semantic_predicate_contracts(status, predicate_ref)",
    """
    CREATE TABLE IF NOT EXISTS semantic_domain_catalog (
        domain_ref   TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated')),
        aliases_json TEXT NOT NULL DEFAULT '[]',
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        CHECK (substr(domain_ref, 1, 7) = 'domain.')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_domain_catalog_status_ref ON semantic_domain_catalog(status, domain_ref)",
    # -------------------------------------------------------------------------
    # Entity relationships
    # -------------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS semantic_entity_relationships (
        relationship_id                         TEXT PRIMARY KEY,
        relationship_ref                        TEXT NOT NULL UNIQUE,
        display_name                            TEXT NOT NULL,
        description                             TEXT NOT NULL DEFAULT '',
        left_entity_ref                         TEXT NOT NULL,
        right_entity_ref                        TEXT NOT NULL,
        key_alignment_json                      TEXT NOT NULL,
        time_alignment_json                     TEXT,
        cardinality                             TEXT NOT NULL CHECK (
            cardinality IN ('one_to_one', 'many_to_one', 'one_to_many', 'many_to_many')
        ),
        grain_compatibility_json                TEXT,
        snapshot_effective_window_alignment_json TEXT,
        catalog_metadata_json                   TEXT NOT NULL DEFAULT '{}',
        status                                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at                              TEXT NOT NULL,
        updated_at                              TEXT NOT NULL,
        CHECK (substr(relationship_ref, 1, 13) = 'relationship.'),
        CHECK (substr(left_entity_ref, 1, 7) = 'entity.'),
        CHECK (substr(right_entity_ref, 1, 7) = 'entity.')
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_relationships_ref ON semantic_entity_relationships(relationship_ref)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_relationships_pair ON semantic_entity_relationships(left_entity_ref, right_entity_ref, status, relationship_ref)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_relationships_right_left ON semantic_entity_relationships(right_entity_ref, left_entity_ref, status, relationship_ref)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_relationships_status ON semantic_entity_relationships(status)",
    # -------------------------------------------------------------------------
    # Compiler compatibility profiles (Phase 1, Task 1.4)
    # -------------------------------------------------------------------------
    # NOTE: subject_ref FK validation deferred to Phase 3 service layer.
    # SQLite cannot enforce FK across multiple tables based on subject_kind,
    # so the service layer must validate that subject_ref points to an
    # existing semantic object (metric/process/binding) before insert.
    """
    CREATE TABLE IF NOT EXISTS compiler_compatibility_profiles (
        profile_id              TEXT PRIMARY KEY,
        profile_ref             TEXT NOT NULL UNIQUE,
        profile_kind            TEXT NOT NULL CHECK (
            profile_kind IN ('requirement', 'capability')
        ),
        schema_version          TEXT NOT NULL DEFAULT 'v1' CHECK (schema_version = 'v1'),
        subject_kind            TEXT NOT NULL CHECK (
            subject_kind IN ('metric', 'process', 'binding')
        ),
        subject_ref             TEXT NOT NULL,
        subject_revision        INTEGER,
        requirement_json        TEXT NOT NULL DEFAULT '{}',
        capability_json         TEXT NOT NULL DEFAULT '{}',
        catalog_metadata_json   TEXT NOT NULL DEFAULT '{}',
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK (substr(profile_ref, 1, 17) = 'compiler_profile.'),
        CHECK (
            (subject_kind = 'metric' AND substr(subject_ref, 1, 7) = 'metric.')
            OR (subject_kind = 'process' AND substr(subject_ref, 1, 8) = 'process.')
            OR (subject_kind = 'binding' AND substr(subject_ref, 1, 8) = 'binding.')
        ),
        CHECK (
            (subject_kind = 'metric' AND profile_kind = 'requirement')
            OR (subject_kind = 'process' AND profile_kind = 'capability')
            OR (subject_kind = 'binding' AND profile_kind = 'capability')
        ),
        CHECK (
            (profile_kind = 'requirement' AND requirement_json != '{}')
            OR (profile_kind = 'capability' AND capability_json != '{}')
        ),
        CHECK (
            (profile_kind = 'requirement' AND capability_json = '{}')
            OR (profile_kind = 'capability' AND requirement_json = '{}')
        ),
        CHECK (subject_revision IS NULL OR subject_revision >= 1)
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_compiler_profiles_subject_revision_guard
    BEFORE UPDATE ON compiler_compatibility_profiles
    FOR EACH ROW
    WHEN NEW.status = 'published' AND NEW.subject_revision IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'published compatibility profiles require subject_revision');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_compiler_profiles_subject_revision_insert_guard
    BEFORE INSERT ON compiler_compatibility_profiles
    FOR EACH ROW
    WHEN NEW.status = 'published' AND NEW.subject_revision IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'published compatibility profiles require subject_revision');
    END
    """,
    # Compiler compatibility profile indexes
    "CREATE INDEX IF NOT EXISTS idx_compiler_compatibility_profiles_ref ON compiler_compatibility_profiles(profile_ref)",
    "CREATE INDEX IF NOT EXISTS idx_compiler_compatibility_profiles_subject ON compiler_compatibility_profiles(subject_kind, subject_ref)",
    "CREATE INDEX IF NOT EXISTS idx_compiler_compatibility_profiles_subject_status_ref ON compiler_compatibility_profiles(subject_kind, subject_ref, status, profile_ref)",
    "CREATE INDEX IF NOT EXISTS idx_compiler_compatibility_profiles_status ON compiler_compatibility_profiles(status)",
    # -- Engine registry --
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
    "CREATE INDEX IF NOT EXISTS idx_jobs_session_status_submitted ON jobs(session_id, status, submitted_at DESC)",
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
    "CREATE INDEX IF NOT EXISTS idx_findings_artifact_created ON findings(artifact_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_findings_session_type ON findings(session_id, finding_type)",
    "CREATE INDEX IF NOT EXISTS idx_findings_session_created ON findings(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_findings_session_type_created ON findings(session_id, finding_type, created_at)",
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
    "CREATE INDEX IF NOT EXISTS idx_propositions_session_created ON propositions(session_id, created_at)",
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
    "CREATE INDEX IF NOT EXISTS idx_assessments_session_created ON assessments(session_id, created_at)",
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
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_session_status_created ON evidence_gaps(session_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition ON evidence_gaps(proposition_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition_status ON evidence_gaps(proposition_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_gaps_proposition_status_created ON evidence_gaps(proposition_id, status, created_at)",
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
    "CREATE INDEX IF NOT EXISTS idx_inference_records_proposition_created ON inference_records(proposition_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_inference_records_assessment ON inference_records(assessment_id)",
    "CREATE INDEX IF NOT EXISTS idx_inference_records_assessment_created ON inference_records(assessment_id, created_at)",
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
        region_code                TEXT NOT NULL,
        calendar_version           TEXT NOT NULL,
        weekday                    INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
        is_weekend                 INTEGER NOT NULL CHECK (is_weekend IN (0, 1)),
        is_workday                 INTEGER NOT NULL CHECK (is_workday IN (0, 1)),
        holiday_name               TEXT,
        holiday_group_id           TEXT,
        year_relative_holiday_key  TEXT,
        event_group_id             TEXT,
        year_relative_event_key    TEXT,
        PRIMARY KEY (calendar_version, region_code, calendar_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_calendar_version_region ON calendar(calendar_version, region_code)",
    """
    CREATE TABLE IF NOT EXISTS metadata_schema_marker (
        backend         TEXT NOT NULL PRIMARY KEY CHECK (backend IN ('sqlite', 'mysql')),
        schema_version  TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        ddl_fingerprint TEXT NOT NULL
    )
    """,
    # -------------------------------------------------------------------------
    # Session snapshot tables for dual-path semantic layer
    # -------------------------------------------------------------------------
    "DROP TABLE IF EXISTS session_semantic_snapshots",
    "DROP TABLE IF EXISTS analysis_sessions",
    """
    CREATE TABLE analysis_sessions (
        session_id          TEXT PRIMARY KEY,
        requesting_user     TEXT NOT NULL,
        snapshot_frozen_at  TEXT NOT NULL DEFAULT (datetime('now')),
        status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        ended_at            TEXT
    )
    """,
    """
    CREATE TABLE session_semantic_snapshots (
        snapshot_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id          TEXT NOT NULL REFERENCES analysis_sessions(session_id),
        model_name          TEXT NOT NULL,
        revision            INTEGER NOT NULL CHECK (revision >= 1),
        visibility          TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
        owner_user          TEXT,
        frozen_at           TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_session_snapshots_session ON session_semantic_snapshots(session_id)",
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
        elif "idx_semantic_metric_contracts_latest_active" in stripped:
            ddl.append(
                "CREATE UNIQUE INDEX idx_semantic_metric_contracts_latest_active "
                "ON semantic_metric_contracts(metric_latest_active_ref)"
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
    if table_name == "semantic_metric_contracts" and stripped.startswith("is_latest_active"):
        leading = line[: len(line) - len(line.lstrip())]
        comma = "," if stripped.endswith(",") else ""
        generated = (
            f"{leading}metric_latest_active_ref        {_MYSQL_KEY_TEXT_TYPE} "
            "GENERATED ALWAYS AS (CASE WHEN status = 'published' AND is_latest_active = 1 "
            f"THEN metric_ref ELSE NULL END) STORED{comma}"
        )
        return [line, generated], []

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
