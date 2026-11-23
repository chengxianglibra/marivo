"""Two-pass loading of authored Semantic declarations.

This is the **low-level** loader.  Agent-facing loading goes through
``ms.load(workspace_dir=...)`` which returns a browseable
``SemanticCatalog``.  The pipeline here (``load_project``) returns a raw
``LoadResult`` and accepts only the ``models/semantic/`` root; use it when
you need the unassembled result rather than a catalog."""

from __future__ import annotations

import ast
import stat
import sys
import types
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path
from typing import Literal, cast

from ibis.common.deferred import Deferred

from marivo._authoring.loading import (
    _authored_file_spec,
    _authored_imports,
    _exception_chain_key,
    _execution_diagnostic,
    _new_module_prefix,
    _source_loading,
)
from marivo._definition.deferred import resolver_of, root_declaration
from marivo._definition.types import InputContext
from marivo._kernel.config import AUTHORED_DIR
from marivo._kernel.refs import FieldKind, Ref, SemanticKindTag
from marivo._kernel.refs import ref as ref_factory
from marivo.datasource.errors import (
    DatasourceDuplicateError,
    DatasourceError,
    DatasourceLoadError,
)
from marivo.datasource.spi import (
    DatasourceIR,
    DatasourceSourceLocation,
    default_datasource,
    load_models_datasources,
    models_root_errors,
)
from marivo.semantic._compiled_state import (
    CompilationGraph,
    build_compiled_state,
    definition_graph,
    dependent_closure,
)
from marivo.semantic._expression_values import _ExpressionCarrier
from marivo.semantic._model_source import _ModelSources
from marivo.semantic._symbolic_binding import publish_declarations
from marivo.semantic.compiled_types import CompiledSemanticState
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    StructuredWarning,
)
from marivo.semantic.expressions.inputs import _as_deferred
from marivo.semantic.expressions.ir import CompiledExpressionSidecar
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityIR,
    MeasureIR,
    RelationshipIR,
    SourceLocation,
)
from marivo.semantic.loading_context import (
    _LOADER_CTX,
    LoaderContext,
)
from marivo.semantic.loading_diagnostics import domain_duplicate_errors, semantic_duplicate_errors
from marivo.semantic.static_check.assembly import assembly_validate
from marivo.semantic.static_check.registry import Registry

__all__ = [
    "LoadResult",
]


def _wrap_datasource_error(error: Exception) -> SemanticLoadError:
    if isinstance(error, DatasourceError):
        if isinstance(error, DatasourceDuplicateError):
            kind = ErrorKind.DUPLICATE_NAME
            refs = ((error.received,) if error.received else ()) + error.declaration_paths
            hint = "Keep each datasource name unique under models/datasources/."
        elif isinstance(error, DatasourceLoadError):
            kind = ErrorKind.INVALID_PROJECT
            refs = ()
            hint = "Check models/datasources/*.py datasource declarations."
        else:
            kind = ErrorKind.ORGANIZATION_ERROR
            refs = (error.received,) if error.received else ()
            hint = "Check models/datasources/*.py datasource declarations."
        source = error.source_location
        wrapped = SemanticLoadError(
            kind=kind,
            message=error.message,
            refs=refs,
            hint=error.repair.action if error.repair is not None else hint,
            expected=error.expected,
            received=error.received,
            location=SourceLocation(source.file, source.line) if source else None,
            location_label=error.location if source is None else None,
            repair=error.repair,
            exception_type=error.exception_type,
            traceback=error.traceback,
        )
        wrapped.__cause__ = error
        return wrapped
    wrapped = SemanticLoadError(
        kind=ErrorKind.ORGANIZATION_ERROR,
        message=str(error),
        hint="Check models/datasources/*.py datasource declarations.",
    )
    wrapped.__cause__ = error
    return wrapped


@dataclass(frozen=True)
class LoadResult:
    """Result of a project load attempt."""

    status: Literal["ready", "partial", "errored"]
    errors: tuple[SemanticError, ...] = ()
    warnings: tuple[StructuredWarning, ...] = ()
    registry: Registry | None = None
    expression_sidecar: CompiledExpressionSidecar | None = None
    compiled_state: CompiledSemanticState | None = None
    filtered_models: tuple[str, ...] = ()
    datasource_irs: tuple[DatasourceIR, ...] = ()
    excluded_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelsRoot:
    """Internal authored models root used by the semantic loader."""

    models_root: Path
    semantic_root: Path
    datasource_root: Path
    is_external: bool = False


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


