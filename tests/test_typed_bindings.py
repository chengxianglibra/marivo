"""Tests for typed binding DDL and constraints.

Task 1.3: Typed Binding 存储模型落地
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.storage.sqlite_metadata import SQLiteMetadataStore


class TypedBindingDDLTests(unittest.TestCase):
    """Test typed binding table creation and constraints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_typed_bindings.sqlite"
        cls.metadata = SQLiteMetadataStore(cls.db_path)
        cls.metadata.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _insert_binding(self, **overrides) -> str:
        """Helper to insert a typed binding."""
        import uuid

        binding_id = f"bind_{uuid.uuid4().hex[:24]}"
        defaults = {
            "binding_id": binding_id,
            "binding_ref": f"binding.test_{uuid.uuid4().hex[:8]}",
            "binding_scope": "entity",
            "bound_object_ref": "entity.test",
            "binding_contract_version": "binding.v1",
            "display_name": "Test Binding",
            "description": "Test Description",
            "status": "draft",
            "revision": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        defaults.update(overrides)
        self.metadata.execute(
            """
            INSERT INTO typed_bindings (
                binding_id, binding_ref, binding_scope, bound_object_ref,
                binding_contract_version, display_name, description,
                status, revision, created_at, updated_at
            ) VALUES (
                :binding_id, :binding_ref, :binding_scope, :bound_object_ref,
                :binding_contract_version, :display_name, :description,
                :status, :revision, :created_at, :updated_at
            )
            """,
            defaults,
        )
        return binding_id

    def _insert_carrier_binding(self, binding_id: str, **overrides) -> str:
        """Helper to insert a carrier binding."""
        import uuid

        carrier_id = f"carb_{uuid.uuid4().hex[:24]}"
        defaults = {
            "carrier_binding_id": carrier_id,
            "binding_id": binding_id,
            "binding_key": "primary",
            "source_object_ref": None,
            "carrier_kind": "table",
            "carrier_locator": "warehouse.test_table",
            "binding_role": "primary",
            "semantic_role_ref": None,
            "grain_ref": None,
            "primary_entity_ref": None,
            "row_filter_refs_json": "[]",
            "freshness_policy_ref": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        defaults.update(overrides)
        self.metadata.execute(
            """
            INSERT INTO carrier_bindings (
                carrier_binding_id, binding_id, binding_key, source_object_ref,
                carrier_kind, carrier_locator, binding_role, semantic_role_ref,
                grain_ref, primary_entity_ref, row_filter_refs_json,
                freshness_policy_ref, created_at, updated_at
            ) VALUES (
                :carrier_binding_id, :binding_id, :binding_key, :source_object_ref,
                :carrier_kind, :carrier_locator, :binding_role, :semantic_role_ref,
                :grain_ref, :primary_entity_ref, :row_filter_refs_json,
                :freshness_policy_ref, :created_at, :updated_at
            )
            """,
            defaults,
        )
        return carrier_id


class TypedBindingsTableTests(TypedBindingDDLTests):
    """Tests for typed_bindings table."""

    def test_table_exists(self) -> None:
        """typed_bindings table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='typed_bindings'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "typed_bindings")

    def test_insert_valid_binding(self) -> None:
        """Should insert a valid typed binding."""
        binding_id = self._insert_binding(
            binding_ref="binding.user_identity",
            binding_scope="entity",
            bound_object_ref="entity.user",
        )
        result = self.metadata.query_one(
            "SELECT * FROM typed_bindings WHERE binding_id = ?", [binding_id]
        )
        self.assertEqual(result["binding_ref"], "binding.user_identity")
        self.assertEqual(result["binding_scope"], "entity")

    def test_binding_ref_prefix_constraint(self) -> None:
        """binding_ref must start with 'binding.'."""
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_binding(binding_ref="invalid_ref")

    def test_binding_scope_enum_constraint(self) -> None:
        """binding_scope must be one of the allowed values."""
        # Valid values
        for scope in ["entity", "process_object", "metric"]:
            binding_id = self._insert_binding(binding_scope=scope)
            self.assertIsNotNone(binding_id)

        # Invalid value
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_binding(binding_scope="invalid_scope")

    def test_status_enum_constraint(self) -> None:
        """status must be one of the allowed values."""
        # Valid values
        for status in ["draft", "published", "deprecated"]:
            binding_id = self._insert_binding(status=status)
            self.assertIsNotNone(binding_id)

        # Invalid value
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_binding(status="invalid_status")

    def test_revision_minimum_constraint(self) -> None:
        """revision must be >= 1."""
        # Valid values
        binding_id = self._insert_binding(revision=1)
        self.assertIsNotNone(binding_id)

        # Invalid value
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_binding(revision=0)

    def test_binding_ref_unique(self) -> None:
        """binding_ref must be unique."""
        self._insert_binding(binding_ref="binding.unique_test")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_binding(binding_ref="binding.unique_test")

    def test_binding_contract_version_prefix_constraint(self) -> None:
        """binding_contract_version must start with 'binding.'."""
        # Valid
        binding_id = self._insert_binding(
            binding_ref="binding.version_test", binding_contract_version="binding.v1"
        )
        self.assertIsNotNone(binding_id)

        # Invalid
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_binding(
                binding_ref="binding.version_invalid", binding_contract_version="v1"
            )


class CarrierBindingsTableTests(TypedBindingDDLTests):
    """Tests for carrier_bindings table."""

    def test_table_exists(self) -> None:
        """carrier_bindings table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='carrier_bindings'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "carrier_bindings")

    def test_insert_valid_carrier(self) -> None:
        """Should insert a valid carrier binding."""
        binding_id = self._insert_binding(binding_ref="binding.carrier_test")
        carrier_id = self._insert_carrier_binding(binding_id)
        result = self.metadata.query_one(
            "SELECT * FROM carrier_bindings WHERE carrier_binding_id = ?", [carrier_id]
        )
        self.assertEqual(result["binding_key"], "primary")
        self.assertEqual(result["carrier_kind"], "table")

    def test_carrier_kind_enum_constraint(self) -> None:
        """carrier_kind must be 'table' or 'view'."""
        binding_id = self._insert_binding(binding_ref="binding.carrier_kind_test")

        # Valid values
        for kind in ["table", "view"]:
            carrier_id = self._insert_carrier_binding(
                binding_id, carrier_kind=kind, binding_key=f"carrier_{kind}"
            )
            self.assertIsNotNone(carrier_id)

        # Invalid value
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_carrier_binding(
                binding_id, carrier_kind="invalid", binding_key="invalid_carrier"
            )

    def test_binding_role_enum_constraint(self) -> None:
        """binding_role must be 'primary' or 'auxiliary'."""
        binding_id = self._insert_binding(binding_ref="binding.role_test")

        # Valid values
        for role in ["primary", "auxiliary"]:
            carrier_id = self._insert_carrier_binding(
                binding_id, binding_role=role, binding_key=f"role_{role}"
            )
            self.assertIsNotNone(carrier_id)

        # Invalid value
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_carrier_binding(
                binding_id, binding_role="invalid", binding_key="invalid_role"
            )

    def test_binding_key_unique_per_binding(self) -> None:
        """binding_key must be unique within a binding."""
        binding_id = self._insert_binding(binding_ref="binding.unique_key_test")
        self._insert_carrier_binding(binding_id, binding_key="primary")

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_carrier_binding(binding_id, binding_key="primary")

    def test_foreign_key_to_typed_bindings(self) -> None:
        """carrier_bindings must reference a valid typed_binding."""
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_carrier_binding("nonexistent_binding_id")

    def test_primary_entity_ref_prefix_constraint(self) -> None:
        """primary_entity_ref must start with 'entity.' if provided."""
        binding_id = self._insert_binding(binding_ref="binding.entity_ref_test")

        # Valid
        carrier_id = self._insert_carrier_binding(
            binding_id, primary_entity_ref="entity.user", binding_key="valid_entity"
        )
        self.assertIsNotNone(carrier_id)

        # Invalid
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_carrier_binding(
                binding_id, primary_entity_ref="invalid_ref", binding_key="invalid_entity"
            )

    def test_cascade_delete(self) -> None:
        """Deleting a typed_binding should cascade to carrier_bindings."""
        binding_id = self._insert_binding(binding_ref="binding.cascade_test")
        carrier_id = self._insert_carrier_binding(binding_id)

        # Delete the binding
        self.metadata.execute("DELETE FROM typed_bindings WHERE binding_id = ?", [binding_id])

        # Carrier should be deleted
        result = self.metadata.query_one(
            "SELECT * FROM carrier_bindings WHERE carrier_binding_id = ?", [carrier_id]
        )
        self.assertIsNone(result)


class CarrierFieldSurfacesTableTests(TypedBindingDDLTests):
    """Tests for carrier_field_surfaces table."""

    def test_table_exists(self) -> None:
        """carrier_field_surfaces table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='carrier_field_surfaces'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "carrier_field_surfaces")

    def test_insert_valid_field_surface(self) -> None:
        """Should insert a valid field surface."""
        binding_id = self._insert_binding(binding_ref="binding.field_surface_test")
        carrier_id = self._insert_carrier_binding(binding_id)

        self.metadata.execute(
            """
            INSERT INTO carrier_field_surfaces (
                carrier_binding_id, position, surface_ref, physical_name, field_type
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [carrier_id, 1, "field.user_id", "user_id", "TEXT"],
        )

        result = self.metadata.query_one(
            "SELECT * FROM carrier_field_surfaces WHERE carrier_binding_id = ?", [carrier_id]
        )
        self.assertEqual(result["surface_ref"], "field.user_id")
        self.assertEqual(result["physical_name"], "user_id")

    def test_position_must_be_positive(self) -> None:
        """position must be > 0."""
        binding_id = self._insert_binding(binding_ref="binding.field_pos_test")
        carrier_id = self._insert_carrier_binding(binding_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.metadata.execute(
                """
                INSERT INTO carrier_field_surfaces (
                    carrier_binding_id, position, surface_ref, physical_name
                ) VALUES (?, ?, ?, ?)
                """,
                [carrier_id, 0, "field.test", "test"],
            )

    def test_surface_ref_unique_per_carrier(self) -> None:
        """surface_ref must be unique within a carrier_binding."""
        binding_id = self._insert_binding(binding_ref="binding.field_unique_test")
        carrier_id = self._insert_carrier_binding(binding_id)

        self.metadata.execute(
            """
            INSERT INTO carrier_field_surfaces (
                carrier_binding_id, position, surface_ref, physical_name
            ) VALUES (?, ?, ?, ?)
            """,
            [carrier_id, 1, "field.unique", "col1"],
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.metadata.execute(
                """
                INSERT INTO carrier_field_surfaces (
                    carrier_binding_id, position, surface_ref, physical_name
                ) VALUES (?, ?, ?, ?)
                """,
                [carrier_id, 2, "field.unique", "col2"],
            )

    def test_surface_ref_prefix_constraint(self) -> None:
        """surface_ref must start with 'field.'."""
        binding_id = self._insert_binding(binding_ref="binding.field_prefix_test")
        carrier_id = self._insert_carrier_binding(binding_id)

        # Invalid prefix
        with self.assertRaises(sqlite3.IntegrityError):
            self.metadata.execute(
                """
                INSERT INTO carrier_field_surfaces (
                    carrier_binding_id, position, surface_ref, physical_name
                ) VALUES (?, ?, ?, ?)
                """,
                [carrier_id, 1, "invalid.user_id", "user_id"],
            )


class CarrierTimeSurfacesTableTests(TypedBindingDDLTests):
    """Tests for carrier_time_surfaces table."""

    def test_table_exists(self) -> None:
        """carrier_time_surfaces table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='carrier_time_surfaces'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "carrier_time_surfaces")

    def test_time_granularity_enum_constraint(self) -> None:
        """time_granularity must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.time_gran_test")
        carrier_id = self._insert_carrier_binding(binding_id)

        # Valid values
        for i, granularity in enumerate(["second", "minute", "hour", "day"], start=1):
            self.metadata.execute(
                """
                INSERT INTO carrier_time_surfaces (
                    carrier_binding_id, position, surface_ref, physical_name, time_granularity
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [carrier_id, i, f"time_surface.{granularity}", "ts", granularity],
            )

        # Invalid value
        with self.assertRaises(sqlite3.IntegrityError):
            self.metadata.execute(
                """
                INSERT INTO carrier_time_surfaces (
                    carrier_binding_id, position, surface_ref, physical_name, time_granularity
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [carrier_id, 5, "time_surface.invalid", "ts", "invalid"],
            )

    def test_surface_ref_prefix_constraint(self) -> None:
        """surface_ref must start with 'time_surface.'."""
        binding_id = self._insert_binding(binding_ref="binding.time_prefix_test")
        carrier_id = self._insert_carrier_binding(binding_id)

        # Invalid prefix
        with self.assertRaises(sqlite3.IntegrityError):
            self.metadata.execute(
                """
                INSERT INTO carrier_time_surfaces (
                    carrier_binding_id, position, surface_ref, physical_name
                ) VALUES (?, ?, ?, ?)
                """,
                [carrier_id, 1, "field.event_time", "event_time"],
            )


class FieldBindingsTableTests(TypedBindingDDLTests):
    """Tests for field_bindings table."""

    def test_table_exists(self) -> None:
        """field_bindings table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='field_bindings'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "field_bindings")

    def _insert_field_binding(self, binding_id: str, **overrides) -> str:
        """Helper to insert a field binding."""
        import uuid

        field_binding_id = f"fldb_{uuid.uuid4().hex[:24]}"
        defaults = {
            "field_binding_id": field_binding_id,
            "binding_id": binding_id,
            "carrier_binding_key": "primary",
            "target_kind": "identity_key",
            "target_key": "key.user_id",
            "context_ref": None,
            "semantic_ref": "key.user_id",
            "surface_ref": "field.user_id",
            "field_type_ref": None,
            "nullability_policy": None,
            "repeated_value_policy": None,
        }
        defaults.update(overrides)
        self.metadata.execute(
            """
            INSERT INTO field_bindings (
                field_binding_id, binding_id, carrier_binding_key, target_kind,
                target_key, context_ref, semantic_ref, surface_ref, field_type_ref,
                nullability_policy, repeated_value_policy
            ) VALUES (
                :field_binding_id, :binding_id, :carrier_binding_key, :target_kind,
                :target_key, :context_ref, :semantic_ref, :surface_ref, :field_type_ref,
                :nullability_policy, :repeated_value_policy
            )
            """,
            defaults,
        )
        return field_binding_id

    def test_target_kind_enum_constraint(self) -> None:
        """target_kind must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.target_kind_test")

        valid_kinds = [
            "identity_key",
            "primary_time",
            "stable_descriptor",
            "population_subject",
            "analysis_window_anchor",
            "process_context",
            "metric_input",
        ]

        for kind in valid_kinds:
            field_id = self._insert_field_binding(
                binding_id, target_kind=kind, target_key=f"key.{kind}"
            )
            self.assertIsNotNone(field_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_field_binding(binding_id, target_kind="invalid_kind")

    def test_nullability_policy_enum_constraint(self) -> None:
        """nullability_policy must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.null_policy_test")

        for policy in ["reject", "allow", "impute"]:
            field_id = self._insert_field_binding(
                binding_id, target_key=f"key.{policy}", nullability_policy=policy
            )
            self.assertIsNotNone(field_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_field_binding(
                binding_id, target_key="key.invalid", nullability_policy="invalid"
            )


class TimeBindingsTableTests(TypedBindingDDLTests):
    """Tests for time_bindings table."""

    def _insert_field_binding(self, binding_id: str, **overrides) -> str:
        import uuid

        field_binding_id = f"fldb_{uuid.uuid4().hex[:24]}"
        defaults = {
            "field_binding_id": field_binding_id,
            "binding_id": binding_id,
            "carrier_binding_key": "primary",
            "target_kind": "identity_key",
            "target_key": "key.user_id",
            "context_ref": None,
            "semantic_ref": "key.user_id",
            "surface_ref": "field.user_id",
            "field_type_ref": None,
            "nullability_policy": None,
            "repeated_value_policy": None,
        }
        defaults.update(overrides)
        self.metadata.execute(
            """
            INSERT INTO field_bindings (
                field_binding_id, binding_id, carrier_binding_key, target_kind,
                target_key, context_ref, semantic_ref, surface_ref, field_type_ref,
                nullability_policy, repeated_value_policy
            ) VALUES (
                :field_binding_id, :binding_id, :carrier_binding_key, :target_kind,
                :target_key, :context_ref, :semantic_ref, :surface_ref, :field_type_ref,
                :nullability_policy, :repeated_value_policy
            )
            """,
            defaults,
        )
        return field_binding_id

    def _insert_time_binding(self, binding_id: str, **overrides) -> str:
        import uuid

        time_binding_id = f"tbind_{uuid.uuid4().hex[:24]}"
        defaults = {
            "time_binding_id": time_binding_id,
            "binding_id": binding_id,
            "carrier_binding_key": "primary",
            "target_kind": "primary_time",
            "target_key": "time.event_time",
            "context_ref": None,
            "semantic_ref": "time.event_time",
            "resolution_kind": "timestamp_column",
            "timestamp_surface_ref": "field.event_time",
            "timestamp_format": None,
            "date_surface_ref": None,
            "date_format": None,
            "hour_surface_ref": None,
            "hour_format": None,
            "timezone_strategy": "session_consistent_naive",
        }
        defaults.update(overrides)
        self.metadata.execute(
            """
            INSERT INTO time_bindings (
                time_binding_id, binding_id, carrier_binding_key, target_kind, target_key,
                context_ref, semantic_ref, resolution_kind, timestamp_surface_ref,
                timestamp_format,
                date_surface_ref, date_format, hour_surface_ref, hour_format,
                timezone_strategy
            ) VALUES (
                :time_binding_id, :binding_id, :carrier_binding_key, :target_kind, :target_key,
                :context_ref, :semantic_ref, :resolution_kind, :timestamp_surface_ref,
                :timestamp_format,
                :date_surface_ref, :date_format, :hour_surface_ref, :hour_format,
                :timezone_strategy
            )
            """,
            defaults,
        )
        return time_binding_id

    def test_table_exists(self) -> None:
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='time_bindings'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "time_bindings")

    def test_resolution_kind_enum_constraint(self) -> None:
        binding_id = self._insert_binding(binding_ref="binding.time_binding_resolution")
        time_binding_id = self._insert_time_binding(binding_id, resolution_kind="date_column")
        self.assertIsNotNone(time_binding_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_time_binding(
                binding_id,
                resolution_kind="invalid_resolution",
                target_key="time.invalid_resolution",
                semantic_ref="time.invalid_resolution",
            )

    def test_timezone_strategy_constraint(self) -> None:
        binding_id = self._insert_binding(binding_ref="binding.time_binding_timezone")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_time_binding(
                binding_id,
                timezone_strategy="utc",
                target_key="time.utc_test",
                semantic_ref="time.utc_test",
            )

    def test_timestamp_format_constraint(self) -> None:
        binding_id = self._insert_binding(binding_ref="binding.time_binding_timestamp_format")
        self.assertIsNotNone(
            self._insert_time_binding(binding_id, timestamp_format="iso8601_t_naive")
        )
        self.assertIsNotNone(
            self._insert_time_binding(
                binding_id,
                target_key="time.compact_timestamp_format",
                semantic_ref="time.compact_timestamp_format",
                timestamp_format="YYYYMMDD hh:mm:ss",
            )
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_time_binding(
                binding_id,
                target_key="time.invalid_timestamp_format",
                semantic_ref="time.invalid_timestamp_format",
                timestamp_format="invalid_format",
            )

    def test_target_kind_constraint(self) -> None:
        binding_id = self._insert_binding(binding_ref="binding.time_binding_target")
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_time_binding(
                binding_id,
                target_kind="identity_key",
                target_key="key.user_id",
                semantic_ref="time.event_time",
            )

    def test_repeated_value_policy_enum_constraint(self) -> None:
        """repeated_value_policy must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.repeat_policy_test")

        for policy in ["take_first", "take_last", "aggregate", "explode"]:
            field_id = self._insert_field_binding(
                binding_id, target_key=f"key.{policy}", repeated_value_policy=policy
            )
            self.assertIsNotNone(field_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_field_binding(
                binding_id, target_key="key.invalid", repeated_value_policy="invalid"
            )

    def test_unique_constraint(self) -> None:
        """(binding_id, carrier_binding_key, target_kind, target_key) must be unique."""
        binding_id = self._insert_binding(binding_ref="binding.unique_field_test")
        self._insert_field_binding(
            binding_id,
            carrier_binding_key="primary",
            target_kind="identity_key",
            target_key="key.user_id",
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_field_binding(
                binding_id,
                carrier_binding_key="primary",
                target_kind="identity_key",
                target_key="key.user_id",
            )

    def test_surface_ref_prefix_constraint(self) -> None:
        """surface_ref must start with 'field.'."""
        binding_id = self._insert_binding(binding_ref="binding.surface_prefix_test")

        # Invalid prefix
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_field_binding(
                binding_id, surface_ref="invalid.user_id", target_key="key.invalid"
            )


class JoinRelationsTableTests(TypedBindingDDLTests):
    """Tests for join_relations table."""

    def test_table_exists(self) -> None:
        """join_relations table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='join_relations'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "join_relations")

    def _insert_join_relation(self, binding_id: str, **overrides) -> str:
        """Helper to insert a join relation."""
        import uuid

        relation_id = f"join_{uuid.uuid4().hex[:24]}"
        defaults = {
            "relation_id": relation_id,
            "binding_id": binding_id,
            "relation_key": "primary_to_aux",
            "left_binding_key": "primary",
            "right_binding_key": "auxiliary",
            "join_kind": None,
            "key_ref_pairs_json": "[]",
            "cardinality": None,
            "temporal_constraint_refs_json": "[]",
            "compatibility_rule_refs_json": "[]",
        }
        defaults.update(overrides)
        self.metadata.execute(
            """
            INSERT INTO join_relations (
                relation_id, binding_id, relation_key, left_binding_key, right_binding_key,
                join_kind, key_ref_pairs_json, cardinality,
                temporal_constraint_refs_json, compatibility_rule_refs_json
            ) VALUES (
                :relation_id, :binding_id, :relation_key, :left_binding_key, :right_binding_key,
                :join_kind, :key_ref_pairs_json, :cardinality,
                :temporal_constraint_refs_json, :compatibility_rule_refs_json
            )
            """,
            defaults,
        )
        return relation_id

    def test_join_kind_enum_constraint(self) -> None:
        """join_kind must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.join_kind_test")

        for kind in ["inner", "left", "semi", "anti"]:
            relation_id = self._insert_join_relation(
                binding_id, relation_key=f"join_{kind}", join_kind=kind
            )
            self.assertIsNotNone(relation_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_join_relation(binding_id, relation_key="join_invalid", join_kind="invalid")

    def test_cardinality_enum_constraint(self) -> None:
        """cardinality must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.cardinality_test")

        for card in ["one_to_one", "many_to_one", "one_to_many", "many_to_many"]:
            relation_id = self._insert_join_relation(
                binding_id, relation_key=f"card_{card}", cardinality=card
            )
            self.assertIsNotNone(relation_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_join_relation(
                binding_id, relation_key="card_invalid", cardinality="invalid"
            )

    def test_relation_key_unique_per_binding(self) -> None:
        """relation_key must be unique within a binding."""
        binding_id = self._insert_binding(binding_ref="binding.unique_relation_test")
        self._insert_join_relation(binding_id, relation_key="primary_to_aux")

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_join_relation(binding_id, relation_key="primary_to_aux")


class ConsumptionPoliciesTableTests(TypedBindingDDLTests):
    """Tests for consumption_policies table."""

    def test_table_exists(self) -> None:
        """consumption_policies table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='consumption_policies'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "consumption_policies")

    def _insert_consumption_policy(self, binding_id: str, **overrides) -> str:
        """Helper to insert a consumption policy."""
        import uuid

        policy_id = f"pol_{uuid.uuid4().hex[:24]}"
        defaults = {
            "policy_id": policy_id,
            "binding_id": binding_id,
            "policy_key": "late_arrival",
            "policy_type": "late_arrival_policy",
            "policy_target_path": "analysis_window",
            "anchor_ref": None,
            "grace_period_ref": None,
            "behavior": None,
        }
        defaults.update(overrides)
        self.metadata.execute(
            """
            INSERT INTO consumption_policies (
                policy_id, binding_id, policy_key, policy_type, policy_target_path,
                anchor_ref, grace_period_ref, behavior
            ) VALUES (
                :policy_id, :binding_id, :policy_key, :policy_type, :policy_target_path,
                :anchor_ref, :grace_period_ref, :behavior
            )
            """,
            defaults,
        )
        return policy_id

    def test_policy_type_enum_constraint(self) -> None:
        """policy_type must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.policy_type_test")

        for ptype in ["late_arrival_policy", "incomplete_window_policy"]:
            policy_id = self._insert_consumption_policy(
                binding_id, policy_key=f"policy_{ptype}", policy_type=ptype
            )
            self.assertIsNotNone(policy_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_consumption_policy(binding_id, policy_key="invalid", policy_type="invalid")

    def test_behavior_enum_constraint(self) -> None:
        """behavior must be one of the allowed values."""
        binding_id = self._insert_binding(binding_ref="binding.behavior_test")

        for behavior in ["exclude_open_subjects", "clip_to_window", "keep_partial"]:
            policy_id = self._insert_consumption_policy(
                binding_id, policy_key=f"behavior_{behavior}", behavior=behavior
            )
            self.assertIsNotNone(policy_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_consumption_policy(binding_id, policy_key="invalid", behavior="invalid")

    def test_policy_key_unique_per_binding(self) -> None:
        """policy_key must be unique within a binding."""
        binding_id = self._insert_binding(binding_ref="binding.unique_policy_test")
        self._insert_consumption_policy(binding_id, policy_key="late_arrival")

        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_consumption_policy(binding_id, policy_key="late_arrival")


class BindingImportsTableTests(TypedBindingDDLTests):
    """Tests for binding_imports table."""

    def test_table_exists(self) -> None:
        """binding_imports table should be created."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='binding_imports'"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "binding_imports")

    def test_insert_valid_import(self) -> None:
        """Should insert a valid binding import."""
        binding_id = self._insert_binding(binding_ref="binding.import_test")

        self.metadata.execute(
            """
            INSERT INTO binding_imports (
                binding_id, import_key, imported_binding_ref, required_ref_prefixes_json
            ) VALUES (?, ?, ?, ?)
            """,
            [binding_id, "user_identity", "binding.user_identity", '["identity_key"]'],
        )

        result = self.metadata.query_one(
            "SELECT * FROM binding_imports WHERE binding_id = ?", [binding_id]
        )
        self.assertEqual(result["import_key"], "user_identity")
        self.assertEqual(result["imported_binding_ref"], "binding.user_identity")

    def test_imported_binding_ref_prefix_constraint(self) -> None:
        """imported_binding_ref must start with 'binding.'."""
        binding_id = self._insert_binding(binding_ref="binding.import_prefix_test")

        with self.assertRaises(sqlite3.IntegrityError):
            self.metadata.execute(
                """
                INSERT INTO binding_imports (
                    binding_id, import_key, imported_binding_ref
                ) VALUES (?, ?, ?)
                """,
                [binding_id, "invalid_import", "invalid_ref"],
            )

    def test_import_key_unique_per_binding(self) -> None:
        """(binding_id, import_key) must be unique."""
        binding_id = self._insert_binding(binding_ref="binding.unique_import_test")

        self.metadata.execute(
            """
            INSERT INTO binding_imports (
                binding_id, import_key, imported_binding_ref
            ) VALUES (?, ?, ?)
            """,
            [binding_id, "user_identity", "binding.user_identity"],
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.metadata.execute(
                """
                INSERT INTO binding_imports (
                    binding_id, import_key, imported_binding_ref
                ) VALUES (?, ?, ?)
                """,
                [binding_id, "user_identity", "binding.other"],
            )


class SemanticMappingsRemovedTests(unittest.TestCase):
    """Verify that semantic_mappings table has been removed."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_no_mappings.sqlite"
        self.metadata = SQLiteMetadataStore(self.db_path)
        self.metadata.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_semantic_mappings_table_not_exists(self) -> None:
        """semantic_mappings table should not exist."""
        result = self.metadata.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_mappings'"
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
