"""Loader for project-level datasource declarations."""

from __future__ import annotations

import sys
import types
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha1
from importlib import util as importlib_util
from pathlib import Path

from marivo._authoring.loading import _source_loading
from marivo.config import AUTHORED_DIR, DATASOURCES_DIR, PROJECT_MANIFEST
from marivo.datasource.authoring import _DATASOURCE_CTX, DatasourceLoaderContext
from marivo.datasource.errors import (
    DatasourceDuplicateError,
    DatasourceError,
    DatasourceLoadError,
    repair,
)
from marivo.datasource.ir import DatasourceIR


@dataclass(frozen=True)
class DatasourceLoadResult:
    datasources: tuple[DatasourceIR, ...]
    errors: tuple[Exception, ...]


def _module_prefix(root: Path) -> str:
    digest = sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"_marivo_datasource_{digest}"


def _purge_synthetic_modules(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            del sys.modules[name]


def _ensure_package(name: str, path: Path) -> None:
    package = types.ModuleType(name)
    package.__file__ = str(path)
    package.__package__ = name
    package.__path__ = [str(path)]
    sys.modules[name] = package


def _execute_file(
    filepath: Path,
    ctx: DatasourceLoaderContext,
    errors: list[Exception],
    *,
    module_name: str,
    package_name: str,
) -> None:
    token = _DATASOURCE_CTX.set(ctx)
    try:
        spec = importlib_util.spec_from_file_location(module_name, filepath)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create module spec for {filepath}")
        module = importlib_util.module_from_spec(spec)
        module.__package__ = package_name
        sys.modules[module_name] = module
        with _source_loading(filepath):
            spec.loader.exec_module(module)
    except Exception as exc:
        if isinstance(exc, DatasourceError):
            errors.append(exc)
        else:
            errors.append(
                DatasourceLoadError(
                    message=f"Error executing {filepath}: {exc}",
                    expected="a loadable datasource declaration",
                    received=str(exc),
                    location=str(filepath),
                    repair=repair(
                        kind="reload",
                        canonical_id="load",
                        action="Fix the datasource declaration and reload it.",
                    ),
                )
            )
    finally:
        _DATASOURCE_CTX.reset(token)


def load_datasources(root: Path) -> DatasourceLoadResult:
    """Load declarations from an exact datasource directory.

    Internal loader used by ``store.load_all()``.  Not part of the public
    ``md.*`` surface — use ``md.list()`` or ``md.describe()`` to browse
    configured datasources, and ``md.register()`` to create new ones.
    """
    result = _load_datasource_directory(root)
    return DatasourceLoadResult(
        datasources=result.datasources,
        errors=(*result.errors, *_duplicate_errors(result.datasources)),
    )


def _load_datasource_directory(root: Path) -> DatasourceLoadResult:
    errors: list[Exception] = []
    if not root.exists():
        return DatasourceLoadResult(datasources=(), errors=())
    if not root.is_dir():
        return DatasourceLoadResult(
            datasources=(),
            errors=(
                DatasourceLoadError(
                    message=f"Datasource path {root} exists but is not a directory.",
                    expected="a datasource declaration directory",
                    received=str(root),
                    location=str(root),
                    repair=repair(
                        kind="reload",
                        canonical_id="load",
                        action="Point loading at a datasource directory.",
                    ),
                ),
            ),
        )

    expected_root: Path | None = None
    if (root / PROJECT_MANIFEST).is_file() or (root / DATASOURCES_DIR).is_dir():
        expected_root = root / DATASOURCES_DIR
    elif (root / "datasources").is_dir() and (
        root.name == AUTHORED_DIR or (root / "semantic").is_dir()
    ):
        expected_root = root / "datasources"
    elif root.name == "semantic" and (root.parent / "datasources").is_dir():
        expected_root = root.parent / "datasources"
    if expected_root is not None:
        return DatasourceLoadResult(
            datasources=(),
            errors=(
                DatasourceLoadError(
                    message=f"load_datasources() expects a datasource directory, got {root}.",
                    expected=str(expected_root),
                    received=str(root),
                    location=str(root),
                    repair=repair(
                        kind="reload",
                        canonical_id="load",
                        action=(
                            f"Pass {expected_root} to load_datasources(), or use "
                            "md.load(workspace_dir=...) with the workspace root."
                        ),
                    ),
                ),
            ),
        )

    prefix = _module_prefix(root)
    _purge_synthetic_modules(prefix)
    _ensure_package(prefix, root)
    ctx = DatasourceLoaderContext()
    for child in sorted(root.iterdir()):
        if not child.is_file() or child.suffix != ".py" or child.name.startswith("."):
            continue
        _execute_file(child, ctx, errors, module_name=f"{prefix}.{child.stem}", package_name=prefix)

    return DatasourceLoadResult(datasources=tuple(ctx.pending_objects), errors=tuple(errors))


def _duplicate_errors(datasources: Sequence[DatasourceIR]) -> tuple[DatasourceDuplicateError, ...]:
    errors: list[DatasourceDuplicateError] = []
    seen: dict[str, DatasourceIR] = {}
    for datasource in datasources:
        existing = seen.get(datasource.name)
        if existing is not None:
            first = existing.location.file
            second = datasource.location.file
            errors.append(
                DatasourceDuplicateError(
                    message=(
                        f"Duplicate datasource name: {datasource.name!r}. "
                        f"First declaration: {first}. Conflicting declaration: {second}."
                    ),
                    expected="a unique datasource name across project model roots",
                    received=datasource.name,
                    location=second,
                    repair=repair(
                        kind="reauthor",
                        canonical_id="load",
                        action="Rename or remove one conflicting datasource declaration.",
                    ),
                )
            )
        seen.setdefault(datasource.name, datasource)
    return tuple(errors)


def _models_root_errors(roots: Sequence[Path]) -> tuple[DatasourceLoadError, ...]:
    """Validate ordered model roots; only the first, local root may be absent."""
    errors: list[DatasourceLoadError] = []
    seen: set[Path] = set()
    for index, candidate in enumerate(roots):
        root = candidate.resolve()
        problems: list[tuple[str, Path, str]] = []
        if root in seen:
            message = (
                "Configured semantic layer models root duplicates the local project models root"
                if root == roots[0].resolve()
                else "Configured semantic layer models root is listed more than once"
            )
            problems.append((message, root, "Keep each configured models root unique."))
        elif index > 0 and not root.exists():
            problems.append(
                (
                    "Configured semantic layer models root does not exist",
                    root,
                    "Point marivo.toml [semantic].layer_paths at an existing models/ directory.",
                )
            )
        elif root.exists() and not root.is_dir():
            problems.append(
                (
                    "Configured semantic layer models root is not a directory",
                    root,
                    "Point model loading at a models/ directory.",
                )
            )
        elif index > 0:
            for child in ("datasources", "semantic"):
                if not (root / child).is_dir():
                    problems.append(
                        (
                            f"Configured semantic layer models root is missing {child}/",
                            root / child,
                            f"Create {child}/ under the configured models root or remove this layer path.",
                        )
                    )
        seen.add(root)
        for message, location, action in problems:
            errors.append(
                DatasourceLoadError(
                    message=f"{message}: {location}",
                    expected="valid distinct project model roots",
                    received=str(location),
                    location=str(location),
                    repair=repair(kind="configure", canonical_id="load", action=action),
                )
            )
    return tuple(errors)


def _load_models_datasources(roots: Sequence[Path]) -> DatasourceLoadResult:
    """Collect declarations from model roots validated by ``_models_root_errors``."""
    datasources: list[DatasourceIR] = []
    errors: list[Exception] = []
    for root in roots:
        result = _load_datasource_directory(root / "datasources")
        datasources.extend(result.datasources)
        errors.extend(result.errors)
    errors.extend(_duplicate_errors(datasources))
    return DatasourceLoadResult(datasources=tuple(datasources), errors=tuple(errors))
