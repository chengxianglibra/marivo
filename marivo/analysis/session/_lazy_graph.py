"""Indexed, bounded Session-scoped topology from one v3 Store snapshot."""

from __future__ import annotations

import heapq
import sqlite3
from collections import deque
from typing import Literal, TypeAlias

from marivo.analysis.errors import (
    ArtifactNotFoundError,
    SessionGraphArgumentError,
    SessionGraphIntegrityError,
    SessionGraphLimitError,
    SessionGraphTooLargeError,
)
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.materialization.store import SessionStore, _one, _rows, _text
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_read_model import (
    ArtifactSummary,
    FailedRun,
    GraphDirection,
    IncompleteRun,
    RunRecord,
    SessionGraph,
    SessionGraphEdge,
    SucceededRun,
)
from marivo.analysis.session._lazy_runtime_reads import (
    _HEAD_SQL,
    OVERALL_GRAPH_SCAN_LIMIT,
    count,
    require_session,
    run_in_snapshot,
    summary_in_snapshot,
)

_Node: TypeAlias = tuple[Literal["run", "artifact"], str]


class _Topology:
    def __init__(self, conn: sqlite3.Connection, session_ref: str, max_nodes: int) -> None:
        self.conn = conn
        self.session_ref = session_ref
        self.max_nodes = max_nodes
        self.times: dict[_Node, str] = {}

    def _nodes(
        self, sql: str, parameters: tuple[object, ...], kind: Literal["run", "artifact"]
    ) -> tuple[_Node, ...]:
        values = []
        for row in _rows(self.conn, sql, parameters):
            node: _Node = (kind, _text(row, "id"))
            self.times[node] = _text(row, "at")
            values.append(node)
        return tuple(values)

    def foreign(self, node: _Node) -> bool:
        if node[0] != "artifact":
            return False
        row = _one(
            self.conn, "SELECT session_ref FROM dataset_artifacts WHERE artifact_ref=?", (node[1],)
        )
        if row is None:
            raise invalid("selected graph Artifact is missing")
        return _text(row, "session_ref") != self.session_ref

    def adjacent(self, node: _Node, direction: GraphDirection) -> tuple[_Node, ...]:
        limit = self.max_nodes + 1
        if node[0] == "run":
            if direction == "ancestors":
                return self._nodes(
                    "SELECT d.artifact_ref id,d.committed_at at FROM analysis_action_run_inputs i "
                    "JOIN dataset_artifacts d USING(artifact_ref) WHERE i.session_ref=? AND i.run_ref=? "
                    "GROUP BY d.artifact_ref ORDER BY d.committed_at DESC,d.artifact_ref DESC LIMIT ?",
                    (self.session_ref, node[1], limit),
                    "artifact",
                )
            return self._nodes(
                "SELECT d.artifact_ref id,d.committed_at at FROM analysis_action_run_terminals t "
                "JOIN dataset_artifacts d ON d.artifact_ref=t.output_artifact_ref "
                "WHERE t.session_ref=? AND t.run_ref=? AND t.outcome='succeeded'",
                (self.session_ref, node[1]),
                "artifact",
            )
        if direction == "ancestors":
            if self.foreign(node):
                return ()
            return self._nodes(
                "SELECT a.run_ref id,a.admitted_at at FROM analysis_action_run_terminals t "
                "JOIN analysis_action_runs a USING(run_ref) WHERE t.session_ref=? "
                "AND t.output_artifact_ref=? AND t.outcome='succeeded'",
                (self.session_ref, node[1]),
                "run",
            )
        return self._nodes(
            "SELECT a.run_ref id,a.admitted_at at FROM analysis_action_run_inputs i "
            "JOIN analysis_action_runs a USING(run_ref) WHERE i.artifact_ref=? AND i.session_ref=? "
            "GROUP BY a.run_ref ORDER BY a.admitted_at DESC,a.run_ref DESC LIMIT ?",
            (node[1], self.session_ref, limit),
            "run",
        )

    def is_head(self, ref: str) -> bool:
        return (
            _one(self.conn, _HEAD_SQL + " AND d.artifact_ref=?", (self.session_ref, ref))
            is not None
        )


