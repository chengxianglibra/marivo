"""Project discovery and loading for marivo.semantic v1.1.

Implements find_project and the two-pass loader pipeline.  This module
absorbs the old registry.py LoaderContext management.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha1
from importlib import util as importlib_util
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from marivo.config import AUTHORED_DIR
from marivo.datasource.errors import (
    DatasourceDuplicateError,
    DatasourceError,
    DatasourceLoadError,
)
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.loader import load_datasources
from marivo.refs import FieldKind, Ref, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._compiled_state import CompiledSemanticState, build_compiled_state
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    StructuredWarning,
    _raise,
)
from marivo.semantic.ir import (
    Additivity,
    CumulativeComposition,
    DimensionIR,
    DimensionKind,
    DomainIR,
    MeasureIR,
    MetricIR,
)
from marivo.semantic.validator import Registry, assembly_validate

if TYPE_CHECKING:
    from marivo.semantic._authoring_context import PendingDefinition

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
    pending_definitions: list[PendingDefinition] = field(default_factory=list)


_LOADER_CTX: ContextVar[LoaderContext | None] = ContextVar(
    "_LOADER_CTX",
    default=None,
)


class LoaderContextManager:
    """Context manager that sets/resets the loader context for test use.

    Usage::

        ctx = LoaderContext(model_name="sales", file_path="/tmp/_domain.py")
        with LoaderContextManager(ctx):
            sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
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
        refs = (error.received,) if error.received else ()
        return SemanticLoadError(
            kind=ErrorKind.DUPLICATE_NAME,
            message=error.message,
            refs=refs,
            hint="Keep each datasource name unique under models/datasources/.",
        )
    if isinstance(error, DatasourceLoadError):
        refs = (error.location,) if error.location else ()
        return SemanticLoadError(
            kind=ErrorKind.INVALID_PROJECT,
            message=error.message,
            refs=refs,
            hint="Check models/datasources/*.py datasource declarations.",
        )
    if isinstance(error, DatasourceError):
        refs = (error.received,) if error.received else ()
        return SemanticLoadError(
            kind=ErrorKind.ORGANIZATION_ERROR,
            message=error.message,
            refs=refs,
            hint="Check models/datasources/*.py datasource declarations.",
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
    expression_sidecar: CompiledExpressionSidecar | None = None
    compiled_state: CompiledSemanticState | None = None
    filtered_models: tuple[str, ...] = ()
    datasource_irs: tuple[DatasourceIR, ...] = ()


@dataclass(frozen=True)
class ModelsRoot:
    """Internal authored models root used by the semantic loader."""

    models_root: Path
    semantic_root: Path
    datasource_root: Path
    is_external: bool = False


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
                hint=(
                    f"Create models/semantic/{model_name}/_domain.py with "
                    'ms.domain(name="<domain>", owner="Mina Zhang"). '
                    'Run ms.help("authoring") for the domain authoring contract.'
                ),
            )
        )
        return None

    # Execute _domain.py
    ctx = LoaderContext(
        model_name=model_name,
        file_path=str(model_file),
        current_model_file=str(model_file),
    )
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
    model_names = [
        pending.definition.name
        for pending in ctx.pending_definitions
        if isinstance(pending.definition, DomainIR)
    ]

    if not model_names:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.DOMAIN_FILE_MISSING,
                message=f"_domain.py in {model_name!r} did not call ms.domain().",
                refs=(model_name,),
                hint=(
                    f'Call ms.domain(name="{model_name}", owner="<owner>") at the '
                    "top level of models/semantic/<domain>/_domain.py. "
                    'Run ms.help("authoring") for the domain authoring contract.'
                ),
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
    for pending in ctx.pending_definitions:
        ir = pending.definition
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
        module_name = f"{model_package}.{sibling.stem}"
        if module_name in sys.modules:
            continue
        ctx.current_model_file = str(sibling)
        _execute_file(
            sibling,
            ctx,
            errors,
            module_name=module_name,
            package_name=model_package,
        )

    return ctx


# ---------------------------------------------------------------------------
# Metric additivity resolution (runs after _build_registry, before validation)
# ---------------------------------------------------------------------------


def _time_dimensions_for_entity(entity_id: str, registry: Registry) -> list[DimensionIR]:
    """Return time dimensions on *entity_id*, sorted by semantic_id."""
    return sorted(
        (
            dim
            for dim in registry.dimensions.values()
            if dim.entity == entity_id and dim.kind == DimensionKind.TIME
        ),
        key=lambda dim: dim.semantic_id,
    )


