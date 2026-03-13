from __future__ import annotations

import os
from typing import Any

from app.mcp_client import OmniDBApiClient
from app.mcp.models import ResponseFormat


def get_client() -> OmniDBApiClient:
    return OmniDBApiClient(
        base_url=os.getenv("OMNIDB_API_BASE_URL", "http://127.0.0.1:8000"),
        timeout=float(os.getenv("OMNIDB_API_TIMEOUT", "30")),
    )


def format_tool_response(
    response_format: ResponseFormat,
    summary: str,
    data: dict[str, Any],
    markdown: str,
) -> dict[str, Any]:
    if response_format == ResponseFormat.MARKDOWN:
        return {"summary": summary, "markdown": markdown, "data": data}
    return {"summary": summary, "data": data}
