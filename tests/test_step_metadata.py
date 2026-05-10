from __future__ import annotations

import unittest

from marivo.core.semantic.compiler import CompiledQuery
from marivo.runtime.runtime import MarivoRuntime
from marivo.runtime.semantic_ops import build_step_semantic_metadata

_VALID_SOURCE_LINEAGE = {
    "table_fqn": "calendar",
    "calendar_version": "cn_2026q2_v1",
}


def _make_metadata_only_service() -> MarivoRuntime:
    from unittest.mock import MagicMock

    from marivo.core.engine import CoreEngine
    from marivo.runtime.ports import RuntimePorts

    ports = MagicMock(spec=RuntimePorts)
    core = CoreEngine()
    runtime = MarivoRuntime(ports, core)
    return runtime


class StepMetadataCalendarPolicyBindingUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _make_metadata_only_service()

    def test_build_step_semantic_metadata_accepts_valid_flat_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": _VALID_SOURCE_LINEAGE,
                }
            },
        )

        semantic_metadata = build_step_semantic_metadata(self.service, compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        binding = semantic_metadata["compile_context"]["calendar_policy_binding"]
        self.assertEqual(binding["source_lineage"], _VALID_SOURCE_LINEAGE)

    def test_build_step_semantic_metadata_rejects_missing_table_fqn(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        "calendar_version": "cn_2026q2_v1",
                    },
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError, "resolved_calendar_alignment source_lineage missing table_fqn"
        ):
            build_step_semantic_metadata(self.service, compiled)

    def test_build_step_semantic_metadata_rejects_missing_calendar_version(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        "table_fqn": "calendar",
                    },
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError, "resolved_calendar_alignment source_lineage missing calendar_version"
        ):
            build_step_semantic_metadata(self.service, compiled)

    def test_build_step_semantic_metadata_normalizes_source_lineage_to_required_fields(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        "table_fqn": "calendar",
                        "calendar_version": "cn_2026q2_v1",
                        "extra_key": "ignored",
                    },
                }
            },
        )

        semantic_metadata = build_step_semantic_metadata(self.service, compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        binding = semantic_metadata["compile_context"]["calendar_policy_binding"]
        self.assertEqual(binding["source_lineage"], _VALID_SOURCE_LINEAGE)
        self.assertNotIn("extra_key", binding["source_lineage"])