def _overall(topology: _Topology) -> tuple[set[_Node], bool]:
    conn = topology.conn
    owner = topology.session_ref
    total = count(
        conn,
        "SELECT (SELECT count(*) FROM analysis_action_runs WHERE session_ref=?) + "
        "(SELECT count(*) FROM dataset_artifacts WHERE session_ref=?) + "
        "(SELECT count(DISTINCT i.artifact_ref) FROM analysis_action_run_inputs i "
        "JOIN dataset_artifacts d USING(artifact_ref) WHERE i.session_ref=? AND d.session_ref<>?)",
        (owner, owner, owner, owner),
    )
    if total > OVERALL_GRAPH_SCAN_LIMIT:
        raise SessionGraphTooLargeError.for_count(count=total, limit=OVERALL_GRAPH_SCAN_LIMIT)
    selected: set[_Node] = set()

    def retain(node: _Node) -> bool:
        if node in selected:
            return True
        if len(selected) == topology.max_nodes:
            return False
        selected.add(node)
        return True

    attention = topology._nodes(
        "SELECT a.run_ref id,a.admitted_at at FROM analysis_action_runs a "
        "LEFT JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? "
        "AND (t.outcome='failed' OR t.run_ref IS NULL) ORDER BY a.admitted_at DESC,a.run_ref DESC",
        (owner,),
        "run",
    )
    for node in attention:
        retain(node)
    heads = topology._nodes(
        "SELECT artifact_ref id,committed_at at FROM (" + _HEAD_SQL + ") "
        "ORDER BY committed_at DESC,artifact_ref DESC",
        (owner,),
        "artifact",
    )
    for node in heads:
        retain(node)
    queue = deque(node for node in (*attention, *heads) if node in selected)
    visited: set[_Node] = set()
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for parent in topology.adjacent(node, "ancestors"):
            if retain(parent):
                queue.append(parent)
    remaining = topology._nodes(
        "SELECT a.run_ref id,a.admitted_at at FROM analysis_action_runs a "
        "JOIN analysis_action_run_terminals t USING(run_ref) WHERE a.session_ref=? AND t.outcome='succeeded' "
        "ORDER BY a.admitted_at DESC,a.run_ref DESC",
        (owner,),
        "run",
    )
    for node in remaining:
        if retain(node):
            for output in topology.adjacent(node, "descendants"):
                retain(output)
    return selected, len(selected) < total


def _focused(topology: _Topology, ref: str, direction: GraphDirection) -> tuple[set[_Node], bool]:
    row = _one(
        topology.conn,
        "SELECT d.artifact_ref id,d.committed_at at FROM dataset_artifacts d WHERE d.artifact_ref=? "
        "AND (d.session_ref=? OR EXISTS (SELECT 1 FROM analysis_action_run_inputs i "
        "WHERE i.artifact_ref=d.artifact_ref AND i.session_ref=?))",
        (ref, topology.session_ref, topology.session_ref),
    )
    if row is None:
        raise ArtifactNotFoundError.for_ref(ref)
    focus: _Node = ("artifact", ref)
    topology.times[focus] = _text(row, "at")
    selected = {focus}
    frontier: tuple[_Node, ...] = (focus,)
    truncated = False
    while frontier:
        candidates = {
            neighbor for node in frontier for neighbor in topology.adjacent(node, direction)
        } - selected
        ordered = sorted(
            candidates, key=lambda node: (topology.times[node], node[1], node[0]), reverse=True
        )
        admitted = ordered[: topology.max_nodes - len(selected)]
        selected.update(admitted)
        truncated |= len(admitted) < len(ordered)
        frontier = tuple(admitted)
    return selected, truncated


