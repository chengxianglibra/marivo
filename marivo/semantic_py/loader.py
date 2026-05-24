from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from marivo.semantic_py.errors import (
    SemanticAssemblyError,
    SemanticError,
    SemanticLoadError,
    SourceLocation,
)
from marivo.semantic_py.registry import (
    SemanticProject,
    use_model,
    use_model_candidates,
    use_registry,
)
from marivo.semantic_py.validator import validate_all

_PROJECT_ROOTS: dict[int, tuple[str, Path]] = {}


def load_project(project: SemanticProject, *, reload: bool = False) -> None:
    """Load a trusted local semantic project.

    Python files under the project root are executed directly; this loader is not
    a sandbox.
    """
    with project.lock:
        root = _project_root(project)
        _clear_project_modules(project)
        if not root.exists():
            project.registry.clear()
            project.registry.state = "ready"
            return

        project.registry.clear()
        project.registry.state = "loading"
        importlib.invalidate_caches()

        try:
            with _project_root_on_path(root), use_registry(project.registry):
                if not root.is_dir():
                    error = SemanticAssemblyError(
                        phase="load",
                        kind="ProjectRootInvalid",
                        location=SourceLocation(file=str(root), line=1),
                        function=None,
                        message=f"Semantic project root '{root}' must be a directory.",
                        hint="Set SemanticProject.root to a directory that contains semantic model subdirectories.",
                        refs=[f"root:{root}"],
                    )
                    raise SemanticLoadError([error])
                _install_namespace_packages(root)
                for model_name, model_file, sibling_files in _semantic_model_files(root):
                    model_dir_name = model_name.rsplit(".", 2)[-2]
                    candidates = (model_dir_name, model_dir_name.replace("_", "-"))
                    existing_models = set(project.registry.models)
                    with use_model_candidates(candidates):
                        _exec_module(model_name, model_file)
                    registered_model = _registered_model_name(project, model_name)
                    _reject_unexpected_models(
                        project=project,
                        module_name=model_name,
                        existing_models=existing_models,
                        registered_model=registered_model,
                    )
                    with use_model(registered_model):
                        for module_name, file_path in sibling_files:
                            _exec_module(module_name, file_path)
                validate_all(project.registry)
        except SemanticLoadError as exc:
            _clear_project_modules(project)
            errors = list(exc.errors)
            project.registry.clear()
            project.registry.state = "errored"
            project.registry.load_errors = errors
            raise


def _semantic_model_files(root: Path) -> Iterator[tuple[str, Path, list[tuple[str, Path]]]]:
    namespace = _namespace(root)
    for model_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        model_file = model_dir / "_model.py"
        if not model_file.is_file():
            error = SemanticAssemblyError(
                phase="load",
                kind="ModelFileMissing",
                location=SourceLocation(file=str(model_dir), line=1),
                function=None,
                message=f"Semantic model directory '{model_dir.name}' must contain _model.py.",
                hint="Add _model.py and register the model with marivo.semantic_py.model().",
                refs=[f"model_dir:{model_dir.name}"],
            )
            raise SemanticLoadError([error])

        package_name = f"{namespace}.{model_dir.name}"
        model_module = f"{package_name}._model"
        sibling_files: list[tuple[str, Path]] = []

        for file_path in sorted(model_dir.glob("*.py")):
            if file_path.name in {"__init__.py", "_model.py"}:
                continue
            sibling_files.append((f"{package_name}.{file_path.stem}", file_path))
        yield model_module, model_file, sibling_files


def _registered_model_name(project: SemanticProject, module_name: str) -> str:
    candidates = [
        module_name.rsplit(".", 2)[-2],
        module_name.rsplit(".", 2)[-2].replace("_", "-"),
    ]
    for candidate in candidates:
        if candidate in project.registry.models:
            return candidate
    error = SemanticAssemblyError(
        phase="load",
        kind="ModelRegistrationMissing",
        location=None,
        function=None,
        message=f"Semantic module '{module_name}' did not register an unambiguous model.",
        hint="Each model directory _model.py must call marivo.semantic_py.model().",
        refs=[f"module:{module_name}"],
    )
    raise SemanticLoadError([error])


def _reject_unexpected_models(
    *,
    project: SemanticProject,
    module_name: str,
    existing_models: set[str],
    registered_model: str,
) -> None:
    unexpected_models = sorted(set(project.registry.models) - existing_models - {registered_model})
    if not unexpected_models:
        return
    error = SemanticAssemblyError(
        phase="load",
        kind="UnexpectedModelRegistration",
        location=None,
        function=None,
        message=(
            f"Semantic module '{module_name}' registered unexpected models: "
            f"{', '.join(unexpected_models)}."
        ),
        hint="Each model directory _model.py must register only the model represented by that directory.",
        refs=[f"module:{module_name}", *(f"model:{name}" for name in unexpected_models)],
    )
    raise SemanticLoadError([error])


def _namespace(root: Path) -> str:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return f"_marivo_semantic_py_{digest}"


def _project_root(project: SemanticProject) -> Path:
    root = Path(project.root)
    cache_key = id(project)
    cached = _PROJECT_ROOTS.get(cache_key)
    if root.exists():
        resolved = root.resolve()
        _PROJECT_ROOTS[cache_key] = (project.root, resolved)
        return resolved
    if cached is not None and cached[0] == project.root:
        return cached[1]
    return root


def _install_namespace_packages(root: Path) -> None:
    namespace = _namespace(root)
    root_module = types.ModuleType(namespace)
    root_module.__package__ = namespace
    root_module.__file__ = str(root)
    root_module.__path__ = [str(root)]
    sys.modules[namespace] = root_module

    for model_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        package_name = f"{namespace}.{model_dir.name}"
        package_module = types.ModuleType(package_name)
        package_module.__package__ = package_name
        package_module.__file__ = str(model_dir)
        package_module.__path__ = [str(model_dir)]
        sys.modules[package_name] = package_module


def _exec_module(module_name: str, file_path: Path) -> None:
    if module_name in sys.modules:
        return

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        error = SemanticAssemblyError(
            phase="load",
            kind="ModuleSpecUnavailable",
            location=SourceLocation(file=str(file_path), line=1),
            function=None,
            message=f"Could not create an import spec for '{file_path}'.",
            hint=None,
            refs=[f"module:{module_name}"],
        )
        raise SemanticLoadError([error])

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except SemanticLoadError:
        raise
    except SemanticError as exc:
        raise SemanticLoadError([exc]) from exc
    except Exception as exc:
        error = SemanticAssemblyError(
            phase="load",
            kind="ModuleLoadFailed",
            location=SourceLocation(file=str(file_path), line=1),
            function=None,
            message=f"Failed to load semantic module '{module_name}': {exc}",
            hint="Check the semantic project Python module for import-time errors.",
            refs=[f"module:{module_name}"],
        )
        raise SemanticLoadError([error]) from exc


def _clear_project_modules(project: SemanticProject) -> None:
    root = _project_root(project)
    namespace = _namespace(root)
    for module_name in list(sys.modules):
        if module_name == namespace or module_name.startswith(f"{namespace}."):
            del sys.modules[module_name]


@contextmanager
def _project_root_on_path(root: Path) -> Iterator[None]:
    root_path = str(root)
    original_path = list(sys.path)
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    try:
        yield
    finally:
        sys.path[:] = original_path
