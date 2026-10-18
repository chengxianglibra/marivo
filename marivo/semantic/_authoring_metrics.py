"""Structured reauthoring errors for retired Metric factories.

Internal module: public symbols are re-exported from
``marivo.semantic.authoring``.
"""

from __future__ import annotations

from typing import NoReturn

from marivo.semantic._authoring_context import (
    _register_authoring_file,
)
from marivo.semantic._authoring_retired import retired_factory


def grain_to_date(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired grain_to_date declaration."""
    retired_factory("grain_to_date", kwargs)


def trailing(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired trailing declaration."""
    retired_factory("trailing", kwargs)


def ratio(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired ratio declaration."""
    retired_factory("ratio", kwargs)


def linear(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired linear declaration."""
    retired_factory("linear", kwargs)


def cumulative(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired cumulative declaration."""
    retired_factory("cumulative", kwargs)


_register_authoring_file(__file__)
