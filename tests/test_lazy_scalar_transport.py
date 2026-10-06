"""Default, service-free regression tests for exact scalar transport boundaries."""

import ibis
import pytest

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.scalar_projection import _name, project


@pytest.mark.parametrize("nested", [False, True])
def test_identity_projection_guard_is_structured(nested: bool) -> None:
    table = ibis.table({"id": "int64"}, name="rows")
    identity = ibis.struct({"id": table.id})
    expression = (
        table.select(identity=ibis.struct({"nested": identity}))
        if nested
        else table.select(identity=identity, **{_name("identity", "id"): table.id})
    )
    with pytest.raises(MaterializationError) as caught:
        project(expression, run_ref="run:compile")
    assert caught.value.stage == "compilation"
    assert caught.value.run_ref == "run:compile"
