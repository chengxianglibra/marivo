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
    DatasourceConfigError,
    DatasourceDuplicateError,
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
from marivo.semantic.ir import Additivity, DimensionIR, DomainIR, MeasureIR, MetricIR
from marivo.semantic.validator import Registry, Sidecar, assembly_validate

__all__ = [
    "LoadResult",
    "LoaderContext",
    "find_project",
    "loader_context",
]


@dataclass
class LoaderContext:
    """Context active during loader execution.

    Set via ``_LOADER_CTX`` ContextVar; decorator functions read
    this to enforce outside-loader-context guards.
    """

    model_name: str | None = None
    file_path: str | None = None
    current_model_file: str | None = None
    default_domain: str | None = None
    pending_objects: list[Any] = field(default_factory=list)
    #: DimensionRef/TimeDimensionRef/MeasureRef instances returned by decorators,
    #: to have their _resolver wired up after the two-pass load completes.
    pending_refs: list[Any] = field(default_factory=list)


_LOADER_CTX: ContextVar[LoaderContext | None] = ContextVar(
    "_LOADER_CTX",
    default=None,
)


class LoaderContextManager:
    """Context manager that sets/resets the loader context for test use.

    Usage::

        ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
        with LoaderContextManager(ctx):
            sales = ms.domain(name="sales", default=True)
            ...
    """

    def __init__(self, ctx: LoaderContext) -> None:
        self._ctx = ctx
        self._token: Any = None

    def __enter__(self) -> LoaderContext:
        self._token = _LOADER_CTX.set(self._ctx)
        return self._ctx

    def __exit__(self, *args: object) -> None:
        if self._token is not None:
            _LOADER_CTX.reset(self._token)


# Alias for convenient use at the call site
loader_context = LoaderContextManager


