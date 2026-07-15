"""Regression tests for semantic authoring help surfaces."""


def test_stepwise_authoring_help_lists_new_symbols_only() -> None:
    import marivo.datasource as md
    import marivo.semantic as ms

    semantic_text = ms.help_text()
    datasource_text = md.help_text()

    for name in ("VerifyResult", "domain", "entity", "metric"):
        assert name in semantic_text, f"semantic help missing {name}"
    for name in ("prepare_entity", "prepare_metric", "DomainBrief"):
        assert name not in semantic_text, f"semantic help still exposes {name}"
    for name in (
        "PartitionScope",
        "UnprunedScope",
        "SourceInspection",
        "DiscoverySnapshot",
        "raw_sql",
    ):
        assert name in datasource_text, f"datasource help missing {name}"
