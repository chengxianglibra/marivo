from __future__ import annotations

import unittest

from marivo.analysis_core import (
    COMPOSITE_STEP_TYPES,
    PRIMITIVE_STEP_TYPES,
    STEP_TAXONOMY,
    SUPPORTED_STEP_TYPES,
    step_category_for,
)


class StepTaxonomyTests(unittest.TestCase):
    def test_supported_step_types_are_split_between_primitive_and_composite(self) -> None:
        self.assertEqual(
            set(SUPPORTED_STEP_TYPES),
            set(PRIMITIVE_STEP_TYPES).union(COMPOSITE_STEP_TYPES),
        )
        self.assertTrue(set(PRIMITIVE_STEP_TYPES).isdisjoint(COMPOSITE_STEP_TYPES))

    def test_step_category_flags_are_explicit(self) -> None:
        self.assertEqual(step_category_for("metric_query"), "primitive")
        self.assertEqual(step_category_for("attribute_change"), "primitive")

    def test_taxonomy_entries_include_descriptions(self) -> None:
        for step_type in SUPPORTED_STEP_TYPES:
            self.assertIn(step_type, STEP_TAXONOMY)
            self.assertTrue(STEP_TAXONOMY[step_type]["description"])


if __name__ == "__main__":
    unittest.main()