def _wrap_datasource_error(error: Exception) -> SemanticLoadError:
    if isinstance(error, DatasourceDuplicateError):
        datasource = error.details.get("datasource")
        refs = (datasource,) if isinstance(datasource, str) and datasource else ()
        return SemanticLoadError(
            kind=ErrorKind.DUPLICATE_NAME,
            message=error.message,
            refs=refs,
            hint="Keep each datasource name unique under models/datasources/.",
        )
    if isinstance(error, DatasourceLoadError):
        path = error.details.get("path")
        refs = (path,) if isinstance(path, str) and path else ()
        return SemanticLoadError(
            kind=ErrorKind.INVALID_PROJECT,
            message=error.message,
            refs=refs,
            hint=error.hint or "Check models/datasources/*.py datasource declarations.",
        )
    if isinstance(error, DatasourceConfigError):
        datasource = error.details.get("datasource")
        refs = (datasource,) if isinstance(datasource, str) and datasource else ()
        return SemanticLoadError(
            kind=ErrorKind.ORGANIZATION_ERROR,
            message=error.message,
            refs=refs,
            hint=error.hint or "Check models/datasources/*.py datasource declarations.",
        )
    return SemanticLoadError(
        kind=ErrorKind.ORGANIZATION_ERROR,
        message=str(error),
        hint="Check models/datasources/*.py datasource declarations.",
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
        if child.is_dir() and not child.name.startswith(".") and child.name != "__pycache__":
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


# ---------------------------------------------------------------------------
# Metric additivity resolution (runs after _build_registry, before validation)
# ---------------------------------------------------------------------------


def _resolve_tier1_additivity(metric: MetricIR, registry: Registry) -> Additivity | None:
    from marivo.semantic.ir import SemiAdditive

    target_kind = metric.aggregation_target_kind or (
        "measure" if metric.measure is not None else None
    )
    target_id = metric.aggregation_target or metric.measure or ""
    agg = metric.aggregation
    agg_name = agg[0] if isinstance(agg, tuple) else agg
    if target_kind == "entity":
        if target_id not in registry.entities:
            return None
        return "additive" if agg_name == "count" else None
    measure = registry.dimensions.get(target_id)
    measure_ir = registry.measures.get(target_id) if hasattr(registry, "measures") else None
    if measure is None and measure_ir is None:
        return None  # validator: UNKNOWN_MEASURE / MISSING_MEASURE_ADDITIVITY
    if measure_ir is None and getattr(measure, "additivity", None) is None:
        return None  # validator: MISSING_MEASURE_ADDITIVITY
    if agg_name == "count":
        return "additive"
    if agg_name == "sum":
        nature = (
            measure_ir.additivity
            if measure_ir is not None
            else getattr(measure, "additivity", None)
        )
        if nature == "additive":
            return "additive"
        if isinstance(nature, SemiAdditive):
            if metric.fold_override is not None:
                return SemiAdditive(over=nature.over, fold=metric.fold_override)
            return nature
        return None  # non_additive measure + sum -> validator: INVALID_MEASURE_AGGREGATION
    return "non_additive"  # mean/median/percentile/count_distinct/min/max


def _resolve_derived_additivity(metric: MetricIR, registry: Registry) -> Additivity | None:
    from marivo.semantic.ir import (
        LinearComposition,
        RatioComposition,
        WeightedAverageComposition,
        additivity_bucket,
    )

    comp = metric.composition
    if isinstance(comp, (RatioComposition, WeightedAverageComposition)):
        return "non_additive"
    assert isinstance(comp, LinearComposition)
    buckets: list[str] = []
    for term in comp.terms:
        dep = registry.metrics.get(term.metric)
        if dep is None or dep.additivity is None:
            return None  # dep not resolved yet (retry) or missing (validator reports)
        buckets.append(additivity_bucket(dep.additivity))
    return "additive" if all(b == "additive" for b in buckets) else "non_additive"


def _resolve_metric_additivity(registry: Registry) -> None:
    import dataclasses

    # Phase A: tier-1 simple metrics resolve from their measure dimension.
    for sid, m in list(registry.metrics.items()):
        if m.metric_type == "simple" and m.aggregation is not None and m.additivity is None:
            resolved = _resolve_tier1_additivity(m, registry)
            if resolved is not None:
                registry.metrics[sid] = dataclasses.replace(m, additivity=resolved)

    # Phase B: derived metrics propagate from components (fixpoint over chains).
    for _ in range(len(registry.metrics) + 1):
        changed = False
        for sid, m in list(registry.metrics.items()):
            if m.metric_type == "derived" and m.additivity is None:
                resolved = _resolve_derived_additivity(m, registry)
                if resolved is not None:
                    registry.metrics[sid] = dataclasses.replace(m, additivity=resolved)
                    changed = True
        if not changed:
            break


def _resolve_tier1_unit(metric: MetricIR, registry: Registry) -> str | None:
    from marivo.semantic.unit_algebra import tier1_unit

    target_kind = metric.aggregation_target_kind or (
        "measure" if metric.measure is not None else None
    )
    if target_kind == "entity":
        return None
    target_id = metric.aggregation_target or metric.measure or ""
    measure_ir: MeasureIR | DimensionIR | None = registry.measures.get(target_id)
    if measure_ir is None:
        measure_ir = registry.dimensions.get(target_id)
    if measure_ir is None:
        return None  # validator: UNKNOWN_MEASURE (existing rule)
    agg = metric.aggregation
    agg_name = agg[0] if isinstance(agg, tuple) else agg
    return tier1_unit(agg_name or "", getattr(measure_ir, "unit", None))


def _resolve_derived_unit(metric: MetricIR, registry: Registry) -> str | None:
    from marivo.semantic.ir import (
        LinearComposition,
        RatioComposition,
        WeightedAverageComposition,
    )
    from marivo.semantic.unit_algebra import (
        linear_unit,
        ratio_unit,
        weighted_average_unit,
    )

    comp = metric.composition
    if isinstance(comp, RatioComposition):
        num = registry.metrics.get(comp.numerator)
        den = registry.metrics.get(comp.denominator)
        if num is None or den is None:
            return None
        return ratio_unit(num.unit, den.unit)
    if isinstance(comp, WeightedAverageComposition):
        value = registry.metrics.get(comp.value)
        return weighted_average_unit(value.unit) if value is not None else None
    assert isinstance(comp, LinearComposition)
    units: list[str | None] = []
    for term in comp.terms:
        dep = registry.metrics.get(term.metric)
        if dep is None:
            return None
        units.append(dep.unit)
    return linear_unit(units)


def _resolve_metric_unit(registry: Registry) -> None:
    import dataclasses

    # Phase A: tier-1 simple metrics resolve from their measure dimension.
    for sid, m in list(registry.metrics.items()):
        if m.metric_type == "simple" and m.aggregation is not None and m.unit is None:
            resolved = _resolve_tier1_unit(m, registry)
            if resolved is not None:
                registry.metrics[sid] = dataclasses.replace(m, unit=resolved)

    # Phase B: derived metrics propagate from components (fixpoint over chains).
    for _ in range(len(registry.metrics) + 1):
        changed = False
        for sid, m in list(registry.metrics.items()):
            if m.metric_type == "derived" and m.unit is None:
                resolved = _resolve_derived_unit(m, registry)
                if resolved is not None:
                    registry.metrics[sid] = dataclasses.replace(m, unit=resolved)
                    changed = True
        if not changed:
            break


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
        EntityIR,
        MetricIR,
        RelationshipIR,
    )
    from marivo.semantic.refs import DimensionRef, MeasureRef, TimeDimensionRef

    registry = Registry()
    sidecar: Sidecar = {}
    for datasource_ir in datasource_irs:
        registry.datasources[datasource_ir.semantic_id] = datasource_ir

    for ctx in all_contexts:
        for ir, callable_ in ctx.pending_objects:
            if not hasattr(ir, "semantic_id"):
                # DomainIR doesn't have semantic_id
                if isinstance(ir, DomainIR):
                    registry.domains[ir.name] = ir
                continue

            sid = ir.semantic_id

            if isinstance(ir, EntityIR):
                registry.entities[sid] = ir
                if callable_ is not None:
                    sidecar[sid] = callable_
            elif isinstance(ir, DimensionIR):
                registry.dimensions[sid] = ir
                if callable_ is not None:
                    sidecar[sid] = callable_
            elif isinstance(ir, MeasureIR):
                registry.measures[sid] = ir
                if callable_ is not None:
                    sidecar[sid] = callable_
            elif isinstance(ir, MetricIR):
                registry.metrics[sid] = ir
                if callable_ is not None:
                    sidecar[sid] = callable_
            elif isinstance(ir, RelationshipIR):
                registry.relationships[sid] = ir

    # Wire up DimensionRef/TimeDimensionRef/MeasureRef resolvers so that calling
    # field_ref(parent_table) in metric bodies resolves to the sidecar callable.
    def _make_field_resolver(sidecar_dict: Sidecar) -> Callable[[str, Any], Any]:
        def _resolver(semantic_id: str, parent_table: Any) -> Any:
            callable_ = sidecar_dict.get(semantic_id)
            if callable_ is None:
                raise RuntimeError(
                    f"semantic field {semantic_id!r} resolver: no sidecar callable found."
                )
            return callable_(parent_table)

        return _resolver

    resolver = _make_field_resolver(sidecar)

    # Set _resolver on all DimensionRef/TimeDimensionRef/MeasureRef instances that
    # were registered during decorator execution via ctx.pending_refs. This lets
    # metric bodies call a measure/dimension ref with the entity table.
    for ctx in all_contexts:
        for ref in ctx.pending_refs:
            if isinstance(ref, (DimensionRef, TimeDimensionRef, MeasureRef)):
                ref._resolver = resolver

    _resolve_metric_additivity(registry)
    _resolve_metric_unit(registry)
    return registry, sidecar


