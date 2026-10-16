"""Exact independent contribution components survive fixed/cold reduction."""

import json

from marivo.analysis.public_dsl import _MaterializedRead
from tests.support.json import Json, checked, digest, encode, obj


def components(value: _MaterializedRead) -> dict[str, Json]:
    assert value._dataset is not None
    part = next(part for part in value._dataset.verified().parts if part.role == "original_state")
    contents = obj(
        checked(
            json.loads(
                json.dumps(
                    {"schema": str(part.table.schema), "rows": part.table.to_pylist()},
                    sort_keys=True,
                    default=str,
                )
            )
        )
    )
    return {"sha256": digest(encode(contents)), "contents": contents}
