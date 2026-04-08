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
        ]
        for table in tables:
            row = self.conn.execute(
                "SELECT COUNT(*) AS cnt FROM sqlite_master WHERE type = 'table' AND name = ?",
                [table],
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["cnt"], 1, f"Table {table} should exist")

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
                additivity,
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
                "non_additive",
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
                    additivity,
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
                    "additive",
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


if __name__ == "__main__":
    unittest.main()
