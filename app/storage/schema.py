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
    CREATE TABLE IF NOT EXISTS legacy_semantic_mappings (
        mapping_id       TEXT PRIMARY KEY,
        semantic_type    TEXT NOT NULL,
        semantic_id      TEXT NOT NULL,
        object_id        TEXT NOT NULL,
        mapping_type     TEXT NOT NULL,
        mapping_json     TEXT NOT NULL DEFAULT '{}',
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    )
    """,
    """
    CREATE VIEW IF NOT EXISTS semantic_mappings AS
    SELECT
        mapping_id,
        semantic_type,
        semantic_id,
        object_id,
        mapping_type,
        mapping_json,
        created_at,
        updated_at
    FROM legacy_semantic_mappings
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_entity_contracts (
        entity_contract_id      TEXT PRIMARY KEY,
        entity_ref              TEXT NOT NULL UNIQUE,
        display_name            TEXT NOT NULL,
        description             TEXT NOT NULL DEFAULT '',
        entity_contract_version TEXT NOT NULL,
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
        metric_ref              TEXT NOT NULL UNIQUE,
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
        additivity              TEXT NOT NULL CHECK (
            additivity IN ('additive', 'semi_additive', 'non_additive')
        ),
        metric_contract_version  TEXT NOT NULL,
        family_payload_json      TEXT NOT NULL DEFAULT '{}',
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
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
            CASE
                WHEN metric_family = 'count_metric' THEN value_semantics = 'count'
                WHEN metric_family = 'sum_metric' THEN value_semantics = 'sum'
                WHEN metric_family = 'rate_metric' THEN value_semantics = 'ratio'
                WHEN metric_family = 'average_metric' THEN value_semantics = 'mean'
                WHEN metric_family = 'distribution_metric' THEN value_semantics = 'distribution_statistic'
                WHEN metric_family = 'score_metric' THEN value_semantics = 'score'
                WHEN metric_family = 'survival_metric' THEN value_semantics = 'survival_probability'
                ELSE 1
            END
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
        dimension_payload_json     TEXT NOT NULL DEFAULT '{}',
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
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK (substr(time_ref, 1, 5) = 'time.'),
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
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_key_refs_entity ON semantic_entity_key_refs(entity_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_entity_stable_descriptors_entity ON semantic_entity_stable_descriptors(entity_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_process_exported_dimension_refs_process ON semantic_process_exported_dimension_refs(process_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_enum_set_versions_enum_set ON semantic_enum_set_versions(enum_set_contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_enum_set_values_version ON semantic_enum_set_values(enum_set_version_id)",
    # -------------------------------------------------------------------------
    # Typed binding contract tables
    # -------------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS typed_bindings (
        binding_id        TEXT PRIMARY KEY,
        binding_ref       TEXT NOT NULL UNIQUE,
        binding_scope     TEXT NOT NULL CHECK (
            binding_scope IN ('entity', 'process_object', 'metric')
        ),
        bound_object_ref  TEXT NOT NULL,
        binding_contract_version TEXT NOT NULL CHECK (
            substr(binding_contract_version, 1, 8) = 'binding.'
        ),
        display_name      TEXT,
        description       TEXT,
        status            TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision          INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        CHECK (substr(binding_ref, 1, 8) = 'binding.')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS binding_imports (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        binding_id         TEXT NOT NULL REFERENCES typed_bindings(binding_id) ON DELETE CASCADE,
        import_key         TEXT NOT NULL,
        imported_binding_ref TEXT NOT NULL CHECK (substr(imported_binding_ref, 1, 8) = 'binding.'),
        required_ref_prefixes_json TEXT NOT NULL DEFAULT '[]',
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(binding_id, import_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS carrier_bindings (
        carrier_binding_id TEXT PRIMARY KEY,
        binding_id         TEXT NOT NULL REFERENCES typed_bindings(binding_id) ON DELETE CASCADE,
        binding_key        TEXT NOT NULL,
        source_object_ref  TEXT,
        carrier_kind       TEXT NOT NULL CHECK (carrier_kind IN ('table', 'view')),
        carrier_locator    TEXT NOT NULL,
        binding_role       TEXT NOT NULL CHECK (binding_role IN ('primary', 'auxiliary')),
        semantic_role_ref  TEXT,
        grain_ref          TEXT,
        primary_entity_ref TEXT CHECK (
            primary_entity_ref IS NULL OR substr(primary_entity_ref, 1, 7) = 'entity.'
        ),
        row_filter_refs_json TEXT NOT NULL DEFAULT '[]',
        freshness_policy_ref TEXT,
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at         TEXT NOT NULL,
        UNIQUE(binding_id, binding_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS carrier_field_surfaces (
        carrier_binding_id TEXT NOT NULL REFERENCES carrier_bindings(carrier_binding_id) ON DELETE CASCADE,
        position           INTEGER NOT NULL,
        surface_ref        TEXT NOT NULL CHECK (substr(surface_ref, 1, 6) = 'field.'),
        physical_name      TEXT NOT NULL,
        field_type         TEXT,
        PRIMARY KEY (carrier_binding_id, position),
        UNIQUE(carrier_binding_id, surface_ref),
        CHECK (position > 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS carrier_time_surfaces (
        carrier_binding_id TEXT NOT NULL REFERENCES carrier_bindings(carrier_binding_id) ON DELETE CASCADE,
        position           INTEGER NOT NULL,
        surface_ref        TEXT NOT NULL CHECK (substr(surface_ref, 1, 13) = 'time_surface.'),
        physical_name      TEXT NOT NULL,
        time_granularity   TEXT CHECK (
            time_granularity IS NULL OR time_granularity IN ('second', 'minute', 'hour', 'day')
        ),
        PRIMARY KEY (carrier_binding_id, position),
        UNIQUE(carrier_binding_id, surface_ref),
        CHECK (position > 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS field_bindings (
        field_binding_id   TEXT PRIMARY KEY,
        binding_id         TEXT NOT NULL REFERENCES typed_bindings(binding_id) ON DELETE CASCADE,
        carrier_binding_key TEXT NOT NULL,
        target_kind        TEXT NOT NULL CHECK (
            target_kind IN (
                'identity_key',
                'primary_time',
                'stable_descriptor',
                'population_subject',
                'analysis_window_anchor',
                'process_context',
                'metric_input'
            )
        ),
        target_key         TEXT NOT NULL,
        context_ref        TEXT,
        semantic_ref       TEXT NOT NULL,
        surface_ref        TEXT NOT NULL CHECK (substr(surface_ref, 1, 6) = 'field.'),
        field_type_ref     TEXT,
        nullability_policy TEXT CHECK (
            nullability_policy IS NULL OR nullability_policy IN ('reject', 'allow', 'impute')
        ),
        repeated_value_policy TEXT CHECK (
            repeated_value_policy IS NULL
            OR repeated_value_policy IN ('take_first', 'take_last', 'aggregate', 'explode')
        ),
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(binding_id, carrier_binding_key, target_kind, target_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS join_relations (
        relation_id        TEXT PRIMARY KEY,
        binding_id         TEXT NOT NULL REFERENCES typed_bindings(binding_id) ON DELETE CASCADE,
        relation_key       TEXT NOT NULL,
        left_binding_key   TEXT NOT NULL,
        right_binding_key  TEXT NOT NULL,
        join_kind          TEXT CHECK (
            join_kind IS NULL OR join_kind IN ('inner', 'left', 'semi', 'anti')
        ),
        key_ref_pairs_json TEXT NOT NULL DEFAULT '[]',
        cardinality        TEXT CHECK (
            cardinality IS NULL
            OR cardinality IN ('one_to_one', 'many_to_one', 'one_to_many', 'many_to_many')
        ),
        temporal_constraint_refs_json TEXT NOT NULL DEFAULT '[]',
        compatibility_rule_refs_json TEXT NOT NULL DEFAULT '[]',
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(binding_id, relation_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS consumption_policies (
        policy_id          TEXT PRIMARY KEY,
        binding_id         TEXT NOT NULL REFERENCES typed_bindings(binding_id) ON DELETE CASCADE,
        policy_key         TEXT NOT NULL,
        policy_type        TEXT NOT NULL CHECK (
            policy_type IN ('late_arrival_policy', 'incomplete_window_policy')
        ),
        policy_target_path TEXT NOT NULL,
        anchor_ref         TEXT,
        grace_period_ref   TEXT,
        behavior           TEXT CHECK (
            behavior IS NULL OR behavior IN ('exclude_open_subjects', 'clip_to_window', 'keep_partial')
        ),
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(binding_id, policy_key)
    )
    """,
    # Typed binding indexes
    "CREATE INDEX IF NOT EXISTS idx_typed_bindings_ref ON typed_bindings(binding_ref)",
    "CREATE INDEX IF NOT EXISTS idx_binding_imports_binding ON binding_imports(binding_id)",
    "CREATE INDEX IF NOT EXISTS idx_carrier_bindings_binding ON carrier_bindings(binding_id)",
    "CREATE INDEX IF NOT EXISTS idx_carrier_bindings_key ON carrier_bindings(binding_id, binding_key)",
    "CREATE INDEX IF NOT EXISTS idx_carrier_field_surfaces_carrier ON carrier_field_surfaces(carrier_binding_id)",
    "CREATE INDEX IF NOT EXISTS idx_carrier_time_surfaces_carrier ON carrier_time_surfaces(carrier_binding_id)",
    "CREATE INDEX IF NOT EXISTS idx_field_bindings_binding ON field_bindings(binding_id)",
    "CREATE INDEX IF NOT EXISTS idx_join_relations_binding ON join_relations(binding_id)",
    "CREATE INDEX IF NOT EXISTS idx_consumption_policies_binding ON consumption_policies(binding_id)",
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
        status                  TEXT NOT NULL DEFAULT 'draft' CHECK (
            status IN ('draft', 'published', 'deprecated')
        ),
        revision                INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        created_at              TEXT NOT NULL,
        updated_at              TEXT NOT NULL,
        CHECK (substr(profile_ref, 1, 17) = 'compiler_profile.'),
        CHECK (
            CASE
                WHEN subject_kind = 'metric' THEN substr(subject_ref, 1, 7) = 'metric.'
                WHEN subject_kind = 'process' THEN substr(subject_ref, 1, 8) = 'process.'
                WHEN subject_kind = 'binding' THEN substr(subject_ref, 1, 8) = 'binding.'
                ELSE 0
            END
        ),
        CHECK (
            (subject_kind = 'metric' AND profile_kind = 'requirement')
            OR (subject_kind = 'process' AND profile_kind = 'capability')
            OR (subject_kind = 'binding' AND profile_kind = 'capability')
        ),
        CHECK (
            CASE
                WHEN profile_kind = 'requirement' THEN requirement_json != '{}'
                WHEN profile_kind = 'capability' THEN capability_json != '{}'
                ELSE 0
            END
        ),
        CHECK (
            CASE
                WHEN profile_kind = 'requirement' THEN capability_json = '{}'
                WHEN profile_kind = 'capability' THEN requirement_json = '{}'
                ELSE 0
            END
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
    "CREATE INDEX IF NOT EXISTS idx_compiler_compatibility_profiles_status ON compiler_compatibility_profiles(status)",
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
