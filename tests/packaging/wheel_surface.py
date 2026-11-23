"""Snapshot installed and source public contracts using the same probes."""

from __future__ import annotations

import inspect
import sys
from enum import Enum

import marivo
import marivo.analysis as mv
import marivo.datasource as md
import marivo.ontology as mo
import marivo.semantic as ms
from marivo._help.render import render_help_text
from marivo.analysis._help_specs.manifest import REGISTRY as ANALYSIS_REGISTRY
from marivo.datasource._help_specs.manifest import REGISTRY as DATASOURCE_REGISTRY
from marivo.introspection.live.model import EnvironmentFingerprint
from marivo.introspection.live.render import render_fingerprint
from marivo.semantic._help_specs.manifest import REGISTRY as SEMANTIC_REGISTRY
from tests.surface.exports import ANALYSIS_PUBLIC, DATASOURCE_PUBLIC, SEMANTIC_PUBLIC


def public_snapshot() -> dict[str, object]:
    """Compare native authoring discovery and signatures with the source owner."""
    result: dict[str, object] = {}

    def help_text(target: str | None) -> str:
        fingerprint = EnvironmentFingerprint.current()
        return (
            render_help_text(target)[0]
            .replace("Python: " + sys.executable, "Python: <interpreter>")
            .replace("Package: " + marivo.__file__, "Package: <product module>")
            .replace(
                "\n" + render_fingerprint(fingerprint, reveal=False) + "\n",
                f"\nMarivo {fingerprint.marivo_version} (fingerprint <environment>)\n",
            )
        )

    for owner, module, expected, targets in (
        ("datasource", md, DATASOURCE_PUBLIC, DATASOURCE_REGISTRY.canonical_ids()),
        ("semantic", ms, SEMANTIC_PUBLIC, SEMANTIC_REGISTRY.canonical_ids()),
    ):
        assert set(module.__all__) == set(expected)
        signatures: dict[str, str] = {}
        enumerations: dict[str, dict[str, str]] = {}
        for name in sorted(expected):
            value: object = getattr(module, name)
            if inspect.isclass(value) and issubclass(value, Enum):
                enumerations[name] = {
                    key: str(item.value) for key, item in value.__members__.items()
                }
            elif callable(value):
                signatures[name] = str(inspect.signature(value))
        result[owner] = {
            "signatures": signatures,
            "enumerations": enumerations,
            "help": {target: help_text(owner + "." + target) for target in targets},
        }
    assert callable(mo.load)
    result["roots"] = {
        target or "root": help_text(target)
        for target in (None, "authoring", "analysis", "datasource", "semantic", "ontology")
    }
    return result


def surface_snapshot() -> list[dict[str, object]]:
    exports = {
        entry.name: entry for provider in ANALYSIS_REGISTRY.providers for entry in provider.exports
    }
    result: list[dict[str, object]] = []
    public_names: object = getattr(mv, "__all__", None)
    assert isinstance(public_names, list)
    names = tuple(str(name) for name in public_names)
    assert list(names) == public_names
    assert set(names) == set(exports) == set(ANALYSIS_PUBLIC)
    assert not hasattr(mv, "Query")
    assert callable(mv.Population.show)
    assert not hasattr(mv.Population, "execute")
    for method_name in ("compute", "persist", "show", "to_pandas", "where", "compare", "reduce"):
        assert callable(getattr(mv.AnalysisFrame, method_name))
    assert all(
        not hasattr(mv.AnalysisFrame, member)
        for member in ("execute", "rollup", "drill", "share", "population")
    )
    assert all(not hasattr(mv.Session, member) for member in ("query", "artifact"))
    assert not hasattr(mv, "Artifact")
    assert not hasattr(mv.AnalysisFrame, "artifact")
    assert tuple(inspect.signature(mv.AnalysisFrame.show).parameters) == ("self",)
    for name in names:
        value: object = getattr(mv, name)
        entry = exports[name]
        assert value is entry.implementation
        signatures = (
            {"__call__": str(inspect.signature(value))}
            if callable(value) and not inspect.isclass(value)
            else {}
        )
        if inspect.isclass(value):
            for member_name, member in inspect.getmembers(value, inspect.isfunction):
                if not member_name.startswith("_") and member.__module__.startswith("marivo."):
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
