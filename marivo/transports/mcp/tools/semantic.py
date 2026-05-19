"""Registration functions for MCP semantic document tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marivo.identity import require_user, resolve_user
from marivo.transports.mcp.tools._async_bridge import call_runtime
from marivo.transports.mcp.tools.schemas import McpOsiDocumentInput


def _load_document_input(input_data: McpOsiDocumentInput) -> dict[str, Any]:
    if input_data.document is not None:
        return input_data.document

    assert input_data.input_path is not None
    with Path(input_data.input_path).expanduser().open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("input_path must contain a JSON object.")
    return data


def _write_export_output(output_path: str | None, result: dict[str, Any]) -> dict[str, Any]:
    if output_path is None or result.get("error") is not None:
        return result

    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(result["data"], f, ensure_ascii=False, indent=2)
        f.write("\n")
    return {
        "data": {
            "output_path": str(path),
            "document": result["data"],
        },
        "error": None,
    }


def register_semantic_tools(server: Any, runtime: Any) -> None:
    svc = runtime.get_service("semantic_v2")

    @server.tool()  # type: ignore
    async def list_semantic_models() -> dict[str, Any]:
        """List semantic models via GET /semantic-models."""
        return await call_runtime(svc.list_semantic_models, requesting_user=resolve_user())

    @server.tool()  # type: ignore
    async def get_semantic_model(model: str) -> dict[str, Any]:
        """Get a semantic model as an OSI document via GET /semantic-models/{model}."""
        return await call_runtime(
            svc.get_semantic_model,
            name=model,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def validate_osi_semantic_models(input: McpOsiDocumentInput) -> dict[str, Any]:
        """Validate an inline or local-file OSI-Marivo semantic document.

        dataset.source must be a relation FQN (schema.table or catalog.schema.table); SQL queries are not accepted.

        Key fields in the MARIVO metric extension: additive_dimensions and aggregation_semantics.
        See the import tool description for details.

        Key fields in the MARIVO field extension (required for all time fields):
        - support_min_granularity: Finest time granularity (hour, day, week, month, quarter, year).
        - data_type: Physical SQL data type of the time field column. One of: date, timestamp, string, integer.
        - format: Temporal format pattern for string/integer time fields. Required when data_type is 'string' or 'integer'.
          Examples: 'yyyymmdd' for YYYYMMDD string partitions, 'yyyy-mm-dd' for ISO date strings,
          'yyyymmddhh' for combined date+hour strings, 'hh' for standalone hour columns,
          'epoch_seconds' for Unix epoch integers.
        - required_prefix: Field name of the date-format time field that provides date context for hour-only fields.
          Required when format is 'hh' or 'h'. Must reference a time field on the same dataset.
        """
        return await call_runtime(
            svc.validate_osi_semantic_models,
            doc_data=_load_document_input(input),
        )

    @server.tool()  # type: ignore
    async def import_osi_semantic_models(input: McpOsiDocumentInput) -> dict[str, Any]:
        """Import an inline or local-file OSI-Marivo semantic document.

        dataset.source must be a relation FQN (schema.table or catalog.schema.table); SQL queries are not accepted.

        Key field in the MARIVO metric extension: additive_dimensions.
        Use [] for non-additive metrics, explicit field names for subset-additive metrics,
        or ["__all"] when the metric is additive across all declared dimension fields in
        the semantic model, including time dimensions. "__all" must be the only item
        when used.

        Key field in the MARIVO metric extension: aggregation_semantics
        (enum: sum | ratio | weighted_average, default: sum).
        Decision rule:
        - 'sum' for additive quantities — values sum across groups (revenue, latency).
        - 'ratio' for proportions / binary-outcome rates (conversion rate, CTR).
        - 'weighted_average' for ratio-of-sums metrics (AOV = SUM/COUNT).

        Key fields in the MARIVO field extension (required for all time fields):
        - data_type: Physical SQL data type of the time field column. One of: date, timestamp, string, integer.
        - format: Required when data_type is 'string' or 'integer'. Temporal format pattern.
          Examples: 'yyyymmdd' for YYYYMMDD string partitions, 'yyyy-mm-dd' for ISO date strings,
          'yyyymmddhh' for combined date+hour strings, 'hh' for standalone hour columns,
          'epoch_seconds' for Unix epoch integers.
        - required_prefix: Field name of the date-format time field that provides date context for hour-only fields.
          Required when format is 'hh' or 'h'. Must reference a time field on the same dataset.
        """
        result = await call_runtime(
            svc.import_osi_semantic_models,
            doc_data=_load_document_input(input),
        )
        if result.get("error") is not None:
            return result
        return {
            "data": {
                "status": "success",
                "message": "OSI semantic models imported successfully.",
            },
            "error": None,
        }

    @server.tool()  # type: ignore
    async def export_osi_semantic_models(
        semantic_model_name: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """Export an OSI-Marivo semantic document, optionally writing it to a local file."""
        result = await call_runtime(
            svc.export_osi_semantic_models,
            semantic_model_name=semantic_model_name,
        )
        return _write_export_output(output_path, result)

    @server.tool()  # type: ignore
    async def delete_semantic_model(model: str) -> dict[str, Any]:
        """Delete the current user's private semantic model via DELETE /semantic-models/{model}."""
        return await call_runtime(
            svc.delete_semantic_model,
            name=model,
            owner_user=require_user(),
        )
