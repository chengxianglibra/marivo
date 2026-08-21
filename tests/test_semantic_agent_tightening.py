"""Regression tests for semantic authoring help surfaces."""


def test_stepwise_authoring_help_lists_new_symbols_only() -> None:
    from tests.shared_fixtures import rendered_help

    semantic_text = rendered_help(owner="semantic")
    datasource_text = rendered_help(owner="datasource")

    for name in ("domain", "entity", "metric", "readiness"):
        assert name in semantic_text, f"semantic help missing {name}"
    assert "VerifyResult" not in semantic_text
    for name in ("prepare_entity", "prepare_metric", "DomainBrief"):
        assert name not in semantic_text, f"semantic help still exposes {name}"
    for name in (
        "PartitionScope",
        "UnprunedScope",
        "SourceInspection",
        "DiscoverySnapshot",
        "time_range",
        "raw_sql",
    ):
        assert name in datasource_text, f"datasource help missing {name}"
