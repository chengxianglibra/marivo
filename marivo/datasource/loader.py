"""Loader for project-level datasource declarations."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from hashlib import sha1
from importlib import util as importlib_util
from pathlib import Path

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
    """Load datasource declarations from ``models/datasources/``.

    Internal loader used by ``store.load_all()``.  Not part of the public
    ``md.*`` surface — use ``md.list()`` or ``md.describe()`` to browse
    configured datasources, and ``md.register()`` to create new ones.
    """
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

    prefix = _module_prefix(root)
    _purge_synthetic_modules(prefix)
    _ensure_package(prefix, root)
    ctx = DatasourceLoaderContext()
    for child in sorted(root.iterdir()):
        if not child.is_file() or child.suffix != ".py" or child.name.startswith("."):
            continue
        _execute_file(child, ctx, errors, module_name=f"{prefix}.{child.stem}", package_name=prefix)

    seen: set[str] = set()
    for ir in ctx.pending_objects:
        if ir.name in seen:
            errors.append(
                DatasourceDuplicateError(
                    message=f"Duplicate datasource name: {ir.name!r}",
                    expected="a unique datasource name",
                    received=ir.name,
                    location="models/datasources/",
                    repair=repair(
                        kind="reauthor",
                        canonical_id="load",
                        action="Rename or remove the duplicate datasource declaration.",
                    ),
                )
            )
        seen.add(ir.name)
    return DatasourceLoadResult(datasources=tuple(ctx.pending_objects), errors=tuple(errors))
