"""R10.1 retirement and archive-content checks; no installed execution qualification."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path
from zipfile import ZipFile

RETIRED_PATHS = frozenset(
    (
        "marivo/analysis/compiler/attribution.py",
        "marivo/analysis/compiler/comparison.py",
        "marivo/analysis/compiler/distinct.py",
        "marivo/analysis/compiler/distinct_fold.py",
        "marivo/analysis/compiler/distribution.py",
        "marivo/analysis/compiler/lowering.py",
        "marivo/analysis/compiler/nodes.py",
        "marivo/analysis/compiler/normalize.py",
        "marivo/analysis/compiler/ordering.py",
        "marivo/analysis/compiler/placement.py",
        "marivo/analysis/compiler/predicates.py",
        "marivo/analysis/compiler/private_parts.py",
        "marivo/analysis/compiler/source_admission.py",
        "marivo/analysis/compiler/source_dependencies.py",
        "marivo/analysis/compiler/temporal.py",
        "marivo/analysis/datasets/actions.py",
        "marivo/analysis/datasets/base.py",
        "marivo/analysis/datasets/contract.py",
        "marivo/analysis/datasets/fields.py",
        "marivo/analysis/datasets/registry.py",
        "marivo/analysis/domains/subject.py",
        "marivo/analysis/evidence/_dataset_reads.py",
        "marivo/analysis/materialization/basic_source.py",
        "marivo/analysis/materialization/dataset_presentation.py",
        "marivo/analysis/materialization/dataset_publication.py",
        "marivo/analysis/materialization/distribution.py",
        "marivo/analysis/materialization/execution_state.py",
        "marivo/analysis/materialization/finding_values.py",
        "marivo/analysis/materialization/ibis_batches.py",
        "marivo/analysis/materialization/input_bindings_codec.py",
        "marivo/analysis/materialization/local.py",
        "marivo/analysis/materialization/local_execution.py",
        "marivo/analysis/materialization/parquet_scan.py",
        "marivo/analysis/materialization/private_parquet.py",
        "marivo/analysis/materialization/publication.py",
        "marivo/analysis/materialization/recovery.py",
        "marivo/analysis/materialization/retained.py",
        "marivo/analysis/materialization/scalar_projection.py",
        "marivo/analysis/observation/aggregation.py",
        "marivo/analysis/observation/distinct_contracts.py",
        "marivo/analysis/observation/distribution_contracts.py",
        "marivo/analysis/observation/fold_contracts.py",
        "marivo/analysis/observation/metric.py",
        "marivo/analysis/observation/ordering.py",
        "marivo/analysis/observation/population.py",
        "marivo/analysis/observation/predicates.py",
        "marivo/analysis/observation/private_parts.py",
        "marivo/analysis/observation/rollup.py",
        "marivo/analysis/operators/attribute.py",
        "marivo/analysis/operators/attribute_expansion.py",
        "marivo/analysis/operators/attribute_values.py",
        "marivo/analysis/operators/attribution_contracts.py",
        "marivo/analysis/operators/clickhouse_support.py",
        "marivo/analysis/operators/compare.py",
        "marivo/analysis/operators/contracts.py",
        "marivo/analysis/operators/delta_state.py",
        "marivo/analysis/operators/distribution_values.py",
        "marivo/analysis/operators/mysql_support.py",
        "marivo/analysis/operators/postgres_support.py",
        "marivo/analysis/operators/registry.py",
        "marivo/analysis/operators/rollup.py",
        "marivo/analysis/operators/row.py",
        "marivo/analysis/operators/row_values.py",
        "marivo/analysis/operators/scalar_support.py",
        "marivo/analysis/operators/sqlite_support.py",
        "marivo/analysis/operators/trino_support.py",
    )
)

SKILLS = ("marivo/skills/marivo-analysis/SKILL.md", "marivo/skills/marivo-semantic/SKILL.md")
REQUIRED_PATHS = frozenset(
    {
        "marivo/analysis/core/graph.py",
        "marivo/analysis/methods/registry.py",
        "marivo/analysis/materialization/graph_store.py",
        "marivo/analysis/public_dsl.py",
        "marivo/analysis/compiler/source_time.py",
        *SKILLS,
    }
)


def source_contents(root: Path) -> dict[str, str]:
    """Hash current product code and the two unchanged packaged workflow resources."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "marivo").rglob("*")
        if path.is_file() and (path.suffix == ".py" or path.name == "SKILL.md")
    }


def validate_contents(contents: dict[str, str], expected: dict[str, str]) -> None:
    """Reject retired code, missing owners, extra resources and changed bytes."""
    retired = RETIRED_PATHS.intersection(contents)
    missing = REQUIRED_PATHS.difference(contents)
    if retired or missing:
        raise ValueError(f"retired={sorted(retired)!r}; missing={sorted(missing)!r}")
    if contents != expected:
        raise ValueError("archive differs from the current product code/resource hashes")


def check_archives(root: Path, wheel: Path, sdist: Path) -> dict[str, str | int]:
    """Check wheel and sdist against one exact source inventory without installing."""
    expected = source_contents(root)
    with ZipFile(wheel) as archive:
        wheel_contents = {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.startswith("marivo/")
        }
    with tarfile.open(sdist) as archive:
        sdist_contents = {}
        for member in archive.getmembers():
            parts = member.name.split("/", 1)
            if member.isfile() and len(parts) == 2 and parts[1].startswith("marivo/"):
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("sdist member has no readable bytes")
                with stream:
                    sdist_contents[parts[1]] = hashlib.sha256(stream.read()).hexdigest()
    validate_contents(wheel_contents, expected)
    validate_contents(sdist_contents, expected)
    return {
        "evidence_kind": "archive_contents_only",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "sdist_sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
        "product_files": len(expected),
        "retired_paths": len(RETIRED_PATHS),
    }
