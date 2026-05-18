#!/usr/bin/env python3
"""Generate static Pydantic models from OSI-Marivo and AOI JSON Schemas.

Usage:
    python scripts/generate_contract_models.py [--check]

With --check, exits non-zero if generated files differ from committed files.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import re
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
AOI_CURRENT_EXAMPLE_FILES = [
    AOI_EXAMPLES / "detect" / "basic-request.json",
    AOI_EXAMPLES / "detect" / "dimension-period-shift-request.json",
]


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


def _patch_aoi_optional_non_null_fields(output: Path) -> None:
    """Preserve omittable-but-non-null AOI request fields in generated models.

    datamodel-code-generator currently renders optional JSON Schema properties
    as ``T | None = None`` even when the property schema itself is non-nullable.
    AOI request optionals intentionally mean "may be omitted"; explicit JSON
    null must still fail Pydantic/FastAPI validation.
    """

    text = output.read_text(encoding="utf-8")
    text = text.replace(
        "from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel\n",
        "from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel, model_validator\n",
    )
    text = text.replace("\n        | None\n    ) = 'normal'", "\n    ) = 'normal'")
    replacements = {
        "    limit: int | None = Field(None, ge=1)": (
            "    limit: int = Field(None, ge=1)  # type: ignore[assignment]"
        ),
        "    min_pairs: int | None = Field(None, ge=1)": (
            "    min_pairs: int = Field(None, ge=1)  # type: ignore[assignment]"
        ),
        "    method: Literal['pearson', 'spearman'] | None = None": (
            "    method: Literal['pearson', 'spearman'] = None  # type: ignore[assignment]"
        ),
        "    decomposition_method: Literal['delta_share'] | None = 'delta_share'": (
            "    decomposition_method: Literal['delta_share'] = 'delta_share'"
        ),
        "    decomposition_limit: int | None = Field(5, ge=1)": (
            "    decomposition_limit: int = Field(5, ge=1)"
        ),
        "    filter: Expression | None = None": (
            "    filter: Expression = None  # type: ignore[assignment]"
        ),
        "    dimension: str | None = Field(None, min_length=1)": (
            "    dimension: str = Field(None, min_length=1)  # type: ignore[assignment]"
        ),
        "    dimensions: list[Dimension] | None = Field(None, min_length=1)": (
            "    dimensions: list[Dimension] = Field(None, min_length=1)  # type: ignore[arg-type]"
        ),
        "    time_scope: TimeScope | None = None": (
            "    time_scope: TimeScope = None  # type: ignore[assignment]"
        ),
        "    current: Slice | None = None": (
            "    current: Slice = None  # type: ignore[assignment]"
        ),
        "    baseline: Slice | None = None": (
            "    baseline: Slice = None  # type: ignore[assignment]"
        ),
        "    detect_dimension: str | None = Field(None, min_length=1)": (
            "    detect_dimension: str = Field(None, min_length=1)  # type: ignore[assignment]"
        ),
        "    scan_dimension: str | None = Field(None, min_length=1)": (
            "    scan_dimension: str = Field(None, min_length=1)  # type: ignore[assignment]"
        ),
        "    candidate_limit: int | None = Field(None, ge=1)": (
            "    candidate_limit: int = Field(None, ge=1)  # type: ignore[assignment]"
        ),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(
        r"    filter: Expression \| None = Field\(\n        None,\n(?P<body>(?:        .+\n)+?)    \)",
        "    filter: Expression = Field(  # type: ignore[assignment]\n"
        "        None,\n"
        r"\g<body>"
        "    )",
        text,
    )
    text = re.sub(
        r"    scan_dimension: str \| None = Field\(\n        None,\n(?P<body>(?:        .+\n)+?)    \)",
        "    scan_dimension: str = Field(  # type: ignore[assignment]\n"
        "        None,\n"
        r"\g<body>"
        "    )",
        text,
    )
    text = re.sub(
        r"    granularity: (Literal\[[^\n]+\]) \| None = \(\n        None\n    \)",
        r"    granularity: \1 = None  # type: ignore[assignment]",
        text,
    )
    text = text.replace(
        "    dimensions: list[Dimension] = Field(None, min_length=1)  # type: ignore[arg-type]\n\n\nclass Observe2",
        "    dimensions: list[Dimension] = Field(None, min_length=1)  # type: ignore[arg-type]\n\n"
        "    @model_validator(mode='after')\n"
        "    def _validate_scalar_branch(self) -> Observe1:\n"
        "        if self.granularity is not None or self.dimensions is not None:\n"
        "            raise ValueError('observe scalar requests must omit granularity and dimensions')\n"
        "        return self\n\n\n"
        "class Observe2",
        1,
    )
    text = text.replace(
        "    dimensions: list[Dimension] = Field(None, min_length=1)  # type: ignore[arg-type]\n\n\nclass Observe3",
        "    dimensions: list[Dimension] = Field(None, min_length=1)  # type: ignore[arg-type]\n\n"
        "    @model_validator(mode='after')\n"
        "    def _validate_time_series_branch(self) -> Observe2:\n"
        "        if self.dimensions is not None:\n"
        "            raise ValueError('observe time-series requests must omit dimensions')\n"
        "        return self\n\n\n"
        "class Observe3",
        1,
    )
    text = text.replace(
        "    dimensions: list[Dimension] = Field(..., min_length=1)\n\n\nclass AnomalyCandidatesResult",
        "    dimensions: list[Dimension] = Field(..., min_length=1)\n\n"
        "    @model_validator(mode='after')\n"
        "    def _validate_segmented_branch(self) -> Observe3:\n"
        "        if self.granularity is not None:\n"
        "            raise ValueError('observe segmented requests must omit granularity')\n"
        "        return self\n\n\n"
        "class AnomalyCandidatesResult",
        1,
    )
    output.write_text(text, encoding="utf-8")


def _patch_osi_generated_model_validators(output: Path) -> None:
    """Preserve OSI invariants that datamodel-code-generator cannot express."""

    text = output.read_text(encoding="utf-8")
    text = text.replace(
        "from pydantic import BaseModel, ConfigDict, Field, RootModel\n",
        "from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator\n",
    )
    text = text.replace(
        "    primary_time_field: str | None = Field(\n"
        "        None,\n"
        '        description="Dataset field used as the metric\'s primary analysis time axis.",\n'
        "        min_length=1,\n"
        "    )\n\n\nclass MarivoDatasetCustomExtension",
        "    primary_time_field: str | None = Field(\n"
        "        None,\n"
        '        description="Dataset field used as the metric\'s primary analysis time axis.",\n'
        "        min_length=1,\n"
        "    )\n\n"
        "    @model_validator(mode='after')\n"
        "    def _validate_additive_dimensions_all(self) -> MarivoMetricExtension:\n"
        "        if self.additive_dimensions is None:\n"
        "            return self\n"
        "        values = [dimension.root for dimension in self.additive_dimensions]\n"
        "        if '__all' in values and values != ['__all']:\n"
        "            raise ValueError(\"additive_dimensions '__all' must not be mixed with explicit fields\")\n"
        "        return self\n\n\n"
        "class MarivoDatasetCustomExtension",
        1,
    )
    text, field_validator_count = re.subn(
        r"(?P<field_model>"
        r"class FieldModel\(BaseModel\):\n"
        r"(?:(?!\nclass Dataset).)*?"
        r"    custom_extensions: list\[MarivoFieldCustomExtension\] \| None = Field\(\n"
        r"        None,\n"
        r"(?:(?!\nclass Dataset).)*?"
        r"        max_length=1,\n"
        r"    \)\n"
        r")\n\nclass Dataset",
        r"\g<field_model>\n"
        "    @model_validator(mode='after')\n"
        "    def _validate_marivo_time_field_extension(self) -> FieldModel:\n"
        "        is_time = self.dimension is not None and self.dimension.is_time is True\n"
        "        extension_count = len(self.custom_extensions or [])\n"
        "        if is_time and extension_count != 1:\n"
        "            raise ValueError('time fields must define exactly one MARIVO field extension')\n"
        "        if not is_time and extension_count:\n"
        "            raise ValueError('non-time fields must not define MARIVO field extensions')\n"
        "        return self\n\n\n"
        "class Dataset",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if field_validator_count != 1:
        raise RuntimeError("Failed to patch OSI FieldModel MARIVO field extension validator")
    output.write_text(text, encoding="utf-8")


def _write_generated_package(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    osi_output = output_dir / "osi.py"
    _run_codegen(OSI_SCHEMA, osi_output, "OSI")
    _patch_osi_generated_model_validators(osi_output)
    aoi_output = output_dir / "aoi.py"
    _run_codegen(AOI_SCHEMA, aoi_output, "AOI")
    _patch_aoi_optional_non_null_fields(aoi_output)
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


def _validate_osi_examples(*, generated_root: Path | None = None) -> None:
    osi_models = _import_generated_module(
        "marivo.contracts.generated.osi", generated_root=generated_root
    )
    root_model = _find_model_with_fields(osi_models, {"version", "semantic_model"})

    for example_path in sorted(OSI_EXAMPLES.rglob("*.json")):
        with example_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        root_model.model_validate(payload)


def _import_generated_module(module_name: str, *, generated_root: Path | None = None) -> ModuleType:
    if generated_root is None:
        return importlib.import_module(module_name)

    module_path = generated_root / f"{module_name.rsplit('.', 1)[-1]}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import generated module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _validate_aoi_examples(*, generated_root: Path | None = None) -> None:
    aoi_models = _import_generated_module(
        "marivo.contracts.generated.aoi", generated_root=generated_root
    )
    detect_model = _find_model_with_fields(
        aoi_models,
        {"metric", "time_scope", "granularity", "filter", "strategy"},
    )
    detect_model.model_rebuild(_types_namespace=vars(aoi_models))

    for example_path in AOI_CURRENT_EXAMPLE_FILES:
        if not example_path.exists():
            continue
        with example_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        detect_model.model_validate(payload)


def _validate_examples(*, generated_root: Path | None = None) -> None:
    _validate_osi_examples(generated_root=generated_root)
    _validate_aoi_examples(generated_root=generated_root)
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
        _validate_examples(generated_root=temp_output)


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
        _validate_examples(generated_root=OUTPUT_DIR)


if __name__ == "__main__":
    main()
