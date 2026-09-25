"""Guarded private J1 execution using the existing Dataset Runtime and Store."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Literal, TypeAlias

import ibis.expr.types as ir

from marivo.analysis.compiler.normalize import _mixed_input_error, require_unmixed_inputs
from marivo.analysis.compiler.placement import place_j1_local, place_j1_source
from marivo.analysis.datasets.base import LogicalDataset, _make_logical_dataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.contracts import RunDatasetInput, run_failure_phase
from marivo.analysis.materialization.dsl_j1_artifact import (
    J1Node,
    _context,
    load_j1_artifact,
    publish_j1_artifact,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.execution_key import fixed_execution_key, source_execution_key
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.source_stage import J1IbisBackend
from marivo.analysis.materialization.store import _new_run_ref
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import (
    ObservationRuntimeOwner,
    make_family_registry,
    make_ids,
)
from marivo.analysis.observation.dsl_j1 import (
    J1Difference,
    J4Association,
    J4CoefficientSelection,
    J4CoefficientStatistic,
    j1_row_contracts,
)
from marivo.analysis.observation.dsl_j1_dataset import J1SourcePayload, MaterializedJ1Dataset
from marivo.analysis.operators.dsl_j1_contracts import j1_numeric_method

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime

J1SourceFactory: TypeAlias = Callable[
    [], AbstractContextManager[tuple[J1IbisBackend, Mapping[str, ir.Table]]]
]


def _reject(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="one J1 source root or one exact admitted local predecessor",
        received=received,
        repair="Use the same logical J1 node for a fresh source evaluation or select its exact saved predecessor.",
        location="dsl.j1.runtime",
    )


def _source_facts(node: J1Node) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(root: object) -> None:
        from marivo.analysis.datasets.handles import LogicalRootHandle

        if not isinstance(root, LogicalRootHandle):
            return
        found.update(root.dependency_facts)
        for child in root.inputs:
            visit(child.root)

    visit(node.root)
    if not found:
        raise _reject("J1 source has no declared dependency")
    return tuple(sorted(found))


def _binding(
    self: DatasetRuntime,
    node: J1Node,
    *,
    retained: tuple[MaterializedJ1Dataset, ...] | MaterializedJ1Dataset | None,
    live: bool,
) -> LogicalDataset:
    context = _context(node)
    if context.session_id != self.session_ref or context.store_id != self.store.store_id:
        raise _reject("foreign J1 Session or Store")
    selected_inputs = (
        ()
        if retained is None
        else (retained,)
        if isinstance(retained, MaterializedJ1Dataset)
        else retained
    )
    row, rows = j1_row_contracts(context, node.root)
    method = j1_numeric_method(node.root)
    method_id = node.root.operator_id if method is None else method.contract.method_id
    method_version = 1 if method is None else method.contract.version
    registry = make_family_registry(make_ids(()), include_j1=True)
    owner = ObservationRuntimeOwner(
        session_id=self.session_ref,
        store_id=self.store.store_id,
        action_port=self,
        source_context=self._source_context,
    )
    return _make_logical_dataset(
        owner=owner,
        registry=registry,
        family_id="dsl_j1",
        row_contract=row,
        row_set_contract=rows,
        operator_id=node.root.operator_id,
        inputs=selected_inputs,
        input_roles=(
            ("left", "right")
            if isinstance(node, J4Association) and selected_inputs
            else ("current", "baseline")
            if isinstance(node, J1Difference) and selected_inputs
            else ("input",)
            if selected_inputs
            else ()
        ),
        parameters=() if live else (node.root.definition_fingerprint, method_id, method_version),
        contract_versions=node.root.contract_versions,
        payload=(
            J1SourcePayload(
                _token=_CORE_TOKEN,
                semantic_definition=node.root.definition_fingerprint,
                sources=_source_facts(node),
                method_id=method_id,
                method_version=method_version,
            )
            if live
            else None
        ),
    )


def _validated_result(
    self: DatasetRuntime,
    artifact_ref: str,
    node: J1Node,
    key: str,
    *,
    public_snapshot: str | None = None,
) -> MaterializedJ1Dataset:
    record = self.store.artifact(artifact_ref)
    if record is None or record.execution_key_digest != key:
        raise _error("authority_resolution")
    if public_snapshot is not None and (
        record.descriptor.j1_exchange is None
        or record.descriptor.j1_exchange.public_snapshot != public_snapshot
    ):
        raise _reject("fixed Artifact has no matching public continuation snapshot")
    load_j1_artifact(
        self.store,
        self.session_ref,
        artifact_ref,
        node,
        input_binding=key,
    )
    recovered = self._recover(record)
    if not isinstance(recovered, MaterializedJ1Dataset):
        raise _error("authority_resolution")
    return recovered


def execute_j1(
    self: DatasetRuntime,
    node: J1Node,
    *,
    source: J1SourceFactory | None = None,
    input_node: J1Node | None = None,
    input_artifact_ref: str | None = None,
    input_nodes: tuple[J1Node, J1Node] | None = None,
    input_artifact_refs: tuple[str, str] | None = None,
    source_route: Literal["automatic", "python", "source_numeric"] = "automatic",
    public_snapshot: str | None = None,
) -> MaterializedJ1Dataset:
    """Execute one private J1 root, selecting fresh source or exact fixed input."""
    context = _context(node)
    if context.session_id != self.session_ref or context.store_id != self.store.store_id:
        raise _reject("foreign J1 Session or Store")
    if source_route not in ("automatic", "python", "source_numeric"):
        raise _reject("unknown J4 source route")
    if source_route != "automatic" and (not isinstance(node, J4Association) or source is None):
        raise _reject("J4 source route requires a live J4 Association")
    if (
        node.root.operator_id == "dsl.j1.observe"
        and "count_observation@v1" in node.root.requirements
    ):
        raise _reject("P1 count observation is admitted only as a J4 source input")
    pair = isinstance(node, (J1Difference, J4Association))
    fixed = any(
        value is not None
        for value in (input_node, input_artifact_ref, input_nodes, input_artifact_refs)
    )
    predecessors: tuple[J1Node, ...]
    refs: tuple[str, ...]
    if fixed:
        if source is not None:
            raise _mixed_input_error()
        if isinstance(node, (J1Difference, J4Association)):
            if (
                input_node is not None
                or input_artifact_ref is not None
                or input_nodes is None
                or input_artifact_refs is None
                or input_nodes
                != (
                    (node.left, node.right)
                    if isinstance(node, J4Association)
                    else (node.current, node.baseline)
                )
                or type(input_artifact_refs) is not tuple
                or len(input_artifact_refs) != 2
                or any(type(ref) is not str or not ref for ref in input_artifact_refs)
            ):
                raise _reject("comparison requires two exact ordered fixed endpoints")
            predecessors = input_nodes
            refs = input_artifact_refs
        elif (
            input_node is not None
            and input_artifact_ref is not None
            and (input_nodes is None and input_artifact_refs is None)
        ):
            predecessors = (input_node,)
            refs = (input_artifact_ref,)
        else:
            raise _reject("incomplete fixed predecessor or concurrent source selection")
        if len(node.root.inputs) != len(predecessors) or any(
            binding.root is not predecessor.root
            for binding, predecessor in zip(node.root.inputs, predecessors, strict=True)
        ):
            raise _reject("selected predecessor differs from the current J1 node")
        if node.root.operator_id in ("dsl.j1.read", "dsl.j1.observe", "dsl.j1.ratio_observe"):
            raise _mixed_input_error()
        if isinstance(node, J4CoefficientSelection) and predecessors != (node.association,):
            raise _reject("coefficient selection requires its exact Association predecessor")
        if isinstance(node, J4CoefficientStatistic) and predecessors != (node.predecessor,):
            raise _reject("coefficient statistic requires its exact predecessor")
        place_j1_local(
            node.root,
            predecessors[0].root,
            baseline_root=predecessors[1].root if len(predecessors) == 2 else None,
        )
        live = False
    else:
        if source is None:
            raise _reject("missing source factory")
        if isinstance(node, (J4CoefficientSelection, J4CoefficientStatistic)):
            raise _reject("coefficient continuation requires a fixed Association Artifact")
        place_j1_source(context, node.root, "duckdb")
        live = True
        predecessors = ()
        refs = ()

    with session_writer_guard(
        self.store.layout.lock_path(self.session_ref), session_ref=self.session_ref
    ):
        from marivo.analysis.materialization.admission import ExecutionStatistics

        self.statistics = ExecutionStatistics()
        self.last_run_ref = None
        reconcile_session(self.store, self.session_ref, event=self._event)
        selected_records = []
        retained_inputs = []
        for ref, predecessor in zip(refs, predecessors, strict=True):
            selected = self.store.artifact(ref)
            if selected is None or selected.session_ref != self.session_ref:
                raise _reject("missing or foreign input Artifact")
            if (
                selected.descriptor.definition_fingerprint
                != predecessor.root.definition_fingerprint
            ):
                raise _reject("selected Artifact differs from the ordered J1 predecessor")
            exchange = selected.descriptor.j1_exchange
            method = j1_numeric_method(predecessor.root)
            if (
                exchange is None
                or exchange.operator_id != predecessor.root.operator_id
                or exchange.input_binding != selected.execution_key_digest
                or exchange.method_id
                != (predecessor.root.operator_id if method is None else method.contract.method_id)
                or exchange.method_version != (1 if method is None else method.contract.version)
            ):
                raise _reject("input Artifact has a wrong method or version")
            recovered = self._recover(selected)
            if not isinstance(recovered, MaterializedJ1Dataset):
                raise _reject("input Artifact is not a J1 result")
            selected_records.append(selected)
            retained_inputs.append(recovered)
        if pair and fixed:
            current_exchange = selected_records[0].descriptor.j1_exchange
            baseline_exchange = selected_records[1].descriptor.j1_exchange
            if (
                current_exchange is None
                or baseline_exchange is None
                or current_exchange.member_binding is None
                or current_exchange.member_binding != baseline_exchange.member_binding
            ):
                raise _reject("comparison endpoints lack one shared member implementation")
            if isinstance(node, J4Association) and (
                current_exchange.operator_id != "dsl.j1.observe"
                or baseline_exchange.operator_id != "dsl.j1.observe"
                or current_exchange.domain != baseline_exchange.domain
                or not {"complete_coverage", "contribution_partition"}
                <= set(current_exchange.completed_checks)
                or not {"complete_coverage", "contribution_partition"}
                <= set(baseline_exchange.completed_checks)
            ):
                raise _reject("Spearman endpoints lack complete matching observation authority")
        definition = _binding(self, node, retained=tuple(retained_inputs), live=live)
        if not isinstance(definition._root, LogicalRootHandle):
            raise _reject("invalid J1 execution root")
        classification = require_unmixed_inputs(definition._root)
        if fixed and classification.kind != "artifact":
            raise _reject("fixed input has live source dependencies")
        if not fixed and classification.kind != "source":
            raise _reject("source input has no live dependency")
        if fixed:
            key = fixed_execution_key(definition, tuple(selected_records))
            hit = self.store.lookup(self.session_ref, key)
            if hit is not None:
                self.last_run_ref = hit.producing_run_ref
                recovered_hit = _validated_result(
                    self, hit.artifact_ref, node, key, public_snapshot=public_snapshot
                )
                self.statistics.j1_fixed_cache_hits += 1
                return recovered_hit
        run_ref = None
        if not fixed:
            # The Run ref used for the key must be exactly the ref admitted below.
            run_ref = _new_run_ref()
            key = source_execution_key(definition, run_ref)
        row, _ = j1_row_contracts(context, node.root)
        run = self.store.admit(
            self.session_ref,
            key,
            RunDatasetInput(
                node.root.definition_fingerprint,
                row.shape_id,
                node.root.row_contract_fingerprint,
                node.root.row_set_contract_fingerprint,
                (node.root.operator_id,),
                _source_facts(node) if not fixed else (),
            ),
            input_artifact_refs=tuple(record.artifact_ref for record in selected_records),
            run_ref=run_ref,
        )
        self.last_run_ref = run.run_ref
        phase = "execution_boundary"
        try:
            if fixed:
                prior = []
                for selected, predecessor in zip(selected_records, predecessors, strict=True):
                    exchange = selected.descriptor.j1_exchange
                    if exchange is None:
                        raise _reject("input Artifact has no J1 exchange")
                    prior.append(
                        load_j1_artifact(
                            self.store,
                            self.session_ref,
                            selected.artifact_ref,
                            predecessor,
                            input_binding=exchange.input_binding,
                        )
                    )
                from marivo.analysis.materialization.dsl_j4_source import (
                    filter_j4_local,
                    run_j4_local,
                    summarize_j4_local,
                )
                from marivo.analysis.materialization.local_stage import (
                    run_j1_compare_local,
                    run_j1_local,
                )

                phase = "stage_execution"
                result = (
                    run_j4_local(node, prior[0], prior[1])
                    if isinstance(node, J4Association)
                    else summarize_j4_local(node, prior[0])
                    if isinstance(node, J4CoefficientStatistic)
                    else filter_j4_local(node, prior[0])
                    if isinstance(node, J4CoefficientSelection)
                    else run_j1_compare_local(node.root, prior[0], prior[1])
                    if pair
                    else run_j1_local(node.root, prior[0])
                )
            else:
                if source is None:
                    raise _reject("missing source factory")
                from marivo.analysis.materialization.dsl_j4_source import (
                    choose_j4_source_route,
                    j4_execution_result,
                    run_j4_source,
                    run_j4_source_numeric,
                )
                from marivo.analysis.materialization.source_stage import run_j1_source

                phase = "stage_execution"
                with source() as (backend, tables):
                    self.statistics.j1_source_evaluations += 1
                    selected_route = None
                    if isinstance(node, J4Association):
                        selected_route = (
                            choose_j4_source_route(j1_numeric_method(node.root), backend.name)
                            if source_route == "automatic"
                            else "source"
                            if source_route == "python"
                            else "source_numeric"
                        )
                    result = (
                        j4_execution_result(
                            node,
                            run_j4_source(node, backend, tables)
                            if selected_route == "source"
                            else run_j4_source_numeric(node, backend, tables),
                        )
                        if isinstance(node, J4Association)
                        else run_j1_source(context, node.root, backend, tables)
                    )
            phase = "publication"
            record = publish_j1_artifact(
                self.store,
                run,
                node,
                result,
                input_binding=key,
                member_binding=(
                    selected_records[0].descriptor.j1_exchange.member_binding
                    if fixed and selected_records[0].descriptor.j1_exchange is not None
                    else run.run_ref
                ),
                public_snapshot=public_snapshot,
                event=self._event,
            )
            return _validated_result(
                self, record.artifact_ref, node, key, public_snapshot=public_snapshot
            )
        except BaseException as exc:
            if isinstance(exc, MaterializationError) and exc.run_ref is None:
                exc.run_ref = run.run_ref
            safe = exc if isinstance(exc, MaterializationError) else _error(phase, run.run_ref)
            from marivo.analysis.materialization.dataset_publication import resolve_outcome

            try:
                outcome = resolve_outcome(self, run, safe, run_failure_phase(safe.stage, phase))
                if outcome is not None:
                    return _validated_result(
                        self,
                        outcome.state.artifact_ref.ref,
                        node,
                        key,
                        public_snapshot=public_snapshot,
                    )
            except MaterializationError as resolution_error:
                if resolution_error.run_ref is None:
                    resolution_error.run_ref = run.run_ref
                raise resolution_error from None
            except Exception:
                raise MaterializationError(
                    expected="a resolved outcome for the original J1 Run",
                    received="outcome resolution failed",
                    repair="Inspect the original Run and resource journal before retrying; do not replay the source automatically.",
                    stage="reconciliation",
                    run_ref=run.run_ref,
                ) from None
            raise
