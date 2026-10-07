"""Shared rendering for domain-owned authoring effect declarations."""

from marivo._authoring.model import AuthoringEffects


def effect_lines(effects: AuthoringEffects) -> tuple[str, ...]:
    """Render the common effect block without inferring any capabilities."""
    return (
        "  Effects:",
        f"    data access: {effects.data_access}",
        f"    connection: {effects.connection}",
        f"    mutations: {', '.join(effects.mutations) or 'none'}",
        f"    flags: {', '.join(effects.flags) or 'none'}",
    )