def load_project(root: Path, *, models: Sequence[str] | None = None) -> LoadResult:
    """Load domains from the semantic project root.

    Two-pass pipeline:
    1. Discover domain directories and execute their files.
    2. Build registry, validate, and assemble the loaded objects.

    When *models* is specified, only those domain directories are loaded.
    Cross-domain references to filtered-out domains produce warnings instead
    of errors, so the registry remains usable.

    Note: Each domain directory is expected to contain a ``_domain.py`` file
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
        datasource_result = load_datasources(root.parent / "datasources")
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

    Looks for a ``marivo.toml`` file.  Returns a
    ``SemanticProject`` on success, or ``None`` if no project is found.

    If ``marivo.toml`` exists but is a non-file entry,
    raises ``SemanticLoadError`` with ``INVALID_PROJECT``.
    """
    from marivo.config import PROJECT_MANIFEST
    from marivo.semantic.reader import SemanticProject

    current = Path(start_dir).resolve()

    while True:
        manifest = current / PROJECT_MANIFEST
        if manifest.exists():
            if not manifest.is_file():
                _raise(
                    ErrorKind.INVALID_PROJECT,
                    f"{manifest} exists but is not a file.",
                    cls=SemanticLoadError,
                    refs=(str(manifest),),
                )
            return SemanticProject(workspace_dir=current)

        parent = current.parent
        if parent == current:
            # Reached filesystem root
            return None
        current = parent
