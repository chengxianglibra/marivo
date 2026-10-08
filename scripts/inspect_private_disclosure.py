"""Capture reproducible private native inputs and full executable-tree fingerprints.

Run with the repository interpreter from its root. This command neither installs
Help nor activates a Session; only its explicit output directory is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    FamilyInput,
    NavigationInput,
    TypeInput,
)
from marivo.analysis._capabilities.dataset_registry import prepare
from marivo.analysis._capabilities.dataset_render import render


def capture(output: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    registry = prepare()
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for descriptor in registry.descriptors:
        row: dict[str, object] = {
            "target": descriptor.canonical_id,
            "kind": descriptor.kind,
            "summary": descriptor.summary,
            "help": render(registry, descriptor.canonical_id),
        }
        if isinstance(descriptor, CallableInput):
            row.update(
                entrypoint=descriptor.public_entrypoint,
                bindings=[
                    {"path": b.path, "signature": str(b.signature)} for b in descriptor.bindings
                ],
                registration_ids=descriptor.registration_ids,
                discovery_group=descriptor.discovery_group,
                unbound_default=descriptor.unbound_default,
                parameters=[
                    {"name": p.name, "acquisition": p.acquisition, "targets": p.targets}
                    for p in descriptor.parameters
                ],
                output=descriptor.output,
                effects=descriptor.effects,
                failures=descriptor.failures,
                example={
                    "code": descriptor.example.code,
                    "requires": descriptor.example.requires,
                    "outcome": descriptor.example.outcome,
                    "runtime": descriptor.example.runtime,
                },
            )
        elif isinstance(descriptor, (TypeInput, FamilyInput)):
            row.update(
                types=[
                    {
                        "name": b.implementation.__name__,
                        "fields": [{"name": f.name, "annotation": f.annotation} for f in b.fields],
                        "methods": b.methods,
                    }
                    for b in descriptor.bindings
                ]
            )
            row["variants"] = [
                {
                    "implementation": v.implementation.__module__ + "." + v.implementation.__name__,
                    "fields": [{"name": f.name, "annotation": f.annotation} for f in v.fields],
                }
                for v in descriptor.variants
            ]
            if isinstance(descriptor, FamilyInput):
                row["shapes"] = [str(s) for s in descriptor.registration.shape_ids]
        elif isinstance(descriptor, NavigationInput):
            row["members"] = descriptor.members
        rows.append(row)
    (output / "native-inputs.json").write_text(
        json.dumps(
            {
                "exports": {e.name: e.target for p in registry.providers for e in p.exports},
                "descriptors": rows,
                "retained_catalog_inputs": [
                    {"target": d.canonical_id, "callable_path": d.callable_path}
                    for d in registry.retained_catalog_inputs
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    scopes = (
        "marivo",
        "tests",
        "scripts",
        "pyproject.toml",
        "Makefile",
        "pytest.ini",
        ".importlinter",
    )
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", *scopes], cwd=root)
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *scopes], cwd=root
    )
    names = sorted({name for name in (tracked + untracked).decode().split("\0") if name})
    fingerprints = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in names
        if (root / name).is_file()
    }
    encoded = json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()
    record = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "file_count": len(fingerprints),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "files": fingerprints,
        "input_sha256": hashlib.sha256((output / "native-inputs.json").read_bytes()).hexdigest(),
    }
    (output / "executable-manifest.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "file_count": len(fingerprints),
                "sha256": record["sha256"],
                "native_targets": len(rows),
                "exports": 100,
                "retained_catalog_inputs": len(registry.retained_catalog_inputs),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    capture(parser.parse_args().output)
