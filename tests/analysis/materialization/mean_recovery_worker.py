"""Source-free current-row mean and original weighted mean recovery."""

from tests.support.json import Json, arr, obj


def snapshot_rows(saved: dict[str, Json]) -> list[Json]:
    return arr(obj(saved["rows"])["data"])
