from __future__ import annotations

import json
from typing import Any


def render_catalog_markdown(data: dict[str, Any]) -> str:
    entities = "\n".join(
        f"- `{e['id']}` (keys: {', '.join(e.get('keys', []))})" for e in data.get("entities", [])
    )
    metrics = "\n".join(f"- `{m['id']}`: {m['definition']}" for m in data.get("metrics", []))
    asset_lines: list[str] = []
    for a in data.get("assets", []):
        row_count = a.get("row_count")
        fqn = a.get("fqn", a["id"])
        suffix = f", rows={row_count}" if row_count is not None else ""
        asset_lines.append(f"- `{fqn}` ({a['kind']}{suffix})")
    assets = "\n".join(asset_lines)
    return (
        "# OmniDB catalog\n\n"
        f"- Entities: {len(data.get('entities', []))}\n"
        f"- Metrics: {len(data.get('metrics', []))}\n"
        f"- Assets: {len(data.get('assets', []))}\n\n"
        "## Entities\n"
        f"{entities or '- None'}\n\n"
        "## Metrics\n"
        f"{metrics or '- None'}\n\n"
        "## Assets\n"
        f"{assets or '- None'}"
    )


def render_step_markdown(data: dict[str, Any]) -> str:
    observations = data.get("observations", [])
    observation_lines = []
    for observation in observations[:5]:
        slice_info = observation.get("subject", {}).get("slice", {})
        payload = observation.get("payload", {})
        observation_lines.append(
            "- "
            f"{observation.get('type')} for "
            f"{slice_info.get('platform')} {slice_info.get('app_version')} "
            f"{slice_info.get('network_type')} {slice_info.get('content_type')}: "
            f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        )
    return (
        f"# Step result: {data.get('step_type', 'unknown')}\n\n"
        f"{data.get('summary', 'No summary available.')}\n\n"
        "## Key observations\n"
        f"{chr(10).join(observation_lines) if observation_lines else '- No observations returned'}"
    )


def render_evidence_markdown(data: dict[str, Any]) -> str:
    claims = "\n".join(f"- {claim['text']} (confidence={claim['confidence']})" for claim in data.get("claims", []))
    edges = "\n".join(
        f"- {edge['from_node_type']}:{edge['from_node_id']} -> {edge['edge_type']} -> {edge['to_node_type']}:{edge['to_node_id']}"
        for edge in data.get("edges", [])[:10]
    )
    return (
        "# Evidence graph\n\n"
        f"- Observations: {len(data.get('observations', []))}\n"
        f"- Claims: {len(data.get('claims', []))}\n"
        f"- Recommendations: {len(data.get('recommendations', []))}\n\n"
        "## Claims\n"
        f"{claims or '- None'}\n\n"
        "## Sample edges\n"
        f"{edges or '- None'}"
    )
