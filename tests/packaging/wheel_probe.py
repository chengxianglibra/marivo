"""Installed-origin assertions and public graph journey process entrypoint."""

from __future__ import annotations

import atexit
import hashlib
import importlib.metadata
import inspect
import json
import os
import sys
import sysconfig
from pathlib import Path

import pytest


def assert_installed_origin() -> dict[str, object]:
    import marivo

    site = Path(sysconfig.get_path("purelib")).resolve()
    assert site.is_relative_to(Path(sys.prefix)), "expected an isolated virtual environment"
    package = site / "marivo"
    assert Path(marivo.__file__).resolve() == package / "__init__.py", "foreign Marivo import"
    origins: dict[str, str] = {}
    for name, module in tuple(sys.modules.items()):
        if name == "marivo" or name.startswith("marivo."):
            location = getattr(module, "__file__", None)
            if location is not None:
                assert Path(location).resolve().is_relative_to(package), (name, location)
                origins[name] = str(Path(location).resolve())
            for path in getattr(module, "__path__", ()):
                assert Path(path).resolve().is_relative_to(package), (name, path)
    distributions = [
        item for item in importlib.metadata.distributions() if item.metadata["Name"] == "marivo"
    ]
    assert len(distributions) == 1
    distribution = distributions[0]
    assert (
        Path(str(distribution.locate_file("marivo/__init__.py"))).resolve()
        == package / "__init__.py"
    )
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert "archive_info" in direct and "dir_info" not in direct, direct
    assert direct["archive_info"]["hashes"]["sha256"] == os.environ["MARIVO_WHEEL_SHA256"]
    return {
        "python": sys.executable,
        "package": str(package),
        "direct_url": direct,
        "modules": origins,
    }


def watch_installed_origin() -> None:
    """Record package authority at process start and after normal completion."""
    directory = Path(os.environ["MARIVO_INSTALLED_ORIGIN_DIR"])
    directory.mkdir(parents=True, exist_ok=True)

    def capture(phase: str) -> None:
        path = directory / f"{os.getpid()}-{phase}.json"
        try:
            origin = assert_installed_origin()
            modules = origin.pop("modules")
            origin["modules_sha256"] = hashlib.sha256(
                json.dumps(modules, sort_keys=True).encode()
            ).hexdigest()
        except BaseException as error:
            path.write_text(json.dumps({"error": str(error), "pid": os.getpid()}))
            raise
        path.write_text(
            json.dumps({"pid": os.getpid(), "phase": phase, "origin": origin}, sort_keys=True)
            + "\n"
        )

    capture("start")
    atexit.register(capture, "exit")


def pytest_sessionstart(session: pytest.Session) -> None:
    assert_installed_origin()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    result = assert_installed_origin()
    Path(os.environ["MARIVO_INSTALLED_ORIGIN_REPORT"]).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )


def surface_snapshot() -> list[dict[str, object]]:
    import marivo.analysis as mv
    from marivo._help.render import render_help_text
    from marivo.analysis._capabilities.registry import REGISTRY

    exports = {entry.name: entry for provider in REGISTRY.providers for entry in provider.exports}
    result: list[dict[str, object]] = []
    public_names: object = vars(mv)["__all__"]
    assert isinstance(public_names, list)
    names = tuple(str(name) for name in public_names)
    assert list(names) == public_names
    from tests.surface.exports import ANALYSIS_PUBLIC

    assert set(names) == set(exports) == set(ANALYSIS_PUBLIC)
    for logical_name, materialized_name in (
        ("LogicalAnalysisDomain", "MaterializedAnalysisDomain"),
        ("LogicalNumericRelation", "MaterializedNumericRelation"),
        ("LogicalRatioRelation", "MaterializedRatioRelation"),
    ):
        logical = getattr(mv, logical_name)
        materialized = getattr(mv, materialized_name)
        assert not any(hasattr(logical, member) for member in ("render", "show", "to_pandas"))
        assert not hasattr(materialized, "render")
        assert all(callable(getattr(materialized, member)) for member in ("show", "to_pandas"))
    for family in ("Deviation", "TimeRun", "Association", "Forecast"):
        logical = getattr(mv, f"Logical{family}Result")
        materialized = getattr(mv, f"Materialized{family}Result")
        assert callable(logical.execute)
        assert not hasattr(logical, "to_pandas")
        assert all(callable(getattr(materialized, member)) for member in ("show", "contract"))
    for name in names:
        value = getattr(mv, name)
        entry = exports[name]
        assert value is entry.implementation
        signatures = {"__call__": str(inspect.signature(value))} if callable(value) else {}
        if inspect.isclass(value):
            for member_name, member in inspect.getmembers(value, inspect.isfunction):
                if not member_name.startswith("_"):
                    signatures[member_name] = str(inspect.signature(member))
        result.append(
            {
                "name": name,
                "target": entry.target,
                "signatures": signatures,
                "help": render_help_text("analysis." + entry.target)[0],
            }
        )
    return result


if __name__ == "__main__":
    assert_installed_origin()
    mode = sys.argv[1]
    if mode == "surface":
        report: object = surface_snapshot()
    elif mode == "guard":
        report = assert_installed_origin()
    elif mode == "install-hook":
        hook = Path(sysconfig.get_path("purelib")) / "marivo_installed_origin.pth"
        assert not hook.exists()
        bootstrap = (
            "if os.environ.get('MARIVO_INSTALLED_ORIGIN_DIR'):\n"
            f"    sys.path.insert(0, {str(Path.cwd())!r})\n"
            "    from tests.packaging.wheel_probe import watch_installed_origin\n"
            "    watch_installed_origin()\n"
        )
        hook.write_text(f"import os, sys; exec({bootstrap!r})\n")
        report = {"hook": str(hook)}
    else:
        if sys.argv[3].startswith("a"):
            from tests.packaging.relation_journeys import journey
        else:
            from tests.packaging.graph_journeys import journey

        report = journey(mode, Path(sys.argv[2]), sys.argv[3], sys.argv[4])
        report["origin"] = assert_installed_origin()
    Path(sys.argv[-1]).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
