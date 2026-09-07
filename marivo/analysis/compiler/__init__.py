"""Private source compilation; no public Dataset or execution authority."""

from marivo.analysis.compiler.lowering import compile_dataset
from marivo.analysis.compiler.nodes import CompiledDataset, CompiledValidation, RetainedPartSpec
from marivo.analysis.compiler.normalize import captured_parameters, required_entities

__all__ = [
    "CompiledDataset",
    "CompiledValidation",
    "RetainedPartSpec",
    "captured_parameters",
    "compile_dataset",
    "required_entities",
]
