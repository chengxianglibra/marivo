"""Project discovery and loading for marivo.semantic v1.1.

Implements find_project and the two-pass loader pipeline.  This module
absorbs the old registry.py LoaderContext management.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha1
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, Literal

from marivo.datasource.errors import (
    DatasourceDuplicateError,
    DatasourceError,
    DatasourceLoadError,
)
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.loader import load_datasources
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    StructuredWarning,
    _raise,
)
from marivo.semantic.ir import DomainIR
from marivo.semantic.validator import Registry, Sidecar, assembly_validate

__all__ = [
    "LoadResult",
    "LoaderContext",
    "find_project",
]


@dataclass
class LoaderContext:
    """Context active during loader execution.

    Set via ``_LOADER_CTX`` ContextVar; decorator functions read
    this to enforce outside-loader-context guards.
    """

    current_model_file: str | None = None
    default_domain: str | None = None
    pending_objects: list[Any] = field(default_factory=list)
    #: DimensionRef/TimeDimensionRef instances returned by decorators, to have
    #: their _resolver wired up after the two-pass load completes.
    pending_refs: list[Any] = field(default_factory=list)


_LOADER_CTX: ContextVar[LoaderContext | None] = ContextVar(
    "_LOADER_CTX",
    default=None,
)


def _wrap_datasource_error(error: Exception) -> SemanticLoadError:
    if isinstance(error, DatasourceDuplicateError):
        datasource = error.details.get("datasource")
        refs = (datasource,) if isinstance(datasource, str) and datasource else ()
        return SemanticLoadError(
            kind=ErrorKind.DUPLICATE_NAME,
            message=error.message,
            refs=refs,
            hint="Keep each datasource name unique under .marivo/datasource.",
        )
    if isinstance(error, DatasourceLoadError):
        path = error.details.get("path")
        refs = (path,) if isinstance(path, str) and path else ()
        return SemanticLoadError(
            kind=ErrorKind.INVALID_PROJECT,
            message=error.message,
            refs=refs,
            hint=error.hint or "Check .marivo/datasource/*.py datasource declarations.",
        )
    if isinstance(error, DatasourceError):
        datasource = error.details.get("datasource")
        refs = (datasource,) if isinstance(datasource, str) and datasource else ()
        return SemanticLoadError(
            kind=ErrorKind.ORGANIZATION_ERROR,
            message=error.message,
            refs=refs,
            hint=error.hint or "Check .marivo/datasource/*.py datasource declarations.",
        )
    return SemanticLoadError(
        kind=ErrorKind.ORGANIZATION_ERROR,
        message=str(error),
        hint="Check .marivo/datasource/*.py datasource declarations.",
    )


@dataclass(frozen=True)
class LoadResult:
    """Result of a project load attempt."""

    status: Literal["ready", "errored"]
    errors: tuple[SemanticError, ...] = ()
    warnings: tuple[StructuredWarning, ...] = ()
    registry: Registry | None = None
    sidecar: Sidecar | None = None
    filtered_models: tuple[str, ...] = ()
    datasource_irs: tuple[DatasourceIR, ...] = ()


# ---------------------------------------------------------------------------
# File loading helpers
# ---------------------------------------------------------------------------


def _is_excluded_file(filename: str) -> bool:
    """Return True if a file should be excluded from loading."""
    basename = filename
    if basename == "_domain.py":
        return True  # Handled separately
    if basename == "_exports.py":
        return True
    if basename.startswith("."):
        return True
    if basename.startswith("test_") and basename.endswith(".py"):
        return True
    return basename.endswith("_test.py")


def _discover_model_dirs(root: Path) -> list[Path]:
    """Find top-level subdirectories that could contain domain definitions.

    Returns directories sorted by name for deterministic load order.
    """
    if not root.exists():
        return []
    dirs = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            dirs.append(child)
    return dirs


def _filter_model_dirs(
    all_dirs: list[Path],
    models: Sequence[str] | None,
) -> tuple[list[Path], list[StructuredWarning]]:
    """Filter discovered model directories to only those matching the given model names.

    When models is None or empty, returns all_dirs unchanged (no warnings).
    """
    if models is None or len(models) == 0:
        return all_dirs, []
    model_set = set(models)
    filtered = [d for d in all_dirs if d.name in model_set]
    discovered_names = {d.name for d in all_dirs}
    missing = model_set - discovered_names
    warnings = [
        StructuredWarning(
            kind="filtered_domain_ref",
            message=f"Requested domain {name!r} has no directory on disk.",
            refs=(name,),
            location=None,
        )
        for name in sorted(missing)
    ]
    return filtered, warnings


def _module_prefix(root: Path) -> str:
    """Return a stable synthetic package prefix for this semantic root."""
    digest = sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"_marivo_semantic_{digest}"


def _purge_synthetic_modules(prefix: str) -> None:
    """Remove previously loaded synthetic modules for a reload-safe project load."""
    for name in list(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            del sys.modules[name]


def _ensure_package(name: str, path: Path) -> None:
    """Install a lightweight package module with a filesystem search path."""
    package = types.ModuleType(name)
    package.__file__ = str(path)
    package.__package__ = name
    package.__path__ = [str(path)]
    sys.modules[name] = package


def _execute_file(
    filepath: Path,
    ctx: LoaderContext,
    errors: list[SemanticError],
    *,
    module_name: str,
    package_name: str,
) -> None:
    """Execute a single Python file within the loader context.

    Errors are accumulated, not raised.
    """
    token = _LOADER_CTX.set(ctx)
    try:
        spec = importlib_util.spec_from_file_location(module_name, filepath)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create module spec for {filepath}")
        module = importlib_util.module_from_spec(spec)
        module.__package__ = package_name
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        if isinstance(exc, SemanticError):
            errors.append(exc)
        else:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.ORGANIZATION_ERROR,
                    message=f"Error executing {filepath}: {exc}",
                    hint="Check the file for syntax or runtime errors.",
                )
            )
    finally:
        _LOADER_CTX.reset(token)


def _load_model_dir(
    model_dir: Path,
    root: Path,
    errors: list[SemanticError],
    *,
    module_prefix: str,
) -> LoaderContext | None:
    """Load a single model directory.

    Returns the LoaderContext with pending objects, or None on critical failure.
    """
    model_file = model_dir / "_domain.py"
    model_name = model_dir.name

    # Check _domain.py exists
    if not model_file.exists():
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.DOMAIN_FILE_MISSING,
                message=f"Domain directory {model_name!r} is missing _domain.py.",
                refs=(model_name,),
            )
        )
        return None

    # Execute _domain.py
    ctx = LoaderContext()
    model_package = f"{module_prefix}.{model_name}"
    _ensure_package(model_package, model_dir)
    _execute_file(
        model_file,
        ctx,
        errors,
        module_name=f"{model_package}._domain",
        package_name=model_package,
    )

    # Validate ms.domain() was called and name matches directory
    model_names = [ir.name for ir, _ in ctx.pending_objects if isinstance(ir, DomainIR)]

    if not model_names:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.DOMAIN_FILE_MISSING,
                message=f"_domain.py in {model_name!r} did not call ms.domain().",
                refs=(model_name,),
            )
        )
        return None

    # Check model name matches directory name
    if model_names[0] != model_name:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.DOMAIN_FILE_MISMATCH,
                message=f"Domain name {model_names[0]!r} does not match directory name {model_name!r}.",
                refs=(model_name, model_names[0]),
            )
        )
        return None

    # Set default_domain from the model declaration
    for ir, _ in ctx.pending_objects:
        if isinstance(ir, DomainIR) and ir.default:
            ctx.default_domain = ir.name
            break

    # Execute sibling .py files (exclude _domain.py, _exports.py, etc.)
    sibling_files: list[Path] = []
    for child in sorted(model_dir.iterdir()):
        if not child.is_file():
            continue
        if child.suffix != ".py":
            continue
        if _is_excluded_file(child.name):
            continue
        sibling_files.append(child)

    for sibling in sibling_files:
        _execute_file(
            sibling,
            ctx,
            errors,
            module_name=f"{model_package}.{sibling.stem}",
            package_name=model_package,
        )

    return ctx


def _build_registry(
    all_contexts: list[LoaderContext],
    *,
    datasource_irs: tuple[DatasourceIR, ...] = (),
) -> tuple[Registry, Sidecar]:
    """Build a Registry and Sidecar from all loader contexts.

    Pass 2: assemble all pending IR objects into the registry.
    """
    from marivo.semantic.ir import (
        DimensionIR,
        DimensionRef,
        EntityIR,
        MetricIR,
        RelationshipIR,
        TimeDimensionRef,
    )

    registry = Registry()
    sidecar: Sidecar = {}
    for datasource_ir in datasource_irs:
        registry.datasources[datasource_ir.semantic_id] = datasource_ir

    for ctx in all_contexts:
        for ir, callable_ in ctx.pending_objects:
            if not hasattr(ir, "semantic_id"):
                # DomainIR doesn't have semantic_id
                if isinstance(ir, DomainIR):
                    registry.models[ir.name] = ir
                continue

            sid = ir.semantic_id

            if isinstance(ir, EntityIR):
                registry.datasets[sid] = ir
                if callable_ is not None:
                    sidecar[sid] = callable_
            elif isinstance(ir, DimensionIR):
                registry.fields[sid] = ir
                if callable_ is not None:
                    sidecar[sid] = callable_
            elif isinstance(ir, MetricIR):
                registry.metrics[sid] = ir
                if callable_ is not None:
                    sidecar[sid] = callable_
            elif isinstance(ir, RelationshipIR):
                registry.relationships[sid] = ir

    # Wire up DimensionRef/TimeDimensionRef resolvers so that calling
    # dimension_ref(parent_table) in metric bodies resolves to the sidecar callable.
    def _make_field_resolver(sidecar_dict: Sidecar) -> Callable[[str, Any], Any]:
        def _resolver(semantic_id: str, parent_table: Any) -> Any:
            callable_ = sidecar_dict.get(semantic_id)
            if callable_ is None:
                raise RuntimeError(
                    f"DimensionRef({semantic_id!r}) resolver: no sidecar callable found."
                )
            return callable_(parent_table)

        return _resolver

    resolver = _make_field_resolver(sidecar)

    # Set _resolver on all DimensionRef/TimeDimensionRef instances that were
    # registered during decorator execution via ctx.pending_refs.
    for ctx in all_contexts:
        for ref in ctx.pending_refs:
            if isinstance(ref, (DimensionRef, TimeDimensionRef)):
                ref._resolver = resolver

    return registry, sidecar


def load_project(root: Path, *, models: Sequence[str] | None = None) -> LoadResult:
    """Load models from the semantic project root.

    Two-pass pipeline:
    1. Discover model directories and execute their files.
    2. Build registry, validate, and assemble the loaded objects.

    When *models* is specified, only those model directories are loaded.
    Cross-model references to filtered-out models produce warnings instead
    of errors, so the registry remains usable.

    Note: Each model directory is expected to contain a ``_domain.py`` file
    that calls ``ms.domain(name=...)``.

    Returns a LoadResult with status, errors, warnings, registry, and sidecar.
    """
    errors: list[SemanticError] = []
    warnings: list[StructuredWarning] = []
    registry: Registry | None = None
    sidecar: Sidecar | None = None

    module_prefix = _module_prefix(root)
    _purge_synthetic_modules(module_prefix)

    # Inject root's parent into sys.path so model files can import each other
    path_entry = str(root.parent)
    sys.path.insert(0, path_entry)
    try:
        _ensure_package(module_prefix, root)
        datasource_result = load_datasources(root.parent / "datasource")
        for error in datasource_result.errors:
            errors.append(_wrap_datasource_error(error))
        model_dirs = _discover_model_dirs(root)
        model_dirs, filter_warnings = _filter_model_dirs(model_dirs, models)
        warnings.extend(filter_warnings)
        all_contexts: list[LoaderContext] = []

        # Pass 1: Discover + Collect
        for model_dir in model_dirs:
            ctx = _load_model_dir(model_dir, root, errors, module_prefix=module_prefix)
            if ctx is not None:
                all_contexts.append(ctx)

        # Pass 2: Resolve + Validate
        registry, sidecar = _build_registry(
            all_contexts,
            datasource_irs=datasource_result.datasources,
        )

        # Duplicate semantic_id check
        seen_objects: dict[str, Any] = {}
        for ctx in all_contexts:
            for ir, _ in ctx.pending_objects:
                if hasattr(ir, "semantic_id"):
                    sid = ir.semantic_id
                    existing = seen_objects.get(sid)
                    if existing is not None:
                        errors.append(
                            SemanticLoadError(
                                kind=ErrorKind.DUPLICATE_NAME,
                                message=f"Duplicate semantic_id: {sid!r}",
                                refs=(sid,),
                            )
                        )
                    seen_objects.setdefault(sid, ir)

        # Assembly validation
        loaded_models_set = {d.name for d in model_dirs} if models is not None else None
        asm_errors, asm_warnings = assembly_validate(
            registry, sidecar, loaded_models=loaded_models_set
        )
        errors.extend(asm_errors)
        warnings.extend(asm_warnings)

    finally:
        # Clean up sys.path
        if path_entry in sys.path:
            sys.path.remove(path_entry)

    status: Literal["ready", "errored"] = "ready" if not errors else "errored"
    filtered_models_tuple = tuple(d.name for d in model_dirs) if models is not None else ()
    return LoadResult(
        status=status,
        errors=tuple(errors),
        warnings=tuple(warnings),
        registry=registry if status == "ready" else None,
        sidecar=sidecar if status == "ready" else None,
        filtered_models=filtered_models_tuple,
        datasource_irs=datasource_result.datasources,
    )


def find_project(start_dir: str | Path = ".") -> Any:
    """Discover a semantic project by walking up from *start_dir*.

    Looks for a ``.marivo/`` directory.  Returns a
    ``SemanticProject`` on success, or ``None`` if no project is found.

    If ``.marivo`` exists but is a non-directory file,
    raises ``SemanticLoadError`` with ``INVALID_PROJECT``.
    """
    from marivo.semantic.reader import SemanticProject

    current = Path(start_dir).resolve()

    while True:
        marivo_dir = current / ".marivo"
        if marivo_dir.exists():
            if not marivo_dir.is_dir():
                _raise(
                    ErrorKind.INVALID_PROJECT,
                    f"{marivo_dir} exists but is not a directory.",
                    cls=SemanticLoadError,
                    refs=(str(marivo_dir),),
                )
            return SemanticProject(workspace_dir=current)

        parent = current.parent
        if parent == current:
            # Reached filesystem root
            return None
        current = parent
