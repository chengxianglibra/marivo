"""Concrete typing for current member and artifact recovery entrypoints."""

from typing import TYPE_CHECKING

from typing_extensions import assert_type

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.public_dsl import PublicMaterialized

if TYPE_CHECKING:
    session = mv.session.get_or_create("recovery-typing")
    members = session.members(ms.ref.entity("sales.orders"))
    assert_type(members, mv.LogicalAnalysisDomain)
    recovered = session.artifact("artifact_current")
    assert_type(recovered, PublicMaterialized)