def _resolve_cumulative_over(metric: MetricIR, registry: Registry) -> MetricIR:
    """Resolve omitted over= when exactly one time dimension exists on the base root entity."""
    import dataclasses

    if not isinstance(metric.composition, CumulativeComposition):
        return metric
    comp = metric.composition
    if comp.over is not None:
        return metric
    base = registry.metrics.get(comp.base)
    if base is None or base.root_entity is None:
        return metric
    candidates = _time_dimensions_for_entity(base.root_entity, registry)
    if len(candidates) != 1:
        return metric
    resolved = dataclasses.replace(comp, over=candidates[0].semantic_id)
    return dataclasses.replace(metric, composition=resolved)


def _resolve_cumulative_over_axes(registry: Registry) -> None:
    """Resolve omitted over= for all cumulative metrics in the registry."""
    for sid, metric in list(registry.metrics.items()):
        if isinstance(metric.composition, CumulativeComposition):
            registry.metrics[sid] = _resolve_cumulative_over(metric, registry)


def _resolve_tier1_additivity(metric: MetricIR, registry: Registry) -> Additivity | None:
    from marivo.semantic.ir import SemiAdditive

    if metric.weighted_mean is not None:
        value = registry.measures.get(metric.weighted_mean.value)
        weight = registry.measures.get(metric.weighted_mean.weight)
        if value is None or weight is None:
            return None
        if value.entity != weight.entity or weight.additivity != "additive":
            return None
        return "non_additive"

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
        CumulativeComposition,
        LinearComposition,
        RatioComposition,
        additivity_bucket,
    )

    comp = metric.composition
    if isinstance(comp, (RatioComposition, CumulativeComposition)):
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
        if (
            m.metric_type == "simple"
            and (m.aggregation is not None or m.weighted_mean is not None)
            and m.additivity is None
        ):
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

    if metric.weighted_mean is not None:
        value = registry.measures.get(metric.weighted_mean.value)
        return value.unit if value is not None else None

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
        CumulativeComposition,
        LinearComposition,
        RatioComposition,
    )
    from marivo.semantic.unit_algebra import (
        linear_unit,
        ratio_unit,
    )

    comp = metric.composition
    if isinstance(comp, RatioComposition):
        num = registry.metrics.get(comp.numerator)
        den = registry.metrics.get(comp.denominator)
        if num is None or den is None:
            return None
        return ratio_unit(num.unit, den.unit)
    if isinstance(comp, CumulativeComposition):
        base = registry.metrics.get(comp.base)
        return base.unit if base is not None else None
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
        if (
            m.metric_type == "simple"
            and (m.aggregation is not None or m.weighted_mean is not None)
            and m.unit is None
        ):
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
) -> tuple[Registry, CompiledExpressionSidecar]:
    """Build a registry and immutable compiled expression sidecar.

    Pass 2: assemble all pending IR objects into the registry.
    """
    from marivo.semantic.ir import (
        DimensionIR,
        EntityIR,
        MetricIR,
        RelationshipIR,
    )

    registry = Registry()
    bodies = {}
    field_owners = {}
    catalog_refs: set[Ref[SemanticKindTag]] = set()
    for datasource_ir in datasource_irs:
        registry.datasources[datasource_ir.semantic_id] = datasource_ir
        catalog_refs.add(ref_factory.datasource(datasource_ir.semantic_id))

    for ctx in all_contexts:
        for pending in ctx.pending_definitions:
            ir = pending.definition
            ref = pending.ref
            expression_body = pending.expression_body
            catalog_refs.add(ref)
            if not hasattr(ir, "semantic_id"):
                if isinstance(ir, DomainIR):
                    registry.domains[ir.name] = ir
                continue

            sid = ir.semantic_id

            if isinstance(ir, EntityIR):
                registry.entities[sid] = ir
            elif isinstance(ir, DimensionIR):
                registry.dimensions[sid] = ir
                field_owners[cast("Ref[FieldKind]", ref)] = ref_factory.entity(ir.entity)
            elif isinstance(ir, MeasureIR):
                registry.measures[sid] = ir
                field_owners[cast("Ref[FieldKind]", ref)] = ref_factory.entity(ir.entity)
            elif isinstance(ir, MetricIR):
                registry.metrics[sid] = ir
            elif isinstance(ir, RelationshipIR):
                registry.relationships[sid] = ir
            if expression_body is not None:
                bodies[ref] = expression_body

    _resolve_cumulative_over_axes(registry)
    _resolve_metric_additivity(registry)
    _resolve_metric_unit(registry)
    expression_sidecar = CompiledExpressionSidecar(
        bodies=bodies,
        field_owners=field_owners,
        catalog_refs=frozenset(catalog_refs),
    )
    return registry, expression_sidecar


