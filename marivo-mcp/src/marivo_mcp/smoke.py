from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from marivo_mcp.config import (
    MarivoMcpConfig,
    MarivoMcpConfigError,
    TargetResolutionError,
    load_config_from_env,
)
from marivo_mcp.http_client import MarivoHttpClient
from marivo_mcp.target_resolution import resolve_target


@dataclass(frozen=True)
class SmokeCheckResult:
    name: str
    ok: bool
    status_code: int
    category: str | None
    marivo_path: str
    message: str


@dataclass(frozen=True)
class SmokeRunResult:
    target_kind: str
    base_url: str
    workspace_root: str | None
    manifest_path: str | None
    runtime_state: str
    checks: list[SmokeCheckResult]


def run_live_smoke(config: MarivoMcpConfig) -> SmokeRunResult:
    """Resolve the target and run a minimal live smoke path against Marivo HTTP."""
    resolution = resolve_target(config)
    client = MarivoHttpClient(resolution.config)
    try:
        health = client.request_envelope("GET", "/health")
        openapi = client.request_envelope("GET", "/openapi/index")
        session = client.request_envelope(
            "POST",
            "/sessions",
            json_body={"goal": "marivo-mcp smoke test"},
        )

        results = [
            _result_from_envelope("health_check", health),
            _result_from_envelope("list_openapi_paths", openapi),
            _result_from_envelope("create_session", session),
        ]

        session_id = _extract_session_id(session)
        if session_id is not None:
            terminated = client.request_envelope(
                "POST",
                f"/sessions/{session_id}/terminate",
                json_body={"terminal_reason": "user_closed"},
            )
            results.append(_result_from_envelope("terminate_session", terminated))
            session_state = client.request_envelope("GET", f"/sessions/{session_id}/state")
            results.append(_result_from_envelope("get_session_state", session_state))
        else:
            results.append(
                SmokeCheckResult(
                    name="terminate_session",
                    ok=False,
                    status_code=session.status_code,
                    category="server_error",
                    marivo_path="/sessions/{session_id}/terminate",
                    message="Skipped because create_session did not return a session_id.",
                )
            )
            results.append(
                SmokeCheckResult(
                    name="get_session_state",
                    ok=False,
                    status_code=session.status_code,
                    category="server_error",
                    marivo_path="/sessions/{session_id}/state",
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
                marivo_path="/semantic/entities",
                message="Expected a validation failure envelope from POST /semantic/entities.",
            )
        elif validation.error.category != "validation":
            validation_result = SmokeCheckResult(
                name="validation_envelope",
                ok=False,
                status_code=validation.status_code,
                category=validation.error.category,
                marivo_path="/semantic/entities",
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
                marivo_path="/semantic/entities",
                message="validation envelope ok",
            )
        results.append(validation_result)

        return SmokeRunResult(
            target_kind=resolution.target_kind,
            base_url=resolution.base_url,
            workspace_root=resolution.workspace_root,
            manifest_path=None
            if resolution.manifest_path is None
            else str(resolution.manifest_path),
            runtime_state=resolution.runtime_state,
            checks=results,
        )
    finally:
        client.close()


def summarize_results(result: SmokeRunResult | list[SmokeCheckResult]) -> dict[str, Any]:
    if isinstance(result, SmokeRunResult):
        checks = result.checks
        target_summary: dict[str, Any] = {
            "target_kind": result.target_kind,
            "base_url": result.base_url,
            "workspace_root": result.workspace_root,
            "manifest_path": result.manifest_path,
            "runtime_state": result.runtime_state,
        }
    else:
        checks = result
        target_summary = {}

    failed = [check for check in checks if not check.ok]
    return {
        "ok": len(failed) == 0,
        **target_summary,
        "checks": [check.__dict__ for check in checks],
        "failed_checks": [check.name for check in failed],
    }


def main() -> None:
    try:
        config = load_config_from_env()
        summary = summarize_results(run_live_smoke(config))
    except MarivoMcpConfigError as error:
        if isinstance(error, TargetResolutionError):
            payload = {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "detail": error.detail,
                    "guidance": error.guidance,
                },
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            raise SystemExit(1) from error
        raise SystemExit(str(error)) from error
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
        marivo_path=str(meta.marivo_path),
        message=str(message),
    )


def _extract_session_id(envelope: Any) -> str | None:
    data = envelope.data
    if not isinstance(data, dict):
        return None
    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None
