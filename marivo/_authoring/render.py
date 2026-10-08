"""Shared rendering for domain-owned authoring effect declarations."""

from marivo._authoring.model import AuthoringEffects, AuthoringRepair


def effect_lines(effects: AuthoringEffects) -> tuple[str, ...]:
    """Render the common effect block without inferring any capabilities."""
    return (
        "  Effects:",
        f"    data access: {effects.data_access}",
        f"    connection: {effects.connection}",
        f"    mutations: {', '.join(effects.mutations) or 'none'}",
        f"    flags: {', '.join(effects.flags) or 'none'}",
    )


def _repair_summary(repair: AuthoringRepair, *, optional: bool = False) -> str:
    """Render an existing repair's action and qualified Help route."""
    label = "optional fix" if optional else "fix"
    target = repair.help_target
    qualified: str = target.surface
    if target.canonical_id is not None:
        qualified += f".{target.canonical_id}"
    return f'{label}: {repair.action}; help: marivo.help("{qualified}")'