def _models_root_from_path(path: Path, *, is_external: bool) -> ModelsRoot:
    models_root = Path(path).resolve()
    return ModelsRoot(
        models_root=models_root,
        semantic_root=models_root / "semantic",
        datasource_root=models_root / "datasources",
        is_external=is_external,
    )


def _models_root_from_semantic_root(root: Path) -> ModelsRoot:
    semantic_root = Path(root).resolve()
    return ModelsRoot(
        models_root=semantic_root.parent,
        semantic_root=semantic_root,
        datasource_root=semantic_root.parent / "datasources",
        is_external=False,
    )


def _root_shape_errors(roots: Sequence[ModelsRoot]) -> list[SemanticLoadError]:
    errors: list[SemanticLoadError] = []
    if not roots:
        return errors
    local_root = roots[0].models_root
    seen_external: set[Path] = set()
    for root in roots[1:]:
        if root.models_root == local_root:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_PROJECT,
                    message=(
                        "Configured semantic layer models root duplicates the local "
                        f"project models root: {root.models_root}"
                    ),
                    refs=(str(root.models_root),),
                    hint="Remove the local models/ path from marivo.toml [semantic].layer_paths.",
                )
            )
            continue
        if root.models_root in seen_external:
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_PROJECT,
                    message=(
                        "Configured semantic layer models root is listed more than once: "
                        f"{root.models_root}"
                    ),
                    refs=(str(root.models_root),),
                    hint="Keep each marivo.toml [semantic].layer_paths entry unique.",
                )
            )
            continue
        seen_external.add(root.models_root)
        if not root.models_root.exists():
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_PROJECT,
                    message=(
                        f"Configured semantic layer models root does not exist: {root.models_root}"
                    ),
                    refs=(str(root.models_root),),
                    hint="Point marivo.toml [semantic].layer_paths at an existing models/ directory.",
                )
            )
            continue
        if not root.models_root.is_dir():
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_PROJECT,
                    message=(
                        "Configured semantic layer models root is not a directory: "
                        f"{root.models_root}"
                    ),
                    refs=(str(root.models_root),),
                    hint="Point marivo.toml [semantic].layer_paths at a models/ directory.",
                )
            )
            continue
        if not root.datasource_root.is_dir():
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_PROJECT,
                    message=(
                        "Configured semantic layer models root is missing datasources/: "
                        f"{root.datasource_root}"
                    ),
                    refs=(str(root.datasource_root),),
                    hint="Create datasources/ under the configured models root or remove this layer path.",
                )
            )
        if not root.semantic_root.is_dir():
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_PROJECT,
                    message=(
                        "Configured semantic layer models root is missing semantic/: "
                        f"{root.semantic_root}"
                    ),
                    refs=(str(root.semantic_root),),
                    hint="Create semantic/ under the configured models root or remove this layer path.",
                )
            )
    return errors


def _semantic_source_path(ir: Any) -> str:
    location = getattr(ir, "location", None)
    file = getattr(location, "file", None)
    if isinstance(file, str) and file:
        return file
    return "<unknown>"


def _datasource_duplicate_errors(datasources: Sequence[DatasourceIR]) -> list[SemanticLoadError]:
    errors: list[SemanticLoadError] = []
    seen: dict[str, DatasourceIR] = {}
    for datasource in datasources:
        existing = seen.get(datasource.name)
        if existing is not None:
            first = existing.location.file
            second = datasource.location.file
            errors.append(
                SemanticLoadError(
                    kind=ErrorKind.DUPLICATE_NAME,
                    message=(
                        f"Duplicate datasource name: {datasource.name!r}. "
                        f"First declaration: {first}. Conflicting declaration: {second}."
                    ),
                    refs=(datasource.name, first, second),
                    hint="Rename or remove one datasource declaration.",
                )
            )
        seen.setdefault(datasource.name, datasource)
    return errors


def _domain_duplicate_errors(model_dirs: Sequence[Path]) -> list[SemanticLoadError]:
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


def _semantic_duplicate_errors(contexts: Sequence[LoaderContext]) -> list[SemanticLoadError]:
    errors: list[SemanticLoadError] = []
    seen: dict[str, Any] = {}
    for ctx in contexts:
        for pending in ctx.pending_definitions:
            ir = pending.definition
            if not hasattr(ir, "semantic_id"):
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


