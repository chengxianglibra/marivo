"""Structured diagnostics for duplicate declarations during model loading."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from marivo.semantic.declaration_records import DefinitionIR
from marivo.semantic.errors import ErrorKind, SemanticLoadError
from marivo.semantic.ir import DomainIR
from marivo.semantic.loading_context import LoaderContext


def _semantic_source_path(ir: DefinitionIR) -> str:
    file: object = ir.location.file
    if isinstance(file, str) and file:
        return file
    return "<unknown>"


def domain_duplicate_errors(model_dirs: Sequence[Path]) -> list[SemanticLoadError]:
    errors: list[SemanticLoadError] = []
    seen: dict[str, Path] = {}
    for model_dir in model_dirs:
        existing = seen.get(model_dir.name)
        if existing is not None:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.DUPLICATE_NAME,
                    message=(
                        f"Duplicate domain name: {model_dir.name!r}. "
                        f"First domain directory: {existing}. "
                        f"Conflicting domain directory: {model_dir}."
                    ),
                    refs=(model_dir.name, str(existing), str(model_dir)),
                    hint="Rename or remove one domain directory.",
                )
            )
        seen.setdefault(model_dir.name, model_dir)
    return errors


def semantic_duplicate_errors(contexts: Sequence[LoaderContext]) -> list[SemanticLoadError]:
    errors: list[SemanticLoadError] = []
    seen: dict[str, DefinitionIR] = {}
    for ctx in contexts:
        for pending in ctx.pending_definitions:
            ir = pending.definition
            if isinstance(ir, DomainIR):
                continue
            sid = ir.semantic_id
            existing = seen.get(sid)
            if existing is not None:
                first = _semantic_source_path(existing)
                second = _semantic_source_path(ir)
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.DUPLICATE_NAME,
                        message=(
                            f"Duplicate semantic_id: {sid!r}. "
                            f"First declaration: {first}. Conflicting declaration: {second}."
                        ),
                        refs=(sid, first, second),
                        hint="Rename or remove one semantic declaration.",
                    )
                )
            seen.setdefault(sid, ir)
    return errors
