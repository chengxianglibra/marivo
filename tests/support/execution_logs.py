"""Read persisted execution diagnostics for independent behavior assertions."""

import json
from pathlib import Path


def execution_records(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted((root / ".marivo" / "logs").glob("execution-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            value: object = json.loads(line)
            assert isinstance(value, dict)
            assert all(isinstance(key, str) for key in value)
            records.append({str(key): item for key, item in value.items()})
    return records