def _is_cache_only_model_dir(root: Path) -> bool:
    """Recognize cache residue without following links or reading cache contents."""
    if not stat.S_ISDIR(root.lstat().st_mode):
        return False
    pending = [root]
    has_cache = False
    while pending:
        for child in pending.pop().iterdir():
            if not stat.S_ISDIR(child.lstat().st_mode):
                return False
            if child.name == "__pycache__":
                has_cache = True
            else:
                pending.append(child)
    return has_cache


def _discover_model_dirs(root: Path) -> list[Path]:
    """Find top-level subdirectories that could contain domain definitions.

    Returns directories sorted by name for deterministic load order.
    """
    if not root.exists():
        return []
    dirs = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and child.name != "__pycache__":
            if not (child / "_domain.py").exists() and _is_cache_only_model_dir(child):
                continue
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


def _ensure_package(name: str, path: Path) -> None:
    """Install a lightweight package module with a filesystem search path."""
    package = types.ModuleType(name)
    package.__file__ = str(path)
    package.__package__ = name
    package.__path__ = [str(path)]
    sys.modules[name] = package


def _entity_constructor_decorator_error(
    filepath: Path, exc: Exception, sources: _ModelSources
) -> SemanticLoadError | None:
    """Classify only an executed @ms.entity(...) application that returned Ref."""
    if type(exc) is not TypeError or str(exc) != "'Ref' object is not callable":
        return None
    offending_line: int | None = None
    offending_scope: dict[str, object] | None = None
    traceback = exc.__traceback__
    while traceback is not None:
        if Path(traceback.tb_frame.f_code.co_filename) == filepath:
            offending_line = traceback.tb_lineno
            offending_scope = {
                **traceback.tb_frame.f_globals,
                **traceback.tb_frame.f_locals,
            }
        traceback = traceback.tb_next
    if offending_line is None or offending_scope is None:
        return None
    semantic_module = sys.modules.get("marivo.semantic")
    if semantic_module is None:
        return None
    entity_constructor = getattr(semantic_module, "entity", None)
    source = sources.get(str(filepath))
    if source is None:
        return None

    module_aliases: set[str] = set()
    direct_aliases: set[str] = set()
    bare_module = False
    for statement in source.tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "marivo.semantic":
                    if alias.asname is None:
                        bare_module = True
                    else:
                        module_aliases.add(alias.asname)
        elif isinstance(statement, ast.ImportFrom):
            if statement.module == "marivo":
                module_aliases.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name == "semantic"
                )
            elif statement.module == "marivo.semantic":
                direct_aliases.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name == "entity"
                )

    for node in ast.walk(source.tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not decorator.lineno <= offending_line <= (decorator.end_lineno or decorator.lineno):
                continue
            target = decorator.func
            direct = (
                isinstance(target, ast.Name)
                and target.id in direct_aliases
                and offending_scope.get(target.id) is entity_constructor
            )
            qualified = (
                isinstance(target, ast.Attribute)
                and target.attr == "entity"
                and isinstance(target.value, ast.Name)
                and target.value.id in module_aliases
                and offending_scope.get(target.value.id) is semantic_module
            )
            bare = (
                bare_module
                and isinstance(target, ast.Attribute)
                and target.attr == "entity"
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "semantic"
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "marivo"
                and getattr(offending_scope.get("marivo"), "semantic", None) is semantic_module
            )
            if direct or qualified or bare:
                return SemanticLoadError(
                    kind=ErrorKind.ENTITY_CONSTRUCTOR_AS_DECORATOR,
                    message="ms.entity(name=...) returns a native Entity symbol and cannot decorate a definition.",
                    location=SourceLocation(str(filepath), decorator.lineno),
                    expected="name = ms.entity(...) without a decorated function body",
                    received="@ms.entity(...) applied to a definition",
                    constraint_id=ConstraintId.ENTITY_CONSTRUCTOR_ASSIGNMENT,
                )
    return None


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
        spec = _authored_file_spec(module_name, filepath, ctx._sources.capture)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create module spec for {filepath}")
        module = importlib_util.module_from_spec(spec)
        module.__package__ = package_name
        sys.modules[module_name] = module
        with _source_loading(filepath):
            spec.loader.exec_module(module)
    except Exception as exc:
        if isinstance(exc, SemanticError):
            if exc.traceback is None:
                diagnostic = _execution_diagnostic(exc, filepath)
                exc.exception_type = diagnostic.exception_type
                exc.traceback = diagnostic.traceback
                if exc.location is None:
                    exc.location = SourceLocation(diagnostic.file, diagnostic.line)
            errors.append(exc)
        elif isinstance(exc, DatasourceError):
            if exc.traceback is None:
                diagnostic = _execution_diagnostic(exc, filepath)
                exc.exception_type = diagnostic.exception_type
                exc.traceback = diagnostic.traceback
                exc.source_location = DatasourceSourceLocation(diagnostic.file, diagnostic.line)
            errors.append(_wrap_datasource_error(exc))
        else:
            diagnostic = _execution_diagnostic(exc, filepath)
            entity_decorator_error = _entity_constructor_decorator_error(
                filepath, exc, ctx._sources
            )
            error = entity_decorator_error or SemanticLoadError(
                kind=ErrorKind.ORGANIZATION_ERROR,
                message=str(exc),
                location=SourceLocation(diagnostic.file, diagnostic.line),
                hint=diagnostic.action,
            )
            error.exception_type = diagnostic.exception_type
            error.traceback = diagnostic.traceback
            error.__cause__ = exc
            errors.append(error)
    finally:
        _LOADER_CTX.reset(token)


def _load_model_dir(
    model_dir: Path,
    errors: list[SemanticError],
    *,
    module_prefix: str,
    sources: _ModelSources,
    project_root: Path | None = None,
    datasource_irs: tuple[DatasourceIR, ...] = (),
) -> LoaderContext | None:
    """Load a single model directory.

    Returns the LoaderContext with pending objects, or None on critical failure.
    """
    model_file = model_dir / "_domain.py"
    model_name = model_dir.name

    if not model_file.exists():
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.DOMAIN_FILE_MISSING,
                message=f"Domain directory {model_name!r} is missing _domain.py.",
                refs=(model_name,),
                hint=(
                    f"Create models/semantic/{model_name}/_domain.py with "
                    'ms.domain(name="<domain>", owner="Mina Zhang"). '
                    'Run marivo.help("authoring") for the domain authoring contract.'
                ),
            )
        )
        return None

    model_package = f"{module_prefix}.{model_name}"
    ctx = LoaderContext(
        model_name=model_name,
        file_path=str(model_file),
        current_model_file=str(model_file),
        datasource_irs=datasource_irs,
        project_root=project_root,
        _model_package=model_package,
        _sources=sources,
    )
    _ensure_package(model_package, model_dir)
    _execute_file(
        model_file,
        ctx,
        errors,
        module_name=f"{model_package}._domain",
        package_name=model_package,
    )

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
                    'Run marivo.help("authoring") for the domain authoring contract.'
                ),
            )
        )
        return None

    if model_names[0] != model_name:
        errors.append(
            SemanticLoadError(
                kind=ErrorKind.DOMAIN_FILE_MISMATCH,
                message=f"Domain name {model_names[0]!r} does not match directory name {model_name!r}.",
                refs=(model_name, model_names[0]),
            )
        )
        return None

    for pending in ctx.pending_definitions:
        ir = pending.definition
        if isinstance(ir, DomainIR) and ir.default:
            ctx.default_domain = ir.name
            break

    sibling_files: list[Path] = []
    for child in sorted(model_dir.iterdir()):
        if not child.is_file():
            continue
        if child.suffix != ".py":
            continue
        if _is_excluded_file(child.name):
            continue
        sibling_files.append(child)

    module_names = [f"{model_package}._domain"]
    for sibling in sibling_files:
        module_name = f"{model_package}.{sibling.stem}"
        module_names.append(module_name)
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

    _collect_module_declarations(ctx, module_names)
    return ctx


