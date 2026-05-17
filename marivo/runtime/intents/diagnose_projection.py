"""Response projection helpers for diagnose envelopes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def compact_diagnose_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return a lightweight diagnose envelope without embedded detail payloads.

    The projection is response-only: committed artifacts and references remain
    unchanged, but bulky nested AOI artifacts and driver rows are omitted from
    the returned envelope.
    """
    compact = deepcopy(envelope)

    result = compact.get("result")
    if isinstance(result, dict):
        result["aoi_artifacts"] = []
        _strip_driver_rows(result.get("diagnoses"))

    product_metadata = compact.get("product_metadata")
    if isinstance(product_metadata, dict):
        product_metadata["aoi_artifacts"] = []

    return compact


def _strip_driver_rows(diagnoses: Any) -> None:
    if not isinstance(diagnoses, list):
        return
    for diagnosis in diagnoses:
        if not isinstance(diagnosis, dict):
            continue
        drivers = diagnosis.get("drivers")
        if not isinstance(drivers, list):
            continue
        for driver in drivers:
            if isinstance(driver, dict):
                driver.pop("rows", None)
