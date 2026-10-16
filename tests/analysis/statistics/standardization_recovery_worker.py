"""Recover actual float/Decimal standardization producers without source state."""

import json

from marivo.analysis.public_dsl import _MaterializedRead
from tests.support.json import Json, checked


def parts(value: _MaterializedRead) -> dict[str, Json]:
    assert value._dataset is not None
    verified = value._dataset.verified()
    schemas = {part.role: part.schema for part in verified.contract.parts}
    return {
        part.role: checked(
            {
                "schema": str(schemas[part.role]),
                "rows": json.loads(json.dumps(part.table.to_dict(orient="records"), default=str)),
            }
        )
        for part in verified.parts
    }