def _collect_module_declarations(ctx: LoaderContext, module_names: Sequence[str]) -> None:
    """Record module-level Measure/Metric declarations once, in file order.

    Only declarations decorated in this model directory's own files are
    collected; an alias or an import of a declaration from another model never
    re-publishes it here.
    """

    modules = [sys.modules[name] for name in module_names if name in sys.modules]
    own_files = {getattr(module, "__file__", None) for module in modules}
    for module in modules:
        for export_name, value in vars(module).items():
            if export_name.startswith("_"):
                continue
            captured_context = InputContext()
            if isinstance(value, _ExpressionCarrier):
                captured_context = value._context
                value = _as_deferred(value)
            if not isinstance(value, Deferred):
                continue
            declaration = root_declaration(value)
            if declaration is None:
                continue
            location = declaration.location
            if location is None or location.path not in own_files:
                continue
            if not any(resolver_of(value) is resolver_of(item) for item in ctx.declarations):
                ctx.declarations.append(value)
                ctx.declaration_contexts[id(resolver_of(value))] = captured_context


def _build_registry(
    all_contexts: list[LoaderContext],
    *,
    datasource_irs: tuple[DatasourceIR, ...] = (),
) -> tuple[Registry, CompiledExpressionSidecar, tuple[SemanticError, ...]]:
    """Build a registry and immutable compiled expression sidecar.

    Pass 2: assemble all pending IR objects into the registry.
    """

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
            if isinstance(ir, DomainIR):
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
            if isinstance(ir, RelationshipIR):
                registry.relationships[sid] = ir
            if expression_body is not None:
                bodies[ref] = expression_body

    expression_sidecar = CompiledExpressionSidecar(
        bodies=bodies,
        field_owners=field_owners,
        catalog_refs=frozenset(catalog_refs),
    )
    return registry, expression_sidecar, ()


