"""The single native Dataset analysis disclosure registry."""

from marivo.analysis._capabilities.dataset_registry import prepare
from marivo.analysis.datasets.contract import _install_continuation_reader

REGISTRY = prepare()
_install_continuation_reader(REGISTRY.continuation_help)