def load_project(
    root: Path,
    *,
    models: Sequence[str] | None = None,
    models_roots: Sequence[Path] | None = None,
) -> LoadResult:
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
    root = Path(root)
    if models_roots is None:
        if root.name == AUTHORED_DIR and (root / "semantic").is_dir():
            return LoadResult(
                status="errored",
                errors=(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_PROJECT,
                        message=(
                            "load_project(root) expects the semantic root directory "
                            "`models/semantic/`, but received the parent `models/` directory."
                        ),
                        refs=(str(root),),
                        hint="Pass the `models/semantic/` path to load_project(...).",
                    ),
                ),
            )
        if (root / AUTHORED_DIR / "semantic").is_dir():
            return LoadResult(
                status="errored",
                errors=(
                    SemanticLoadError(
                        kind=ErrorKind.INVALID_PROJECT,
                        message=(
                            "load_project(root) expects the semantic root directory "
                            "`models/semantic/`, but received a workspace root."
                        ),
                        refs=(str(root),),
                        hint=(
                            "Pass `workspace/models/semantic` to load_project(...) or use "
                            "ms.load(workspace_dir=...) for workspace-root loading."
                        ),
                    ),
                ),
            )
        root_specs: tuple[ModelsRoot, ...] = (_models_root_from_semantic_root(root),)
    else:
        root_specs = tuple(
            _models_root_from_path(models_root, is_external=index > 0)
            for index, models_root in enumerate(models_roots)
        )

    errors: list[SemanticError] = []
    warnings: list[StructuredWarning] = []
    registry: Registry | None = None
    expression_sidecar: CompiledExpressionSidecar | None = None
    all_contexts: list[LoaderContext] = []
    all_model_dirs: list[Path] = []
    datasource_irs: list[DatasourceIR] = []
    path_entries: list[str] = []
    module_prefixes: list[str] = []

    errors.extend(_root_shape_errors(root_specs))
    if errors:
        return LoadResult(status="errored", errors=tuple(errors))

    for root_spec in root_specs:
        module_prefix = _module_prefix(root_spec.semantic_root)
        module_prefixes.append(module_prefix)
        _purge_synthetic_modules(module_prefix)
        path_entry = str(root_spec.semantic_root.parent)
        path_entries.append(path_entry)
        sys.path.insert(0, path_entry)

    try:
        for root_spec, module_prefix in zip(root_specs, module_prefixes, strict=True):
            _ensure_package(module_prefix, root_spec.semantic_root)
            datasource_result = load_datasources(root_spec.datasource_root)
            for error in datasource_result.errors:
                errors.append(_wrap_datasource_error(error))
            datasource_irs.extend(datasource_result.datasources)
            all_model_dirs.extend(_discover_model_dirs(root_spec.semantic_root))

        errors.extend(_datasource_duplicate_errors(datasource_irs))

        model_dirs, filter_warnings = _filter_model_dirs(all_model_dirs, models)
        warnings.extend(filter_warnings)
        errors.extend(_domain_duplicate_errors(model_dirs))

        prefix_by_semantic_root = {
            root_spec.semantic_root: module_prefix
            for root_spec, module_prefix in zip(root_specs, module_prefixes, strict=True)
        }

        for model_dir in model_dirs:
            semantic_root = model_dir.parent
            module_prefix = prefix_by_semantic_root[semantic_root]
            ctx = _load_model_dir(
                model_dir,
                semantic_root,
                errors,
                module_prefix=module_prefix,
            )
            if ctx is not None:
                all_contexts.append(ctx)

        registry, expression_sidecar = _build_registry(
            all_contexts,
            datasource_irs=tuple(datasource_irs),
        )

        errors.extend(_semantic_duplicate_errors(all_contexts))

        loaded_models_set = {d.name for d in model_dirs} if models is not None else None
        asm_errors, asm_warnings = assembly_validate(
            registry, expression_sidecar, loaded_models=loaded_models_set
        )
        errors.extend(asm_errors)
        warnings.extend(asm_warnings)

    finally:
        for path_entry in path_entries:
            if path_entry in sys.path:
                sys.path.remove(path_entry)

    status: Literal["ready", "errored"] = "ready" if not errors else "errored"
    filtered_models_tuple = tuple(d.name for d in model_dirs) if models is not None else ()
    compiled_state = (
        build_compiled_state(
            registry=registry,
            sidecar=expression_sidecar,
            selected_root_roles=tuple(
                "external" if root_spec.is_external else "local" for root_spec in root_specs
            ),
            filtered_domains=filtered_models_tuple,
        )
        if status == "ready" and registry is not None and expression_sidecar is not None
        else None
    )
    return LoadResult(
        status=status,
        errors=tuple(errors),
        warnings=tuple(warnings),
        registry=registry if status == "ready" else None,
        expression_sidecar=expression_sidecar if status == "ready" else None,
        compiled_state=compiled_state,
        filtered_models=filtered_models_tuple,
        datasource_irs=tuple(datasource_irs),
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
