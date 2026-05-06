from __future__ import annotations

from collections.abc import Callable, Iterable
from json import JSONDecodeError
from typing import Any, Literal

import httpx

from marivo_mcp.config import MarivoMcpConfig, TargetResolutionError
from marivo_mcp.models import ToolEnvelope, ToolError, ToolMeta
from marivo_mcp.target_resolution import resolve_target

_RETRYABLE_STATUS_CODES = {502, 503, 504}
_TIMEOUT_STATUS_CODE = 504
_TRANSPORT_STATUS_CODE = 503


class MarivoHttpClientError(RuntimeError):
    """Raised when a canonical HTTP read fails outside the tool envelope path."""

    def __init__(
        self,
        *,
        status_code: int,
        category: str,
        message: str,
        path: str,
        detail: object | None = None,
        guidance: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.path = path
        self.detail = detail
        self.guidance = guidance


class MarivoHttpClient:
    """Shared transport wrapper for all MCP tools."""

    def __init__(
        self,
        config: MarivoMcpConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if config.base_url is None:
            raise TargetResolutionError(
                code="remote_target_required",
                message="远程模式需要提供 Marivo 服务地址",
                detail={},
                guidance="请设置 MARIVO_BASE_URL",
            )
        headers = {"Accept": "application/json"}
        if config.api_token:
            headers["Authorization"] = f"Bearer {config.api_token}"
        if config.user:
            headers["X-Marivo-User"] = config.user
        self._client = httpx.Client(
            base_url=config.base_url,
            headers=headers,
            timeout=config.timeout_ms / 1000,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request_envelope(
        self,
        method: str,
        path: str,
        *,
        params: (
            dict[str, str | int | float | bool | None | list[str | int | float | bool | None]]
            | None
        ) = None,
        json_body: object | None = None,
    ) -> ToolEnvelope:
        normalized_method = method.upper()
        attempt_count = 0
        while True:
            attempt_count += 1
            request = self._client.build_request(
                normalized_method,
                path,
                params=params,
                json=json_body,
            )
            try:
                response = self._client.send(request)
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                if self._should_retry(
                    normalized_method,
                    status_code=None,
                    attempt_count=attempt_count,
                ):
                    continue
                return self._transport_error_envelope(
                    path=path,
                    method=normalized_method,
                    request_url=str(request.url),
                    attempt_count=attempt_count,
                    error=error,
                )

            if self._should_retry(
                normalized_method,
                status_code=response.status_code,
                attempt_count=attempt_count,
            ):
                continue
            return self._response_to_envelope(
                response=response,
                path=path,
                method=normalized_method,
                attempt_count=attempt_count,
            )

    def request_canonical(
        self,
        method: str,
        path: str,
        *,
        params: (
            dict[str, str | int | float | bool | None | list[str | int | float | bool | None]]
            | None
        ) = None,
        json_body: object | None = None,
    ) -> object | None:
        envelope = self.request_envelope(method, path, params=params, json_body=json_body)
        if envelope.ok:
            return envelope.data
        error = envelope.error
        raise MarivoHttpClientError(
            status_code=envelope.status_code,
            category=error.category if error is not None else "server_error",
            message=error.message if error is not None else "Marivo request failed.",
            path=path,
            detail=error.detail if error is not None else None,
            guidance=error.guidance if error is not None else None,
        )

    def _should_retry(
        self,
        method: str,
        *,
        status_code: int | None,
        attempt_count: int,
    ) -> bool:
        return (
            method == "GET"
            and attempt_count < 2
            and (status_code is None or status_code in _RETRYABLE_STATUS_CODES)
        )

    def _response_to_envelope(
        self,
        *,
        response: httpx.Response,
        path: str,
        method: str,
        attempt_count: int,
    ) -> ToolEnvelope:
        meta = ToolMeta(
            marivo_path=path,
            method=method,
            request_url=str(response.request.url),
            attempt_count=attempt_count,
            content_type=response.headers.get("content-type"),
        )
        parsed_body, raw_body = self._parse_response_body(response)
        if response.is_success:
            return ToolEnvelope(
                ok=True,
                status_code=response.status_code,
                data=parsed_body,
                error=None,
                meta=meta,
            )
        return ToolEnvelope(
            ok=False,
            status_code=response.status_code,
            data=None,
            error=self._build_http_error(
                status_code=response.status_code,
                parsed_body=parsed_body,
                raw_body=raw_body,
            ),
            meta=meta,
        )

    def _transport_error_envelope(
        self,
        *,
        path: str,
        method: str,
        request_url: str,
        attempt_count: int,
        error: httpx.TimeoutException | httpx.ConnectError,
    ) -> ToolEnvelope:
        status_code = (
            _TIMEOUT_STATUS_CODE
            if isinstance(error, httpx.TimeoutException)
            else _TRANSPORT_STATUS_CODE
        )
        return ToolEnvelope(
            ok=False,
            status_code=status_code,
            data=None,
            error=ToolError(
                category="transport",
                message=str(error) or error.__class__.__name__,
                raw_body=None,
            ),
            meta=ToolMeta(
                marivo_path=path,
                method=method,
                request_url=request_url,
                attempt_count=attempt_count,
                content_type=None,
            ),
        )

    def _build_http_error(
        self,
        *,
        status_code: int,
        parsed_body: object | None,
        raw_body: str | None,
    ) -> ToolError:
        payload = parsed_body if isinstance(parsed_body, dict) else {}
        detail = payload.get("detail")
        guidance = payload.get("guidance") if isinstance(payload.get("guidance"), dict) else None
        error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else None
        message = self._extract_message(
            detail=detail, error_payload=error_payload, raw_body=raw_body
        )
        detail_payload = detail if isinstance(detail, dict) else None
        code = (
            error_payload.get("code")
            if error_payload
            else detail_payload.get("code")
            if detail_payload
            else payload.get("code")
        )
        remediation_hint = (
            self._build_validation_remediation_hint(detail=detail, guidance=guidance)
            if status_code == 422
            else None
        )
        return ToolError(
            category=self._classify_status(status_code),
            message=message,
            code=code if isinstance(code, str) else None,
            detail=detail,
            guidance=guidance,
            remediation_hint=remediation_hint,
            raw_body=raw_body if detail is None else None,
        )

    def _parse_response_body(self, response: httpx.Response) -> tuple[object | None, str | None]:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                return response.json(), None
            except JSONDecodeError:
                text = response.text
                return {"raw_text": text}, text
        text = response.text
        if not text:
            return None, None
        return {"raw_text": text}, text

    def _extract_message(
        self,
        *,
        detail: object,
        error_payload: dict[str, Any] | None,
        raw_body: str | None,
    ) -> str:
        if error_payload is not None:
            message = error_payload.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(detail, dict):
            message = detail.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(detail, str) and detail:
            return detail
        if isinstance(detail, list) and detail:
            first_issue = detail[0]
            if isinstance(first_issue, dict):
                issue_msg = first_issue.get("msg")
                if isinstance(issue_msg, str) and issue_msg:
                    return issue_msg
            return "Request validation failed."
        if raw_body:
            return raw_body
        return "Marivo request failed."

    def _classify_status(
        self,
        status_code: int,
    ) -> Literal["validation", "not_found", "conflict", "server_error"]:
        if status_code == 422:
            return "validation"
        if status_code == 404:
            return "not_found"
        if status_code == 409:
            return "conflict"
        return "server_error"

    def _build_validation_remediation_hint(
        self,
        *,
        detail: object,
        guidance: dict[str, object] | None,
    ) -> str:
        if guidance:
            examples = guidance.get("examples")
            if isinstance(examples, list) and examples:
                return "Validation failed. Start with guidance.examples for the shortest valid payload."
            schema_url = guidance.get("schema_url")
            if isinstance(schema_url, str) and schema_url:
                return "Validation failed. Read guidance.schema_url for the exact request model."
            contract_url = guidance.get("contract_url")
            if isinstance(contract_url, str) and contract_url:
                return (
                    "Validation failed. Read guidance.contract_url for the route-scoped contract."
                )
        if isinstance(detail, list) and detail:
            return "Validation failed. Use detail[*].loc to repair the failing field path."
        return "Validation failed. Inspect the canonical Marivo error body for remediation."


class ResolvingMarivoHttpClient(MarivoHttpClient):
    """HTTP client facade that resolves local/auto targets on first use."""

    def __init__(
        self,
        config: MarivoMcpConfig,
        *,
        workspace_roots_provider: Callable[[], Iterable[str]] | None = None,
        client_factory: Callable[[MarivoMcpConfig], MarivoHttpClient] = MarivoHttpClient,
    ) -> None:
        self._base_config = config
        self._workspace_roots_provider = workspace_roots_provider or (lambda: ())
        self._client_factory = client_factory
        self._resolved_http_client: MarivoHttpClient | None = None

    def close(self) -> None:
        if self._resolved_http_client is not None:
            self._resolved_http_client.close()

    def request_envelope(
        self,
        method: str,
        path: str,
        *,
        params: (
            dict[str, str | int | float | bool | None | list[str | int | float | bool | None]]
            | None
        ) = None,
        json_body: object | None = None,
    ) -> ToolEnvelope:
        return self._resolved_client().request_envelope(
            method,
            path,
            params=params,
            json_body=json_body,
        )

    def request_canonical(
        self,
        method: str,
        path: str,
        *,
        params: (
            dict[str, str | int | float | bool | None | list[str | int | float | bool | None]]
            | None
        ) = None,
        json_body: object | None = None,
    ) -> object | None:
        return self._resolved_client().request_canonical(
            method,
            path,
            params=params,
            json_body=json_body,
        )

    def _resolved_client(self) -> MarivoHttpClient:
        if self._resolved_http_client is None:
            resolution = resolve_target(
                self._base_config,
                workspace_roots=self._workspace_roots_provider(),
            )
            self._resolved_http_client = self._client_factory(resolution.config)
        return self._resolved_http_client
