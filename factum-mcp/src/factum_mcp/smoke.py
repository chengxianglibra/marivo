from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from factum_mcp.config import FactumMcpConfig, FactumMcpConfigError, load_config_from_env
from factum_mcp.http_client import FactumHttpClient


@dataclass(frozen=True)
class SmokeCheckResult:
    name: str
    ok: bool
    status_code: int
    category: str | None
    factum_path: str
    message: str


def run_live_smoke(config: FactumMcpConfig) -> list[SmokeCheckResult]:
    """Run a minimal live smoke path against a real Factum HTTP service."""
    client = FactumHttpClient(config)
    try:
        health = client.request_envelope("GET", "/health")
        openapi = client.request_envelope("GET", "/openapi/index")
        session = client.request_envelope(
            "POST",
            "/sessions",
            json_body={"goal": "factum-mcp smoke test"},
        )

        results = [
            _result_from_envelope("health_check", health),
            _result_from_envelope("list_openapi_paths", openapi),
            _result_from_envelope("create_session", session),
        ]

        session_id = _extract_session_id(session)
        if session_id is not None:
            session_state = client.request_envelope("GET", f"/sessions/{session_id}/state")
            results.append(_result_from_envelope("get_session_state", session_state))
        else:
            results.append(
                SmokeCheckResult(
                    name="get_session_state",
                    ok=False,
                    status_code=session.status_code,
                    category="server_error",
                    factum_path="/sessions/{session_id}/state",
                    message="Skipped because create_session did not return a session_id.",
                )
            )

        validation = client.request_envelope("POST", "/semantic/entities", json_body={})
        if validation.ok or validation.error is None:
            validation_result = SmokeCheckResult(
                name="validation_envelope",
                ok=False,
                status_code=validation.status_code,
                category="server_error",
                factum_path="/semantic/entities",
                message="Expected a validation failure envelope from POST /semantic/entities.",
            )
        elif validation.error.category != "validation":
            validation_result = SmokeCheckResult(
                name="validation_envelope",
                ok=False,
                status_code=validation.status_code,
                category=validation.error.category,
                factum_path="/semantic/entities",
                message=(
                    "Expected error.category=validation for POST /semantic/entities, "
                    f"got {validation.error.category!r}."
                ),
            )
        else:
            validation_result = SmokeCheckResult(
                name="validation_envelope",
                ok=True,
                status_code=validation.status_code,
                category=validation.error.category,
                factum_path="/semantic/entities",
                message="validation envelope ok",
            )
        results.append(validation_result)

        return results
    finally:
        client.close()


def summarize_results(results: list[SmokeCheckResult]) -> dict[str, Any]:
    failed = [result for result in results if not result.ok]
    return {
        "ok": len(failed) == 0,
        "checks": [result.__dict__ for result in results],
        "failed_checks": [result.name for result in failed],
    }


def main() -> None:
    try:
        config = load_config_from_env()
    except FactumMcpConfigError as error:
        raise SystemExit(str(error)) from error

    summary = summarize_results(run_live_smoke(config))
    print(json.dumps(summary, indent=2))
    if not summary["ok"]:
        raise SystemExit(1)


def _result_from_envelope(name: str, envelope: Any) -> SmokeCheckResult:
    error = envelope.error
    meta = envelope.meta
    message = "ok" if envelope.ok else error.message if error is not None else "request failed"
    return SmokeCheckResult(
        name=name,
        ok=bool(envelope.ok),
        status_code=int(envelope.status_code),
        category=None if error is None else error.category,
        factum_path=str(meta.factum_path),
        message=str(message),
    )


def _extract_session_id(envelope: Any) -> str | None:
    data = envelope.data
    if not isinstance(data, dict):
        return None
    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None
