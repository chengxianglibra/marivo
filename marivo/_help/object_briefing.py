"""No-I/O semantic object briefings for unified help."""

from __future__ import annotations

from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.introspection.live.render import enforce_budget


def _bounded(text: str) -> str:
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


def is_semantic_object(target: object) -> bool:
    """Return whether target is an exact Ref or registered CatalogEntry."""
    from marivo.refs import Ref

    if type(target) is Ref:
        return True
    if type(target).__module__ != "marivo.semantic.catalog":
        return False
    from marivo.semantic.catalog import CatalogEntry

    return isinstance(target, CatalogEntry)


def semantic_object_path(target: object) -> str:
    """Return the exact identity path after semantic-object validation."""
    from marivo.refs import Ref
    from marivo.semantic.catalog import CatalogEntry

    if type(target) is Ref:
        return target.path
    if isinstance(target, CatalogEntry):
        return target.ref.path
    raise RuntimeError(f"unsupported semantic object: {type(target).__name__}")


def _analysis_consumers(kind: str) -> tuple[str, ...]:
    from marivo.analysis._capabilities.registry import REGISTRY

    family = {
        "metric": "MetricSemantic",
        "dimension": "DimensionSemantic",
        "time_dimension": "TimeDimensionSemantic",
    }.get(kind)
    if family is None:
        return ()
    return REGISTRY.constructor_consumers.get(family, ())


def render_semantic_object(target: object) -> str:
    """Render identity or loaded catalog facts without loading or querying."""
    from marivo.refs import Ref
    from marivo.semantic.catalog import CatalogEntry

    if type(target) is Ref:
        ref = target
        return _bounded(
            "\n".join(
                (
                    f"{ref.kind.value}: {ref.path}",
                    "  Object: Ref",
                    "  Authority: typed identity only; project membership and readiness are unknown.",
                    "",
                    "  Inspect in an explicitly loaded project:",
                    "    import marivo.semantic as ms",
                    "    catalog = ms.load()",
                    "    entry = catalog.require(ref)",
                    "    entry.details().show()",
                    "    entry.contract().show()",
                    "",
                    '  Capability help: marivo.help("semantic.Ref")',
                )
            )
        )
    if not isinstance(target, CatalogEntry):
        raise RuntimeError(f"unsupported semantic object: {type(target).__name__}")

    ref = target.ref
    details_text = target.details().render()
    contract_text = target.contract().render()
    lines = [
        f"{ref.kind.value}: {ref.path}",
        f"  Object: {type(target).__name__}",
        "  Authority: current compiled catalog entry.",
        "",
        "  Details:",
        *(f"    {line}" for line in details_text.splitlines()),
        "",
        "  Semantic continuation:",
        *(f"    {line}" for line in contract_text.splitlines()),
    ]
    consumers = _analysis_consumers(ref.kind.value)
    if consumers:
        lines.extend(
            (
                "",
                "  Conditional analysis consumers (require readiness first):",
                *(f'    marivo.help("analysis.{consumer}")' for consumer in consumers[:8]),
            )
        )
    lines.extend(
        (
            "",
            "  Readiness is not inferred here; use catalog.readiness(refs=[entry]).",
            "  No datasource connectivity or inspection evidence was queried.",
        )
    )
    return _bounded("\n".join(lines))
