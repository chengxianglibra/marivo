"""The single native Dataset analysis disclosure registry."""

from marivo.analysis._capabilities.dataset_registry import prepare
from marivo.analysis.compiler.source_admission import source_admission_fact
from marivo.analysis.datasets.contract import _install_continuation_reader
from marivo.analysis.observation.contracts import _install_source_admission_reader

REGISTRY = prepare()
_install_continuation_reader(REGISTRY.continuation_help)
_install_source_admission_reader(source_admission_fact)
