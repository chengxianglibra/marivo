#!/usr/bin/env python3
"""Generate static Pydantic models from OSI-Marivo and AOI JSON Schemas.

Usage:
    python scripts/generate_contract_models.py [--check]

With --check, exits non-zero if generated files differ from committed files.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
OSI_SCHEMA = ROOT / "osi-marivo-spec" / "schema" / "osi-marivo.schema.json"
AOI_SCHEMA = ROOT / "aoi-spec" / "schema" / "aoi.schema.json"
OUTPUT_DIR = ROOT / "marivo" / "contracts" / "generated"

OSI_EXAMPLES = ROOT / "osi-marivo-spec" / "examples"
AOI_EXAMPLES = ROOT / "aoi-spec" / "examples"


def _run_codegen(schema: Path, output: Path, module_name: str) -> None:
    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(schema),
        "--output",
        str(output),
        "--input-file-type",
        "jsonschema",
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.11",
        "--use-standard-collections",
        "--use-union-operator",
        "--field-constraints",
        "--collapse-root-models",
        "--use-schema-description",
        "--enum-field-as-literal",
        "all",
        "--strict-nullable",
        "--extra-fields",
        "forbid",
        "--disable-timestamp",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        print(f"ERROR generating {module_name}:", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    print(f"Generated {_display_path(output)}")


def _schema_version(schema: Path) -> str:
    with schema.open(encoding="utf-8") as f:
        doc = json.load(f)
    version = doc.get("properties", {}).get("version", {}).get("const")
    if version is None:
        version = doc.get("version", "unknown")
    return str(version)


def _write_init(output_dir: Path) -> None:
    init = output_dir / "__init__.py"
    init.write_text(
        '"""Generated contract models - do not edit manually.\n\n'
        "Regenerate with: python scripts/generate_contract_models.py\n"
        '"""\n\n'
        "from pydantic import RootModel\n\n"
        "from . import aoi as aoi\n"
        "from . import osi as osi\n"
        "from .aoi import TimeScope as TimeScope\n\n\n"
        "class AIContext(RootModel[str | osi.AIContext1]):\n"
        '    """Root model accepting either a plain string or structured AI context object."""\n\n'
        "    root: str | osi.AIContext1\n\n\n"
        "AIContextObject = osi.AIContext1\n"
        "Field = osi.FieldModel\n"
        "CustomExtension = osi.CustomExtension\n"
        "Dataset = osi.Dataset\n"
        "DialectExpression = osi.DialectExpression\n"
        "Dimension = osi.Dimension\n"
        "Expression = osi.Expression\n"
        "Metric = osi.Metric\n"
        "OSIDocument = osi.OsiCoreMetadataSpecificationWithMarivoVendorExtensions\n"
        "Relationship = osi.Relationship\n"
        "SemanticModel = osi.SemanticModel\n\n"
        f'OSI_MARIVO_SPEC_VERSION = "{_schema_version(OSI_SCHEMA)}"\n'
        f'AOI_SPEC_VERSION = "{_schema_version(AOI_SCHEMA)}"\n'
    )
    print(f"Generated {_display_path(init)}")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_generated_package(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    osi_output = output_dir / "osi.py"
    _run_codegen(OSI_SCHEMA, osi_output, "OSI")
    _run_codegen(AOI_SCHEMA, output_dir / "aoi.py", "AOI")
    _write_init(output_dir)
    _format_generated_package(output_dir)


def _format_generated_package(output_dir: Path) -> None:
    cmd = [sys.executable, "-m", "ruff", "format", str(output_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        print("ERROR formatting generated models:", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    if result.stdout.strip():
        print(result.stdout.strip())


def _generated_model_classes(module: ModuleType) -> list[type]:
    classes: list[type] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if hasattr(obj, "model_validate"):
            classes.append(obj)
    return classes


def _find_model_with_fields(module: ModuleType, field_names: set[str]) -> type:
    for cls in _generated_model_classes(module):
        if field_names <= set(getattr(cls, "model_fields", {}).keys()):
            return cls
    raise RuntimeError(f"No generated model in {module.__name__} exposes {sorted(field_names)}")


def _validate_osi_examples() -> None:
    osi_models = importlib.import_module("marivo.contracts.generated.osi")
    root_model = _find_model_with_fields(osi_models, {"version", "semantic_model"})

    for example_path in sorted(OSI_EXAMPLES.rglob("*.json")):
        with example_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        root_model.model_validate(payload)


def _validate_aoi_examples() -> None:
    aoi_models = importlib.import_module("marivo.contracts.generated.aoi")
    root_model = None
    for cls in _generated_model_classes(aoi_models):
        root = getattr(cls, "model_fields", None)
        if root is None and hasattr(cls, "model_validate"):
            try:
                if cls.__name__ == "AoiV01":
                    root_model = cls
                    break
            except Exception:
                continue
    if root_model is None:
        for cls in _generated_model_classes(aoi_models):
            if cls.__name__ == "AoiV01":
                root_model = cls
                break
    if root_model is None:
        raise RuntimeError("No AOI root model was generated")

    for example_path in sorted(AOI_EXAMPLES.rglob("*.json")):
        with example_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        root_model.model_validate(payload)


def _validate_examples() -> None:
    _validate_osi_examples()
    _validate_aoi_examples()
    print("All examples validated successfully.")


def _generated_file_bytes(base: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(base): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _check_generated_files(temp_output: Path) -> None:
    expected = _generated_file_bytes(temp_output)
    actual = _generated_file_bytes(OUTPUT_DIR)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            path for path in set(actual) & set(expected) if actual[path] != expected[path]
        )
        print("Generated files differ from committed code.", file=sys.stderr)
        for label, paths in (("missing", missing), ("extra", extra), ("changed", changed)):
            if paths:
                print(f"{label}:", file=sys.stderr)
                for path in paths:
                    print(f"  - {path}", file=sys.stderr)
        raise SystemExit(1)
    print("Generated files match committed code.")


def _check_generation_freshness() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_root = Path(tmp_dir)
        temp_output = temp_root / "marivo" / "contracts" / "generated"
        temp_output.parent.parent.mkdir(parents=True, exist_ok=True)
        _write_generated_package(temp_output)
        _check_generated_files(temp_output)
    _validate_examples()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate contract models from JSON Schemas")
    parser.add_argument("--check", action="store_true", help="CI mode: fail if files differ")
    parser.add_argument("--skip-validation", action="store_true", help="Skip example validation")
    args = parser.parse_args()

    if args.check:
        _check_generation_freshness()
        return

    _write_generated_package(OUTPUT_DIR)
    if not args.skip_validation:
        _validate_examples()


if __name__ == "__main__":
    main()
