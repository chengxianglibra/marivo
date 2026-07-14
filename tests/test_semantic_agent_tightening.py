"""Regression tests for semantic authoring help surfaces."""


def test_stepwise_authoring_help_lists_new_symbols_only() -> None:
    import marivo.datasource as md
    from marivo.introspection.surface import render as surface_render
    from marivo.semantic.help import _surface as semantic_surface

    semantic_data = surface_render(semantic_surface(), None, "json")
    datasource_text = md.help_text()

    for name in ("VerifyResult", "domain", "entity", "metric"):
        assert name in str(semantic_data), f"semantic help missing {name}"
    for name in ("prepare_entity", "prepare_metric", "DomainBrief"):
        assert name not in str(semantic_data), f"semantic help still exposes {name}"
    for name in (
        "PartitionScope",
        "UnprunedScope",
        "SourceInspection",
        "DiscoverySnapshot",
        "raw_sql",
    ):
        assert name in datasource_text, f"datasource help missing {name}"