def _exclude_unavailable_sources(
    registry: Registry, sidecar: CompiledExpressionSidecar, contexts: Sequence[LoaderContext]
) -> set[Ref[SemanticKindTag]]:
    """Remove unbound Entities and their physical dependents before assembly."""
    definitions, dependencies = definition_graph(registry, sidecar)
    roots = {
        ref
        for ref, definition in definitions.items()
        if isinstance(definition, EntityIR) and definition.datasource not in registry.datasources
    }
    excluded = dependent_closure(roots, dependencies)
    for context in contexts:
        context.pending_definitions[:] = [
            definition
            for definition in context.pending_definitions
            if definition.ref not in excluded
        ]
    return excluded


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


def _load_root_specs(
    root: Path, models_roots: Sequence[Path] | None
) -> tuple[ModelsRoot, ...] | LoadResult:
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
    return root_specs


def _distinct_source_errors(source_errors: tuple[SemanticError, ...]) -> tuple[SemanticError, ...]:
    unique_source_errors: list[SemanticError] = []
    seen_source_errors: set[
        tuple[
            SourceLocation, str, str | None, str, str | None, tuple[tuple[str, str, str, int], ...]
        ]
    ] = set()
    for error in source_errors:
        if error.location is not None:
            cause = error.__cause__
            key = (
                error.location,
                error.kind,
                error.exception_type,
                error.message,
                error.received,
                _exception_chain_key(cause.__cause__ if cause else None),
            )
            if key in seen_source_errors:
                continue
            seen_source_errors.add(key)
        unique_source_errors.append(error)
    return tuple(unique_source_errors)


