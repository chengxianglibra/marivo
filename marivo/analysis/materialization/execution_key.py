"""The sole Runtime key derives from the already normalized Core definition."""

from marivo.analysis.datasets.descriptors import _canonical_digest


def execution_key(definition_fingerprint: str) -> str:
    """Bind a definition to common materialization protocol v1, within one Session."""
    return _canonical_digest(("marivo.dataset_execution_key/v1", definition_fingerprint, 1))
