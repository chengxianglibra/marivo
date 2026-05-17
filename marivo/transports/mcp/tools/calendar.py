"""Registration functions for MCP calendar data tools."""

from __future__ import annotations

from typing import Any

from marivo.contracts.calendar import (
    CalendarDataListEnvelope,
    CalendarDataQuery,
    CalendarDataUpdateEnvelope,
    CalendarDataUpdateRequest,
)
from marivo.transports.mcp.tools._async_bridge import call_runtime


def register_calendar_tools(server: Any, runtime: Any) -> None:
    @server.tool(structured_output=True)  # type: ignore
    async def list_calendar_data(input: CalendarDataQuery) -> CalendarDataListEnvelope:
        """List sparse holiday/adjusted-workday calendar rows for calendar-aware analysis."""
        svc = runtime.get_service("calendar_data")
        result = await call_runtime(svc.list_calendar_data, input=input)
        return CalendarDataListEnvelope.model_validate(result)

    @server.tool(structured_output=True)  # type: ignore
    async def update_calendar_data(input: CalendarDataUpdateRequest) -> CalendarDataUpdateEnvelope:
        """Incrementally upsert sparse calendar rows without deleting existing rows."""
        svc = runtime.get_service("calendar_data")
        result = await call_runtime(svc.update_calendar_data, input=input)
        return CalendarDataUpdateEnvelope.model_validate(result)
