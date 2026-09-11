"""Registry-owned progressive topology for private Dataset Help assembly."""

from __future__ import annotations

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    DisclosureProvider,
    FamilyInput,
    NavigationInput,
    TypeInput,
    invalid,
)


def navigation(providers: tuple[DisclosureProvider, ...]) -> tuple[NavigationInput, ...]:
    """Partition native descriptors once; no renderer or discovery shadow inventory."""
    buckets: dict[str, list[str]] = {
        "entry": [],
        "methods": [],
        "inputs": [],
        "artifacts": [],
        "evidence": [],
        "runtime": [],
    }
    groups: dict[str, list[str]] = {
        "filters": [],
        "forecast_models": [],
        "event_matching": [],
        "discovery": [],
        "session.namespace": [],
        "datasets": [],
    }
    nested = {
        member
        for p in providers
        for d in p.descriptors
        if isinstance(d, NavigationInput)
        for member in d.members
    }
    for provider in providers:
        for descriptor in provider.descriptors:
            target = descriptor.canonical_id
            if target in nested:
                continue
            group = next(
                (
                    g
                    for g in ("forecast_models", "event_matching", "discovery")
                    if target.startswith(g + ".")
                ),
                None,
            )
            if group is not None:
                groups[group].append(target)
            elif isinstance(descriptor, CallableInput) and descriptor.discovery_group is not None:
                if descriptor.discovery_group not in groups:
                    raise invalid("an existing native discovery group", descriptor.discovery_group)
                groups[descriptor.discovery_group].append(target)
            elif isinstance(descriptor, FamilyInput):
                buckets["artifacts"].append(target)
            elif isinstance(descriptor, TypeInput) and provider.owner == "core":
                groups["datasets"].append(target)
            elif isinstance(descriptor, TypeInput):
                buckets["evidence" if provider.owner == "runtime" else "inputs"].append(target)
            elif provider.owner == "runtime":
                buckets["runtime"].append(target)
            elif target.startswith("actions.") or target == "Session.source_bindings":
                buckets["entry"].append(target)
            elif (
                isinstance(descriptor, CallableInput)
                and descriptor.public_entrypoint.startswith("mv.")
            ) or isinstance(descriptor, NavigationInput):
                buckets["inputs"].append(target)
            else:
                buckets["methods"].append(target)
    buckets["entry"].append("session.namespace")
    buckets["artifacts"].append("datasets")
    buckets["inputs"].extend(("filters", "forecast_models", "event_matching"))
    buckets["methods"].append("discovery")
    result: list[NavigationInput] = []

    def pages(target: str, members: list[str], hub: bool) -> None:
        # Page size is below the existing navigation budget. Pages are native
        # descriptors, individually resolvable and traversable by the shared resolver.
        limit = 10 if hub else 16
        if len(members) <= limit:
            result.append(
                NavigationInput(
                    target,
                    "Discover " + target + " contracts.",
                    tuple(members),
                    "decision_hub" if hub else "navigation",
                )
            )
            return
        children = []
        for start in range(0, len(members), 16):
            child = f"{target}.page_{start // 16 + 1}"
            children.append(child)
            result.append(
                NavigationInput(
                    child, "Focused " + target + " contracts.", tuple(members[start : start + 16])
                )
            )
        result.append(
            NavigationInput(
                target,
                "Discover " + target + " contracts.",
                tuple(children),
                "decision_hub" if hub else "navigation",
            )
        )

    for target, members in groups.items():
        pages(target, members, False)
    for target, members in buckets.items():
        pages(target, members, True)
    result.append(
        NavigationInput(
            "",
            "Dataset analysis: construct, explicitly execute, inspect committed results.",
            tuple(buckets),
            "root",
        )
    )
    return tuple(result)
