"""Concrete recovery typing and intentional rejected retired entry shapes."""

from typing import TYPE_CHECKING

from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.public_dsl import PublicMaterialized

if TYPE_CHECKING:
    session = mv.session.get_or_create("retirement-typing")
    members = session.members(ms.ref.entity("sales.orders"))
    assert_type(members, mv.LogicalAnalysisDomain)
    recovered = session.artifact("artifact_current")
    assert_type(recovered, PublicMaterialized)
    # Expected-error assertions fail with unused-ignore if an old entry returns.
    session.population(ms.ref.entity("sales.orders"))  # type: ignore[attr-defined]
    session.observe(ms.ref.metric("sales.revenue"))  # type: ignore[attr-defined]
    legacy_logical = mv.LogicalMetricDataset  # type: ignore[attr-defined]
    legacy_materialized = mv.MaterializedDataset  # type: ignore[attr-defined]
    mv.gt("amount", 1)  # type: ignore[attr-defined]
