"""Progressive native Dataset Help uses bounded source and method discovery."""

from marivo.analysis._capabilities.dataset_model import NavigationInput
from marivo.analysis._capabilities.registry import REGISTRY
from tests.shared_fixtures import rendered_help


def test_methods_hub_reaches_core_operators_without_root_inventory() -> None:
    root = rendered_help(owner="analysis")
    reached: set[str] = set()

    def visit(target: str) -> None:
        if target in reached:
            return
        reached.add(target)
        descriptor = REGISTRY.by_canonical_id(target)
        rendered_help(target, owner="analysis")
        if isinstance(descriptor, NavigationInput):
            for member in descriptor.members:
                visit(member)

    visit("methods")
    assert {
        "metric_dataset.compare",
        "delta_dataset.attribute",
        "metric_dataset.correlate",
        "metric_dataset.forecast",
        "discovery",
    } <= reached
    for name in ("compare", "attribute", "correlate", "forecast", "hypothesis_test"):
        assert name not in root
    assert "marivo.help('analysis.methods')" in root
    assert "recommend" not in root.lower()
    assert "hypothesis_test" not in reached