def load_project(
    root: Path,
    *,
    models: Sequence[str] | None = None,
    models_roots: Sequence[Path] | None = None,
    project_root: Path | None = None,
    allow_partial: bool = False,
) -> LoadResult:
    """Load domains from the semantic project root.

    This is the **low-level** two-pass pipeline.  Prefer
    ``ms.load(workspace_dir=...)``, which returns a ``SemanticCatalog`` and
    accepts a workspace root.  Use ``load_project`` only when you need the raw
    ``LoadResult``.

    Two-pass pipeline:
    1. Discover domain directories and execute their files.
    2. Build registry, validate, and assemble the loaded objects.

    When *models* is specified, only those domain directories are loaded.
    Filtered Relationship references may warn; unresolved required Metric
    dependencies fail loading.

    Note: Each domain directory is expected to contain a ``_domain.py`` file
    that calls ``ms.domain(name=...)``.

    Returns a LoadResult with status, errors, warnings, registry, and sidecar.
    """
    root = Path(root)
    resolved_roots = _load_root_specs(root, models_roots)
    if isinstance(resolved_roots, LoadResult):
        return resolved_roots
    root_specs = resolved_roots

    errors: list[SemanticError] = []
    warnings: list[StructuredWarning] = []
    registry: Registry | None = None
    expression_sidecar: CompiledExpressionSidecar | None = None
    compilation_graph: CompilationGraph | None = None
    all_contexts: list[LoaderContext] = []
    all_model_dirs: list[Path] = []
    datasource_irs: list[DatasourceIR] = [default_datasource()]

    datasource_roots = tuple(spec.models_root for spec in root_specs)
    errors.extend(_wrap_datasource_error(error) for error in models_root_errors(datasource_roots))
    if errors:
        return LoadResult(status="errored", errors=tuple(errors))

    module_prefixes = [_new_module_prefix("semantic") for _ in root_specs]

    for root_spec, module_prefix in zip(root_specs, module_prefixes, strict=True):
        _ensure_package(module_prefix, root_spec.semantic_root)
        all_model_dirs.extend(_discover_model_dirs(root_spec.semantic_root))

    datasource_result = load_models_datasources(datasource_roots, isolate_failures=allow_partial)
    source_errors: tuple[SemanticError, ...] = tuple(
        _wrap_datasource_error(error) for error in datasource_result.errors
    )
    source_errors = _distinct_source_errors(source_errors)
    partial = (
        allow_partial
        and bool(source_errors)
        and all(
            isinstance(error, DatasourceError)
            and not isinstance(error, DatasourceDuplicateError)
            and error.source_location is not None
            for error in datasource_result.errors
        )
    )
    if not partial:
        errors.extend(source_errors)
    datasource_irs.extend(datasource_result.datasources)

    model_dirs, filter_warnings = _filter_model_dirs(all_model_dirs, models)
    warnings.extend(filter_warnings)
    errors.extend(domain_duplicate_errors(model_dirs))

    prefix_by_semantic_root = {
        root_spec.semantic_root: module_prefix
        for root_spec, module_prefix in zip(root_specs, module_prefixes, strict=True)
    }

    sources = _ModelSources()
    with _authored_imports(module_prefixes, sources.capture):
        for model_dir in model_dirs:
            semantic_root = model_dir.parent
            module_prefix = prefix_by_semantic_root[semantic_root]
            ctx = _load_model_dir(
                model_dir,
                errors,
                module_prefix=module_prefix,
                sources=sources,
                project_root=project_root or root.parent.parent,
                datasource_irs=tuple(datasource_irs),
            )
            if ctx is not None:
                all_contexts.append(ctx)

    registry, expression_sidecar, assembly_errors = _build_registry(
        all_contexts,
        datasource_irs=tuple(datasource_irs),
    )
    errors.extend(assembly_errors)

    errors.extend(semantic_duplicate_errors(all_contexts))
    excluded: set[Ref[SemanticKindTag]] = set()
    original_registry: Registry | None = None
    loaded_models_set = {d.name for d in model_dirs} if models is not None else None
    asm_errors, asm_warnings = assembly_validate(
        registry, expression_sidecar, loaded_models=loaded_models_set
    )
    if partial and not errors:
        # Validate authored structure before pruning so an unavailable datasource
        # cannot hide an invalid clock, field binding or relationship.
        discarded_names = {item.name for item in datasource_result.discarded}
        authored_errors, asm_errors = asm_errors, []
        for error in authored_errors:
            if error.kind == ErrorKind.MISSING_DATASOURCE_REF:
                if error.received not in discarded_names:
                    source_errors = (*source_errors, error)
            else:
                asm_errors.append(error)
    if partial and not errors and not asm_errors:
        excluded = _exclude_unavailable_sources(registry, expression_sidecar, all_contexts)
        if excluded:
            original_registry = registry
            registry, expression_sidecar, assembly_errors = _build_registry(
                all_contexts, datasource_irs=tuple(datasource_irs)
            )
            errors.extend(assembly_errors)
            asm_errors, asm_warnings = assembly_validate(
                registry, expression_sidecar, loaded_models=loaded_models_set
            )
    errors.extend(asm_errors)
    warnings.extend(asm_warnings)
    if not errors:
        compilation_graph = CompilationGraph.create(registry, expression_sidecar)
        errors.extend(
            publish_declarations(
                all_contexts,
                registry,
                expression_sidecar,
                graph=compilation_graph,
                excluded=excluded,
                original_registry=original_registry,
            )
        )

    status: Literal["ready", "partial", "errored"] = (
        "errored" if errors else "partial" if partial else "ready"
    )
    filtered_models_tuple = tuple(d.name for d in model_dirs) if models is not None else ()
    compiled_state = (
        build_compiled_state(
            registry=registry,
            sidecar=expression_sidecar,
            selected_root_roles=tuple(
                "external" if root_spec.is_external else "local" for root_spec in root_specs
            ),
            filtered_domains=filtered_models_tuple,
            graph=compilation_graph,
        )
        if status != "errored" and registry is not None and expression_sidecar is not None  # type: ignore[redundant-expr]  # Preserve the runtime check.
        else None
    )
    return LoadResult(
        status=status,
        errors=(*source_errors, *errors) if partial else tuple(errors),
        warnings=tuple(warnings),
        registry=registry if status != "errored" else None,
        expression_sidecar=expression_sidecar if status != "errored" else None,
        compiled_state=compiled_state,
        filtered_models=filtered_models_tuple,
        datasource_irs=tuple(datasource_irs),
        excluded_refs=tuple(sorted(ref.key for ref in excluded)),
    )
