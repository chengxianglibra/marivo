"""Static contract checks for ontology authoring and read-side SPI values."""

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from ibis.common.deferred import Resolver
from ibis.expr.types import Value
from typing_extensions import assert_type

import marivo.ontology as mo
import marivo.semantic as ms
from marivo.analysis.model.result_reference import FrameReferenceWire
from marivo.analysis.session import read_spi
from marivo.analysis.session.read_spi import FrameSnapshot, StoredSessions
from marivo.datasource import spi as source_spi
from marivo.datasource.engines.query import EngineQueryPolicy, QueryDialectProfile
from marivo.semantic import spi as semantic_spi


def ontology_authoring(semantic: ms.SemanticCatalog) -> None:
    driver = ms.ref.measure("sales.orders.refund_count")
    outcome = ms.ref.metric("sales.retention")
    context = ms.ai_context(business_definition="Refund counts may influence retention.")
    edge = mo.influences(name="refund_pressure", driver=driver, outcome=outcome, ai_context=context)
    assert_type(edge, mo.SemanticEdgeRef)
    assert_type(edge.kind, Literal["semantic_edge"])
    assert_type(edge.to_dict(), dict[str, str])
    assert_type(mo.load(semantic=semantic), mo.OntologyCatalog)


def catalog_projection(semantic: ms.SemanticCatalog, provider: str) -> None:
    assert_type(semantic_spi.catalog_state(semantic), semantic_spi.CompiledSemanticState)
    assert_type(semantic_spi.iter_catalog_entries(semantic), Iterable[ms.SemanticObject])
    assert_type(source_spi.query_dialect(provider), QueryDialectProfile)
    assert_type(source_spi.query_policy(provider), EngineQueryPolicy)


def captured_source_and_expression(
    session: source_spi.SourceSession, physical: Value, expression: semantic_spi.NumericMetric
) -> None:
    assert_type(source_spi.bound_duration_units(session, physical), tuple[str, ...])
    assert_type(semantic_spi.expression_recipe(expression), Resolver)


def stored_frames(root: Path, session_id: str, artifact_id: str) -> None:
    sessions = read_spi.find_sessions(root)
    assert_type(sessions, StoredSessions | None)
    if sessions is None or (session := sessions.session(session_id)) is None:
        return
    frame = session.frame(artifact_id)
    assert_type(frame, FrameSnapshot)
    assert_type(frame.ref, FrameReferenceWire)
