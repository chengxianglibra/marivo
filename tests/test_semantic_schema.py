from __future__ import annotations

import sqlite3
import unittest

from app.storage.schema import METADATA_DDL


class SemanticSchemaDDLTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        for stmt in METADATA_DDL:
            if isinstance(stmt, str):
                self.conn.execute(stmt)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_target_semantic_tables_exist(self) -> None:
        tables = [
            "semantic_entity_contracts",
            "semantic_entity_key_refs",
            "semantic_entity_stable_descriptors",
            "semantic_metric_contracts",
            "semantic_process_objects",
            "semantic_process_exported_dimension_refs",
            "semantic_dimension_contracts",
            "semantic_time_objects",
            "semantic_enum_sets",
            "semantic_enum_set_versions",
            "semantic_enum_set_values",
            "compiler_compatibility_profiles",
        ]
        for table in tables:
            row = self.conn.execute(
                "SELECT COUNT(*) AS cnt FROM sqlite_master WHERE type = 'table' AND name = ?",
                [table],
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["cnt"], 1, f"Table {table} should exist")

    def test_semantic_list_indexes_exist(self) -> None:
        indexes = {
            "idx_semantic_metric_contracts_status_ref",
            "idx_semantic_metric_contracts_ref_revision",
            "idx_semantic_metric_contracts_latest_active",
            "idx_semantic_dimension_contracts_status_ref",
            "idx_typed_bindings_status_ref",
        }
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name IN (?, ?, ?, ?, ?)",
            tuple(indexes),
        ).fetchall()
        self.assertEqual({str(row["name"]) for row in rows}, indexes)

    def test_entity_contract_constraints(self) -> None:
        self.conn.execute(
            """
            INSERT INTO semantic_entity_contracts (
                entity_contract_id,
                entity_ref,
                display_name,
                description,
                entity_contract_version,
                uniqueness_scope,
                id_stability,
                nullable_key_policy,
                parent_entity_ref,
                cardinality_to_parent,
                ownership_semantics,
                primary_time_ref,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "ent_contract_1",
                "entity.user",
                "User",
                "Stable user entity",
                "entity.v1",
                "global",
                "stable",
                "reject",
                None,
                None,
                None,
                "time.user_created_at",
                "2026-04-08T00:00:00Z",
                "2026-04-08T00:00:00Z",
            ],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_entity_key_refs (
                entity_contract_id, position, key_ref, description
            ) VALUES (?, ?, ?, ?)
            """,
            ["ent_contract_1", 1, "key.user_id", "Primary user key"],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_entity_key_refs (
                entity_contract_id, position, key_ref, description
            ) VALUES (?, ?, ?, ?)
            """,
            ["ent_contract_1", 2, "key.account_id", "Secondary account key"],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_entity_stable_descriptors (
                entity_contract_id, position, dimension_ref, cardinality
            ) VALUES (?, ?, ?, ?)
            """,
            ["ent_contract_1", 1, "dimension.country", "one"],
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_entity_contracts (
                    entity_contract_id,
                    entity_ref,
                    display_name,
                    description,
                    entity_contract_version,
                    uniqueness_scope,
                    id_stability,
                    nullable_key_policy,
                    parent_entity_ref,
                    cardinality_to_parent,
                    ownership_semantics,
                    primary_time_ref,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "ent_contract_2",
                    "entity.session",
                    "Session",
                    "Missing parent cardinality",
                    "entity.v1",
                    "parent_scoped",
                    "stable",
                    "reject",
                    "entity.user",
                    None,
                    "belongs_to",
                    "time.session_started_at",
                    "2026-04-08T00:00:00Z",
                    "2026-04-08T00:00:00Z",
                ],
            )
            self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_entity_key_refs (
                    entity_contract_id, position, key_ref, description
                ) VALUES (?, ?, ?, ?)
                """,
                ["missing_contract", 1, "key.orphan", "Should fail FK"],
            )
            self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_entity_key_refs (
                    entity_contract_id, position, key_ref, description
                ) VALUES (?, ?, ?, ?)
                """,
                ["ent_contract_1", 3, "key.user_id", "Duplicate key"],
            )
            self.conn.commit()

    def test_metric_contract_constraints(self) -> None:
        self.conn.execute(
            """
            INSERT INTO semantic_metric_contracts (
                metric_contract_id,
                metric_ref,
                display_name,
                description,
                metric_family,
                population_subject_ref,
                observed_entity_ref,
                observation_grain_ref,
                sample_kind,
                value_semantics,
                aggregation_scope,
                primary_time_ref,
                additivity_constraints_json,
                metric_contract_version,
                family_payload_json,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "met_contract_1",
                "metric.conversion_rate",
                "Conversion Rate",
                "Rate of converted users",
                "rate_metric",
                "subject.user",
                "entity.user",
                "grain.user",
                "rate",
                "ratio",
                "subject",
                "time.conversion_time",
                '{"dimension_policy":"none","time_axis_policy":"non_additive"}',
                "metric.v1",
                '{"numerator":"measure.converted_user","denominator":"measure.eligible_user"}',
                "2026-04-08T00:00:00Z",
                "2026-04-08T00:00:00Z",
            ],
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_metric_contracts (
                    metric_contract_id,
                    metric_ref,
                    display_name,
                    description,
                    metric_family,
                    population_subject_ref,
                    observed_entity_ref,
                    observation_grain_ref,
                    sample_kind,
                    value_semantics,
                    aggregation_scope,
                    primary_time_ref,
                    additivity_constraints_json,
                    metric_contract_version,
                    family_payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "met_contract_2",
                    "metric.gross_merchandise_value",
                    "GMV",
                    "Sum of paid amount",
                    "sum_metric",
                    "subject.order",
                    "entity.order",
                    "grain.order",
                    "numeric",
                    "ratio",
                    "event",
                    "time.payment_time",
                    '{"dimension_policy":"all","time_axis_policy":"additive"}',
                    "metric.v1",
                    "{}",
                    "2026-04-08T00:00:00Z",
                    "2026-04-08T00:00:00Z",
                ],
            )
            self.conn.commit()

    def test_process_contract_constraints(self) -> None:
        self.conn.execute(
            """
            INSERT INTO semantic_process_objects (
                process_contract_id,
                process_ref,
                display_name,
                description,
                process_type,
                process_contract_version,
                contract_mode,
                context_kind,
                population_subject_ref,
                membership_cardinality,
                entity_ref,
                emitted_grain_ref,
                subject_cardinality,
                anchor_time_ref,
                process_payload_json,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "proc_contract_1",
                "process.signup_cohort",
                "Signup Cohort",
                "Cohort membership provider",
                "cohort_definition",
                "process.v2",
                "context_provider",
                "cohort_membership",
                "subject.user",
                "exclusive_one",
                None,
                None,
                None,
                "time.signup_time",
                '{"cohort_key":"signup_week"}',
                "2026-04-08T00:00:00Z",
                "2026-04-08T00:00:00Z",
            ],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_process_exported_dimension_refs (
                process_contract_id, position, dimension_ref
            ) VALUES (?, ?, ?)
            """,
            ["proc_contract_1", 1, "dimension.variant"],
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_process_objects (
                    process_contract_id,
                    process_ref,
                    display_name,
                    description,
                    process_type,
                    process_contract_version,
                    contract_mode,
                    context_kind,
                    population_subject_ref,
                    membership_cardinality,
                    entity_ref,
                    emitted_grain_ref,
                    subject_cardinality,
                    anchor_time_ref,
                    process_payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "proc_contract_2",
                    "process.sessionized_users",
                    "Sessionized Users",
                    "Invalid entity stream wiring",
                    "session_contract",
                    "process.v2",
                    "entity_stream",
                    None,
                    "subject.user",
                    None,
                    None,
                    None,
                    None,
                    "time.session_started_at",
                    "{}",
                    "2026-04-08T00:00:00Z",
                    "2026-04-08T00:00:00Z",
                ],
            )
            self.conn.commit()

    def test_dimension_time_and_enum_constraints(self) -> None:
        self.conn.execute(
            """
            INSERT INTO semantic_dimension_contracts (
                dimension_contract_id,
                dimension_ref,
                display_name,
                description,
                dimension_contract_version,
                structure_kind,
                semantic_role,
                value_type,
                domain_kind,
                enum_set_ref,
                enum_version,
                hierarchy_type,
                parent_dimension_ref,
                supports_grouping,
                required_time_anchor_ref,
                dimension_payload_json,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "dim_contract_1",
                "dimension.country",
                "Country",
                "Country dimension",
                "dimension.v1",
                "hierarchical",
                "category",
                "string",
                "enumerated",
                "enum.iso_country_code",
                "2026-01",
                "parent_child",
                None,
                1,
                None,
                '{"notes":"published set"}',
                "2026-04-08T00:00:00Z",
                "2026-04-08T00:00:00Z",
            ],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_time_objects (
                time_contract_id,
                time_ref,
                display_name,
                description,
                time_contract_version,
                business_anchor,
                measurement,
                operational_support,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "time_contract_1",
                "time.conversion_time",
                "Conversion Time",
                "Measurement and business anchor time",
                "time.v1",
                1,
                1,
                0,
                "2026-04-08T00:00:00Z",
                "2026-04-08T00:00:00Z",
            ],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_enum_sets (
                enum_set_contract_id,
                enum_set_ref,
                display_name,
                value_type,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                "enum_contract_1",
                "enum.iso_country_code",
                "ISO Country Code",
                "string",
                "2026-04-08T00:00:00Z",
                "2026-04-08T00:00:00Z",
            ],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_enum_set_versions (
                enum_set_version_id,
                enum_set_contract_id,
                enum_version,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                "enum_version_1",
                "enum_contract_1",
                "2026-01",
                "2026-04-08T00:00:00Z",
                "2026-04-08T00:00:00Z",
            ],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_enum_set_values (
                enum_set_version_id,
                position,
                value_key,
                raw_value,
                label,
                aliases_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ["enum_version_1", 1, "country_us", "US", "United States", '["USA"]'],
        )
        self.conn.execute(
            """
            INSERT INTO semantic_enum_set_values (
                enum_set_version_id,
                position,
                value_key,
                raw_value,
                label,
                aliases_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ["enum_version_1", 2, "country_cn", "CN", "China", "[]"],
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_dimension_contracts (
                    dimension_contract_id,
                    dimension_ref,
                    display_name,
                    description,
                    dimension_contract_version,
                    structure_kind,
                    semantic_role,
                    value_type,
                    domain_kind,
                    enum_set_ref,
                    enum_version,
                    hierarchy_type,
                    parent_dimension_ref,
                    supports_grouping,
                    required_time_anchor_ref,
                    dimension_payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "dim_contract_2",
                    "dimension.open_country",
                    "Open Country",
                    "Invalid open domain config",
                    "dimension.v1",
                    "flat",
                    "category",
                    "string",
                    "open",
                    "enum.iso_country_code",
                    "2026-01",
                    None,
                    None,
                    1,
                    None,
                    "{}",
                    "2026-04-08T00:00:00Z",
                    "2026-04-08T00:00:00Z",
                ],
            )
            self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_dimension_contracts (
                    dimension_contract_id,
                    dimension_ref,
                    display_name,
                    description,
                    dimension_contract_version,
                    structure_kind,
                    semantic_role,
                    value_type,
                    domain_kind,
                    enum_set_ref,
                    enum_version,
                    hierarchy_type,
                    parent_dimension_ref,
                    supports_grouping,
                    required_time_anchor_ref,
                    dimension_payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "dim_contract_3",
                    "dimension.region",
                    "Region",
                    "Missing hierarchy type for parent dimension",
                    "dimension.v1",
                    "hierarchical",
                    "category",
                    "string",
                    "open",
                    None,
                    None,
                    None,
                    "dimension.country",
                    1,
                    None,
                    "{}",
                    "2026-04-08T00:00:00Z",
                    "2026-04-08T00:00:00Z",
                ],
            )
            self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_time_objects (
                    time_contract_id,
                    time_ref,
                    display_name,
                    description,
                    time_contract_version,
                    business_anchor,
                    measurement,
                    operational_support,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "time_contract_2",
                    "time.invalid",
                    "Invalid Time",
                    "No semantic role selected",
                    "time.v1",
                    0,
                    0,
                    0,
                    "2026-04-08T00:00:00Z",
                    "2026-04-08T00:00:00Z",
                ],
            )
            self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO semantic_enum_set_values (
                    enum_set_version_id,
                    position,
                    value_key,
                    raw_value,
                    label,
                    aliases_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ["enum_version_1", 3, "country_us_alias", "US", "Duplicate raw value", "[]"],
            )
            self.conn.commit()

    def test_compiler_compatibility_profile_constraints(self) -> None:
        """Test compiler_compatibility_profiles DDL constraints.

        Validates:
        - Valid profile inserts for all three subject_kind/profile_kind combinations
        - profile_ref prefix validation (compiler_profile.*)
        - subject_ref prefix matching subject_kind
        - Illegal subject_kind/profile_kind combinations
        - Payload exclusivity (requirement vs capability JSON fields)
        - schema_version, revision, status enum constraints
        - profile_ref uniqueness
        """

        def _insert_profile(
            profile_id: str,
            profile_ref: str,
            profile_kind: str,
            subject_kind: str,
            subject_ref: str,
            requirement_json: str = "{}",
            capability_json: str = "{}",
            status: str = "draft",
            revision: int = 1,
            subject_revision: int | None = None,
            schema_version: str = "v1",
        ) -> None:
            """Helper to insert a profile with default values."""
            self.conn.execute(
                """
                INSERT INTO compiler_compatibility_profiles (
                    profile_id, profile_ref, profile_kind, schema_version,
                    subject_kind, subject_ref, subject_revision, requirement_json, capability_json,
                    status, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    profile_id,
                    profile_ref,
                    profile_kind,
                    schema_version,
                    subject_kind,
                    subject_ref,
                    subject_revision,
                    requirement_json,
                    capability_json,
                    status,
                    revision,
                    "2026-04-08T00:00:00Z",
                    "2026-04-08T00:00:00Z",
                ],
            )
            self.conn.commit()

        # Valid profiles - all three legal combinations
        _insert_profile(
            "profile_1",
            "compiler_profile.conversion_rate_requirement",
            "requirement",
            "metric",
            "metric.conversion_rate",
            requirement_json='{"contract_modes": ["context_provider"], "context_kinds": ["experiment_split"]}',
        )
        _insert_profile(
            "profile_2",
            "compiler_profile.experiment_exp123_capability",
            "capability",
            "process",
            "process.exp_123",
            capability_json='{"inferential_ready": true, "supported_sample_summaries": ["rate_sample_summary"]}',
        )
        _insert_profile(
            "profile_3",
            "compiler_profile.binding_user_activity_capability",
            "capability",
            "binding",
            "binding.user_activity",
            capability_json='{"inferential_ready": false}',
        )

        # Invalid profile_ref prefix
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_4",
                "profile.invalid_prefix",
                "requirement",
                "metric",
                "metric.test",
                requirement_json='{"contract_modes": ["context_provider"]}',
            )

        # Invalid subject_ref prefix for metric
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_5",
                "compiler_profile.metric_wrong_ref",
                "requirement",
                "metric",
                "process.wrong_ref",
                requirement_json='{"contract_modes": ["context_provider"]}',
            )

        # Invalid subject_ref prefix for process
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_6",
                "compiler_profile.process_wrong_ref",
                "capability",
                "process",
                "metric.wrong_ref",
                capability_json='{"inferential_ready": true}',
            )

        # Invalid subject_ref prefix for binding
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_7",
                "compiler_profile.binding_wrong_ref",
                "capability",
                "binding",
                "metric.wrong_ref",
                capability_json='{"inferential_ready": false}',
            )

        # Illegal combination: metric + capability
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_8",
                "compiler_profile.metric_illegal_combo",
                "capability",
                "metric",
                "metric.test_illegal",
                capability_json='{"inferential_ready": true}',
            )

        # Illegal combination: process + requirement
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_9",
                "compiler_profile.process_illegal_combo",
                "requirement",
                "process",
                "process.test_illegal",
                requirement_json='{"contract_modes": ["context_provider"]}',
            )

        # Empty requirement_json for requirement profile
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_10",
                "compiler_profile.empty_requirement",
                "requirement",
                "metric",
                "metric.empty_req",
            )

        # Empty capability_json for capability profile
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_11",
                "compiler_profile.empty_capability",
                "capability",
                "process",
                "process.empty_cap",
            )

        # Both payloads populated (payload exclusivity violation)
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_12",
                "compiler_profile.both_payloads",
                "requirement",
                "metric",
                "metric.both_payloads",
                requirement_json='{"contract_modes": ["context_provider"]}',
                capability_json='{"inferential_ready": true}',
            )

        # Invalid schema_version
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_13",
                "compiler_profile.invalid_schema_version",
                "requirement",
                "metric",
                "metric.invalid_schema",
                requirement_json='{"contract_modes": ["context_provider"]}',
                schema_version="v2",
            )

        # Invalid revision (revision = 0)
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_14",
                "compiler_profile.invalid_revision",
                "requirement",
                "metric",
                "metric.invalid_revision",
                requirement_json='{"contract_modes": ["context_provider"]}',
                revision=0,
            )

        # Invalid status
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_15",
                "compiler_profile.invalid_status",
                "requirement",
                "metric",
                "metric.invalid_status",
                requirement_json='{"contract_modes": ["context_provider"]}',
                status="active",
            )

        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_16",
                "compiler_profile.missing_subject_revision",
                "requirement",
                "metric",
                "metric.published_missing_subject_revision",
                requirement_json='{"contract_modes": ["context_provider"]}',
                status="published",
            )

        # Duplicate profile_ref (violates UNIQUE constraint)
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_profile(
                "profile_16",
                "compiler_profile.conversion_rate_requirement",  # Same as profile_1
                "requirement",
                "metric",
                "metric.duplicate_ref",
                requirement_json='{"contract_modes": ["context_provider"]}',
            )


if __name__ == "__main__":
    unittest.main()
