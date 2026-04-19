"""Tests for enum set semantic models."""

import pytest
from pydantic import ValidationError

from app.api.models.enum_set import EnumSetCreateRequest, EnumSetHeader, EnumSetVersionSpec


class TestEnumSetCreateRequest:
    """Tests for enum set cross-field validation."""

    def test_accepts_matching_integer_raw_values(self):
        request = EnumSetCreateRequest(
            header=EnumSetHeader(
                enum_set_ref="enum.age_bucket",
                value_type="integer",
            ),
            display_name="Age Bucket",
            versions=[
                EnumSetVersionSpec(
                    enum_version="v1",
                    values=[
                        {"value_key": "young", "raw_value": 18, "label": "18"},
                        {"value_key": "adult", "raw_value": 35, "label": "35"},
                    ],
                )
            ],
        )

        assert request.header.value_type == "integer"

    @pytest.mark.parametrize(
        ("value_type", "raw_value"),
        [
            ("integer", "18"),
            ("integer", True),
            ("number", "1.5"),
            ("boolean", 1),
            ("string", 123),
        ],
    )
    def test_rejects_raw_values_with_type_mismatch(self, value_type, raw_value):
        with pytest.raises(
            ValidationError,
            match=rf"must match header.value_type '{value_type}'",
        ):
            EnumSetCreateRequest(
                header=EnumSetHeader(
                    enum_set_ref="enum.test_values",
                    value_type=value_type,
                ),
                display_name="Test Values",
                versions=[
                    EnumSetVersionSpec(
                        enum_version="v1",
                        values=[
                            {
                                "value_key": "bad_value",
                                "raw_value": raw_value,
                                "label": "Bad Value",
                            }
                        ],
                    )
                ],
            )
