from __future__ import annotations

import importlib
import sys

import pytest


def test_primitives_has_no_io_imports() -> None:
    """Verify app.core.intent.primitives imports nothing from I/O modules."""
    # Unload if previously imported to get a fresh check
    mod_name = "app.core.intent.primitives"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    mod = importlib.import_module(mod_name)

    # Standard I/O modules that core must not depend on
    forbidden = {
        "sqlalchemy",
        "asyncio",
        "httpx",
        "aiohttp",
        "requests",
        "app.service",
        "app.infrastructure",
        "app.analysis_core",
    }

    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        module = getattr(obj, "__module__", None)
        if module and any(module.startswith(f) or module == f for f in forbidden):
            pytest.fail(
                f"app.core.intent.primitives references I/O module {module!r} "
                f"via attribute {attr_name!r}"
            )


def test_intent_taxonomy_keys_match_types() -> None:
    from app.core.intent.primitives import (
        ATOMIC_INTENT_TYPES,
        DERIVED_INTENT_TYPES,
        INTENT_TAXONOMY,
        SUPPORTED_INTENT_TYPES,
    )

    assert set(ATOMIC_INTENT_TYPES) | set(DERIVED_INTENT_TYPES) == set(INTENT_TAXONOMY)
    assert set(SUPPORTED_INTENT_TYPES) == set(INTENT_TAXONOMY)
    assert len(SUPPORTED_INTENT_TYPES) == len(INTENT_TAXONOMY)


def test_step_category_for_known_types() -> None:
    from app.core.intent.primitives import step_category_for

    assert step_category_for("metric_query") == "primitive"
    assert step_category_for("profile_table") == "primitive"
    assert step_category_for("unknown_type") == "primitive"  # default


def test_step_category_for_reflects_taxonomy() -> None:
    from app.core.intent.primitives import STEP_TAXONOMY, step_category_for

    for step_type, meta in STEP_TAXONOMY.items():
        assert step_category_for(step_type) == meta["category"]


def test_primitive_step_types_non_empty() -> None:
    from app.core.intent.primitives import PRIMITIVE_STEP_TYPES

    assert len(PRIMITIVE_STEP_TYPES) > 0


def test_atomic_intent_types_non_empty() -> None:
    from app.core.intent.primitives import ATOMIC_INTENT_TYPES

    assert len(ATOMIC_INTENT_TYPES) > 0
