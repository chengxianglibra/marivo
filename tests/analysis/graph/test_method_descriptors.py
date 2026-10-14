"""The descriptor table is the single declaration of every connected method."""

from __future__ import annotations

from typing import get_args

from marivo.analysis.methods.descriptor import BY_NAME, DESCRIPTORS, MethodName
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.methods.semantics import CONNECTED_METHODS


def test_every_method_name_has_exactly_one_descriptor() -> None:
    names = [item.name for item in DESCRIPTORS]

    assert len(names) == len(set(names))
    assert set(names) == set(get_args(MethodName))
    assert [item.key.name for item in CONNECTED_METHODS] == names


def test_registry_registers_every_descriptor() -> None:
    registered = {item.semantics.key.name for item in REGISTRY.registrations}

    assert registered == set(BY_NAME)


def test_original_state_method_only_on_state_rollups() -> None:
    owners = {item.name for item in DESCRIPTORS if item.original_state_method is not None}

    assert owners == {item.name for item in DESCRIPTORS if item.rule == "original_reduce@v1"} - {
        "metric.ratio"
    }
    assert all(item.local_laws == ("L8", "L9") for item in DESCRIPTORS if item.name in owners)


def test_every_parameter_selector_is_unambiguous() -> None:
    seen: dict[tuple[type[object], str | None, str], str] = {}
    for descriptor in DESCRIPTORS:
        for selector in descriptor.selectors:
            for value in selector.values or ("",):
                key = (selector.params, selector.attribute, value)
                if selector.attribute is None and selector.params.__name__ == "ObserveMetric":
                    # ObserveMetric uses explicit precedence in method_name_for.
                    continue
                assert key not in seen, (descriptor.name, seen.get(key))
                seen[key] = descriptor.name
