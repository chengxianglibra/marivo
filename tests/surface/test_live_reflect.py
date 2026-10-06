"""Tests for neutral reflection helpers used by live-surface collectors."""

from __future__ import annotations

from marivo.introspection.live.reflect import return_annotation_mismatch


class CoverageFrame:
    """Test-only concrete output family."""


class ComponentFrame:
    """Test-only mismatched output family."""


def _coverage() -> CoverageFrame:
    return CoverageFrame()


def _nullable_coverage() -> CoverageFrame | None:
    return None


def _component() -> ComponentFrame:
    return ComponentFrame()


def _unannotated() -> object:
    return None


def test_return_annotation_check_accepts_matching_concrete_outputs() -> None:
    assert (
        return_annotation_mismatch(_coverage, expected_family="CoverageFrame", nullable=False)
        is None
    )
    assert (
        return_annotation_mismatch(
            _nullable_coverage,
            expected_family="CoverageFrame",
            nullable=True,
        )
        is None
    )


def test_return_annotation_check_rejects_nullable_and_family_drift() -> None:
    missing_none = return_annotation_mismatch(
        _coverage,
        expected_family="CoverageFrame",
        nullable=True,
    )
    extra_none = return_annotation_mismatch(
        _nullable_coverage,
        expected_family="CoverageFrame",
        nullable=False,
    )
    wrong_family = return_annotation_mismatch(
        _component,
        expected_family="CoverageFrame",
        nullable=False,
    )

    assert missing_none is not None and "CoverageFrame | None" in missing_none
    assert extra_none is not None and "callable annotation is CoverageFrame | None" in extra_none
    assert wrong_family is not None and "callable annotation is ComponentFrame" in wrong_family


def test_return_annotation_check_accepts_runtime_type_annotations() -> None:
    def runtime_annotation() -> object:
        return CoverageFrame()

    runtime_annotation.__annotations__["return"] = CoverageFrame

    assert (
        return_annotation_mismatch(
            runtime_annotation,
            expected_family="CoverageFrame",
            nullable=False,
        )
        is None
    )


def test_return_annotation_check_skips_receiver_dependent_outputs() -> None:
    assert (
        return_annotation_mismatch(
            _unannotated,
            expected_family=object(),
            nullable=False,
        )
        is None
    )