def _ordered(
    topology: _Topology, selected: set[_Node], edges: tuple[SessionGraphEdge, ...]
) -> tuple[_Node, ...]:
    successors: dict[_Node, set[_Node]] = {node: set() for node in selected}
    incoming = dict.fromkeys(selected, 0)
    for edge in edges:
        run: _Node = ("run", edge.run_id)
        artifact: _Node = ("artifact", edge.artifact_ref.ref)
        source, target = (artifact, run) if edge.kind == "consumes" else (run, artifact)
        if target not in successors[source]:
            successors[source].add(target)
            incoming[target] += 1
    ready = [(topology.times[node], node) for node, degree in incoming.items() if degree == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, node = heapq.heappop(ready)
        result.append(node)
        for successor in successors[node]:
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heapq.heappush(ready, (topology.times[successor], successor))
    if len(result) != len(selected):
        raise SessionGraphIntegrityError.mismatch(
            message="The selected Session graph contains a cycle.",
            expected="acyclic Run-to-Artifact production and consumption",
            received=", ".join(node[1] for node in sorted(selected - set(result)))[:1024],
            location="session.graph",
        )
    return tuple(result)


def graph(
    store: SessionStore,
    session_ref: str,
    *,
    artifact_ref: ArtifactRef | None = None,
    direction: GraphDirection = "ancestors",
    max_nodes: int = 100,
) -> SessionGraph:
    if type(max_nodes) is not int or not 1 <= max_nodes <= 500:
        raise SessionGraphLimitError.for_value(max_nodes)
    if direction not in ("ancestors", "descendants") or (
        artifact_ref is None and direction != "ancestors"
    ):
        raise SessionGraphArgumentError.invalid(
            artifact_ref=None if artifact_ref is None else artifact_ref.ref, direction=direction
        )
    with store._read() as conn:
        require_session(store, conn, session_ref)
        topology = _Topology(conn, session_ref, max_nodes)
        selected, truncated = (
            _overall(topology)
            if artifact_ref is None
            else _focused(topology, artifact_ref.ref, direction)
        )
        artifacts: dict[str, ArtifactSummary] = {}
        runs: dict[str, RunRecord] = {}
        for kind, identity in sorted(selected):
            if kind == "run":
                runs[identity] = run_in_snapshot(store, conn, session_ref, identity)
            else:
                record = store._artifact(conn, identity)
                if record is None:
                    raise invalid("selected graph Artifact is missing")
                artifacts[identity] = summary_in_snapshot(store, conn, record)
        edge_values: list[SessionGraphEdge] = []
        for run in runs.values():
            edge_values.extend(
                SessionGraphEdge(kind="consumes", run_id=run.run_id, artifact_ref=ref)
                for ref in run.input_artifact_refs
                if ref.ref in artifacts
            )
            if isinstance(run, SucceededRun) and run.output_artifact_ref.ref in artifacts:
                edge_values.append(
                    SessionGraphEdge(
                        kind="produces", run_id=run.run_id, artifact_ref=run.output_artifact_ref
                    )
                )
        edges = tuple(edge_values)
        order = _ordered(topology, selected, edges)
        positions = {node: index for index, node in enumerate(order)}
        ordered_runs = tuple(runs[identity] for kind, identity in order if kind == "run")
        ordered_artifacts = tuple(
            artifacts[identity] for kind, identity in order if kind == "artifact"
        )
        boundary_artifacts = set()
        boundary_runs = set()
        for node in selected:
            directions: tuple[GraphDirection, ...] = (
                ("ancestors", "descendants") if artifact_ref is None else (direction,)
            )
            omitted = any(
                neighbor not in selected
                for way in directions
                for neighbor in topology.adjacent(node, way)
            )
            if node[0] == "artifact" and (omitted or topology.foreign(node)):
                boundary_artifacts.add(node[1])
            elif node[0] == "run" and omitted:
                boundary_runs.add(node[1])
        return SessionGraph(
            session_id=session_ref,
            artifacts=ordered_artifacts,
            runs=ordered_runs,
            edges=tuple(
                sorted(
                    edges,
                    key=lambda edge: (
                        positions[("artifact", edge.artifact_ref.ref)]
                        if edge.kind == "consumes"
                        else positions[("run", edge.run_id)],
                        positions[("run", edge.run_id)]
                        if edge.kind == "consumes"
                        else positions[("artifact", edge.artifact_ref.ref)],
                        edge.kind,
                    ),
                )
            ),
            root_run_ids=tuple(run.run_id for run in ordered_runs if not run.input_artifact_refs),
            head_artifact_refs=tuple(
                item.artifact_ref
                for item in ordered_artifacts
                if topology.is_head(item.artifact_ref.ref)
            ),
            failed_run_ids=tuple(run.run_id for run in ordered_runs if isinstance(run, FailedRun)),
            incomplete_run_ids=tuple(
                run.run_id for run in ordered_runs if isinstance(run, IncompleteRun)
            ),
            boundary_artifact_refs=tuple(
                ArtifactRef(ref=ref) for ref in sorted(boundary_artifacts)
            ),
            boundary_run_ids=tuple(sorted(boundary_runs)),
            truncated=truncated,
        )


__all__: list[str] = []
