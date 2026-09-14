"""Event and Lifecycle native disclosure inputs, including exact overload owners."""

from __future__ import annotations

from marivo.analysis._capabilities.dataset_model import (
    CONSTRUCTION_EFFECT,
    CONSTRUCTION_FAILURES,
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    family,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    SourceOriginCompletenessDeclarationV1,
)
from marivo.analysis.domains.contracts import (
    EventFunnelSemantics,
    EventJourneySemantics,
    EventTimeToEventSemantics,
)
from marivo.analysis.domains.lifecycle import LifecycleSemantics
from marivo.analysis.domains.lifecycle_reducers import REDUCER_TYPES, InState, in_state
from marivo.analysis.event import (
    EventPattern,
    EveryStart,
    FirstPerSubject,
    PatternStep,
    every_start,
    first_per_subject,
    sequence,
    step,
)
from marivo.analysis.funnel import FunnelLossRate, funnel_loss_rate
from marivo.analysis.lifecycle import FromInception, from_inception
from marivo.analysis.session._lazy_sources import LazyEvents, LazyLifecycle
from marivo.analysis.subject import DroppedBefore, dropped_before
from marivo.refs import SemanticKind


def provider(registry: DatasetFamilyRegistry) -> DisclosureProvider:
    parameters: tuple[P, ...]
    requires: tuple[str, ...]
    registrations: tuple[str, ...]
    variants: tuple[type[object], ...]
    value: object
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    for fid, variants, summary in (
        (
            "event",
            (EventJourneySemantics, EventFunnelSemantics, EventTimeToEventSemantics),
            "Dense subject journeys and their funnel or time-to-event projections.",
        ),
        (
            "lifecycle",
            (LifecycleSemantics, *REDUCER_TYPES),
            "From-inception state history with distribution, transition, dwell and violation projections.",
        ),
    ):
        f = registry.get(fid)
        target = fid + "_dataset"
        descriptors.append(
            family(
                target,
                f,
                summary=summary,
                variants=variants,
                acquisition=(
                    "Construct via session.events.match(...)."
                    if fid == "event"
                    else "Construct via session.lifecycle.replay(...)."
                )
                + " Reducers remain in the same family.",
                constraints=(
                    "Raw Entity identities remain private until explicit authorized terminal row reads.",
                    "Right censoring and coverage censoring differ; membership selection requires exact complete identities.",
                    "Logical reuse shares the plan; materialized continuations consume only their retained roles.",
                ),
            )
        )
        exports.extend(
            ExportInput(t.__name__, t, target) for t in (f.logical_type, f.materialized_type)
        )

    for target, value, entry, parameters, output, code, requires, constraint, registrations in (
        (
            "events.match",
            LazyEvents.match,
            "session.events.match",
            (
                P(
                    "pattern",
                    "Build an ordered sequence of exact participant-role steps.",
                    ("sequence",),
                ),
                P("cohort_window", "Choose the explicit anchor cohort window.", ("time_scope",)),
                P("completion_through", "Choose an aware exclusive follow-up endpoint."),
                P(
                    "matching",
                    "Choose first-per-subject or every-start matching explicitly.",
                    ("event_matching",),
                ),
                P(
                    "population",
                    "Use an admitted exact same-Session membership input.",
                    ("population",),
                ),
                P(
                    "completeness",
                    "Supply explicit bounded/source-origin declarations; declarations do not prove source coverage.",
                    ("BoundedCompletenessDeclarationV1", "SourceOriginCompletenessDeclarationV1"),
                ),
            ),
            "LogicalEventDataset",
            "result = session.events.match(pattern, cohort_window=window, completion_through=end, matching=first_per_subject(), completeness=event_completeness)",
            ("session", "pattern", "window", "end", "first_per_subject", "event_completeness"),
            "Matching policy, anchor scope, follow-up and coverage authority are separate choices.",
            ("session.events.match",),
        ),
        (
            "lifecycle.replay",
            LazyLifecycle.replay,
            "session.lifecycle.replay",
            (
                P("model", "Choose an exact current StateModel ref or catalog entry."),
                P("window", "Choose an explicit aware replay window.", ("time_scope",)),
                P("seed", "Use from_inception(); no ad-hoc state snapshot.", ("from_inception",)),
                P("population", "Use exact admitted membership or None.", ("population",)),
                P(
                    "completeness",
                    "Supply source-origin declarations for the model's trigger Events.",
                    ("SourceOriginCompletenessDeclarationV1",),
                ),
            ),
            "LogicalLifecycleDataset",
            "result = session.lifecycle.replay(model, window=window, seed=from_inception(), completeness=lifecycle_completeness)",
            ("session", "model", "window", "from_inception", "lifecycle_completeness"),
            "Replay requires from-inception history and exact trigger coverage; a bounded prefix cannot replace source-origin completeness.",
            ("session.lifecycle.replay",),
        ),
    ):
        descriptors.append(
            operation(
                target,
                entry,
                value,
                semantic_kinds=(SemanticKind.EVENT,)
                if target == "events.match"
                else (SemanticKind.STATE_MODEL,),
                summary=constraint,
                discovery_group="entry",
                related=("session.get_or_create", "catalog.require", "catalog.readiness"),
                parameters=parameters,
                output=output,
                constraints=(constraint,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(code, requires, "result", output),
                registration_ids=registrations,
            )
        )

    methods = (
        (
            "event",
            "funnel",
            "event_dataset.funnel",
            ("event.funnel",),
            (
                P(
                    "axes",
                    "Choose stable governed non-time Dimensions for complete journey partitions.",
                ),
            ),
            "LogicalEventDataset",
            "result = events.funnel()",
            ("events",),
            "Reduce dense journeys with fixed denominators and separate censoring counts.",
        ),
        (
            "event",
            "time_to_event",
            "event_dataset.time_to_event",
            ("event.time_to_event",),
            (
                P("from_step", "Use an exact source-pattern step."),
                P("to_step", "Use a later exact source-pattern step."),
            ),
            "LogicalEventDataset",
            "result = events.time_to_event(from_step=start_step, to_step=finish_step)",
            ("events", "start_step", "finish_step"),
            "Pair-local entry classification preserves completed versus observed durations.",
        ),
        (
            "event",
            "select_subjects",
            "event_dataset.select_subjects",
            ("event.select_subjects",),
            (
                P(
                    "selection",
                    "Construct dropped_before for an exact non-initial source step.",
                    ("dropped_before",),
                ),
            ),
            "LogicalPopulationDataset",
            "result = events.select_subjects(dropped_before(step=finish_step))",
            ("events", "dropped_before", "finish_step"),
            "Select resolved losses with proven complete membership; row filters do not create a new cohort.",
        ),
        (
            "event",
            "compare",
            "event_dataset.compare",
            ("event.compare",),
            (
                P(
                    "baseline",
                    "Use a complete-journey funnel with compatible matching, pattern, axes and scope.",
                ),
            ),
            "LogicalDeltaDataset",
            "result = events.funnel().compare(events.funnel())",
            ("events",),
            "Compare compatible complete funnel partitions without rematching journeys.",
        ),
        (
            "delta",
            "attribute",
            "funnel_delta_dataset.attribute",
            ("delta.funnel_attribute",),
            (
                P("axes", "Choose scoped stable Dimensions for complete journey partitions."),
                P("mode", "Use the supported joint contribution mode."),
                P("top_k", "Optional positive contribution bound."),
                P(
                    "target",
                    "Construct the exact funnel loss-rate endpoint pair.",
                    ("funnel_loss_rate",),
                ),
            ),
            "LogicalAttributionDataset",
            "result = funnel_delta.attribute(axes=(region,), target=funnel_loss_rate(step=finish_step))",
            ("funnel_delta", "region", "funnel_loss_rate", "finish_step"),
            "Reconcile scoped funnel loss rates; Metric attribution parameters do not authorize funnel inputs.",
        ),
        (
            "lifecycle",
            "distribution",
            "lifecycle_dataset.distribution",
            ("lifecycle.distribution",),
            (
                P(
                    "at",
                    "Choose a nonempty tuple of unique aware instants within the replay window.",
                ),
                P("axes", "Choose stable Dimensions at the requested checkpoints."),
            ),
            "LogicalLifecycleDataset",
            "result = lifecycle.distribution(at=(end,))",
            ("lifecycle", "end"),
            "Read exact state at each checkpoint from retained intervals.",
        ),
        (
            "lifecycle",
            "transitions",
            "lifecycle_dataset.transitions",
            ("lifecycle.transitions",),
            (),
            "LogicalLifecycleDataset",
            "result = lifecycle.transitions()",
            ("lifecycle",),
            "Read the lossless transition trace; no replay of original Events.",
        ),
        (
            "lifecycle",
            "dwell",
            "lifecycle_dataset.dwell",
            ("lifecycle.dwell",),
            (),
            "LogicalLifecycleDataset",
            "result = lifecycle.dwell()",
            ("lifecycle",),
            "Completed window-fragment dwell duration; disclose left clipping and right/coverage censoring.",
        ),
        (
            "lifecycle",
            "violations",
            "lifecycle_dataset.violations",
            ("lifecycle.violations",),
            (),
            "LogicalLifecycleDataset",
            "result = lifecycle.violations()",
            ("lifecycle",),
            "Read exact violation evidence from the retained trace.",
        ),
        (
            "lifecycle",
            "select_subjects",
            "lifecycle_dataset.select_subjects",
            ("lifecycle.select_subjects",),
            (
                P(
                    "selection",
                    "Construct in_state with an exact model state handle and aware checkpoint.",
                    ("in_state",),
                ),
            ),
            "LogicalPopulationDataset",
            "result = lifecycle.select_subjects(in_state(done_state, at=end))",
            ("lifecycle", "in_state", "done_state", "end"),
            "Select exact checkpoint membership, rejecting incompatible models or incomplete coverage.",
        ),
    )
    for fid, name, target, registrations, parameters, output, code, requires, constraint in methods:
        f = registry.get(fid)
        bindings = tuple(bind(getattr(t, name), t) for t in (f.logical_type, f.materialized_type))
        descriptors.append(
            operation(
                target,
                "dataset." + name,
                bindings[0].implementation,
                bindings=bindings,
                summary=constraint,
                discovery_group="methods.compare"
                if name in ("compare", "attribute")
                else "methods.events"
                if fid == "event"
                else "methods.lifecycle",
                parameters=parameters,
                output=output,
                constraints=(constraint,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(code, requires, "result", output),
                registration_ids=registrations,
            )
        )

    constructors = (
        (
            "step",
            "step",
            step,
            "step(participant=start_role, key='started')",
            "PatternStep",
            "Use ms.participant_role(...) and a unique lowercase step key.",
        ),
        (
            "sequence",
            "sequence",
            sequence,
            "sequence(start_step, finish_step)",
            "EventPattern",
            "Provide ordered unique steps in one subject domain.",
        ),
        (
            "first_per_subject",
            "event_matching.first_per_subject",
            first_per_subject,
            "first_per_subject()",
            "FirstPerSubject",
            "Select the first qualifying start for each subject.",
        ),
        (
            "every_start",
            "event_matching.every_start",
            every_start,
            "every_start(completion_assignment='exclusive')",
            "EveryStart",
            "Admit each qualifying start under the governed assignment rules.",
        ),
        (
            "dropped_before",
            "dropped_before",
            dropped_before,
            "dropped_before(step=finish_step)",
            "DroppedBefore",
            "Choose an exact non-initial source-pattern step.",
        ),
        (
            "in_state",
            "in_state",
            in_state,
            "in_state(done_state, at=end)",
            "InState",
            "Choose an exact StateModel state handle and an aware checkpoint.",
        ),
        (
            "funnel_loss_rate",
            "funnel_loss_rate",
            funnel_loss_rate,
            "funnel_loss_rate(step=finish_step)",
            "FunnelLossRate",
            "Choose an exact non-initial PatternStep; the objective is loss from its immediately preceding step.",
        ),
        (
            "from_inception",
            "from_inception",
            from_inception,
            "from_inception()",
            "FromInception",
            "Require the governed source-origin history, not a user-supplied seed snapshot.",
        ),
        (
            "BoundedCompletenessDeclarationV1",
            "BoundedCompletenessDeclarationV1.create",
            BoundedCompletenessDeclarationV1,
            "BoundedCompletenessDeclarationV1(inputs=event_refs, complete_from=start, complete_through=end, rationale='Source coverage declaration')",
            "BoundedCompletenessDeclarationV1",
            "Declare exact Event refs and aware coverage bounds with an explicit rationale.",
        ),
        (
            "SourceOriginCompletenessDeclarationV1",
            "SourceOriginCompletenessDeclarationV1.create",
            SourceOriginCompletenessDeclarationV1,
            "SourceOriginCompletenessDeclarationV1(inputs=event_refs, source_origin_ref=source_origin, complete_through=end, rationale='Source-origin declaration')",
            "SourceOriginCompletenessDeclarationV1",
            "Declare exact Event refs, source origin and aware complete-through bound with a rationale.",
        ),
    )
    constructor_inputs = {
        "participant": "Acquire the exact handle with ms.participant_role(event=event_ref, name=role_name).",
        "key": "Choose a unique lowercase snake-case key within this pattern.",
        "steps": "Pass the exact ordered PatternStep values created by step(...).",
        "completion_assignment": "Choose exclusive for earliest-open-attempt assignment, or shared only when one completion may complete multiple attempts.",
        "step": "Use the exact non-initial PatternStep retained by the source pattern.",
        "state": "Use ms.model_state(model=model_ref, name=state_name) after inspecting the exact catalog StateModel; strings and foreign model handles are rejected.",
        "at": "Choose a timezone-aware checkpoint inside the source Lifecycle replay window.",
        "inputs": "List the exact distinct Event refs covered by this declaration.",
        "complete_from": "Choose the aware inclusive start of declared bounded source coverage.",
        "complete_through": "Choose the aware end of declared source coverage; this is not an observed watermark.",
        "source_origin_ref": "Select the datasource ref that owns the declared Event history.",
        "rationale": "Supply a nonempty explanation of the explicit coverage assumption.",
    }
    for name, target, value, code, output, guidance in constructors:
        descriptors.append(
            operation(
                target,
                "mv." + name,
                value,
                summary=guidance,
                discovery_group="event_matching"
                if name in ("first_per_subject", "every_start")
                else "inputs.lifecycle"
                if name in ("in_state", "from_inception", "SourceOriginCompletenessDeclarationV1")
                else "inputs.events",
                related=("catalog.require",),
                parameters=tuple(
                    P(n, constructor_inputs[n]) for n in bind(value).signature.parameters
                ),
                output=output,
                constraints=(guidance,),
                effects=CONSTRUCTION_EFFECT,
                failures=(
                    "AnalysisError: use the exact typed inputs named by the structured repair.",
                ),
                example=ExampleInput(
                    "result = " + code,
                    (name, "start_role")
                    if name == "step"
                    else (name, "start_step", "finish_step")
                    if name == "sequence"
                    else (name, "finish_step")
                    if name in ("dropped_before", "funnel_loss_rate")
                    else (name, "done_state", "end")
                    if name == "in_state"
                    else (name, "event_refs", "start", "end")
                    if name == "BoundedCompletenessDeclarationV1"
                    else (name, "event_refs", "source_origin", "end")
                    if name == "SourceOriginCompletenessDeclarationV1"
                    else (name,),
                    "result",
                    output,
                ),
            )
        )
        if not isinstance(value, type):
            exports.append(ExportInput(name, value, target))
    for type_value, producer in (
        (PatternStep, "step"),
        (EventPattern, "sequence"),
        (FirstPerSubject, "event_matching.first_per_subject"),
        (EveryStart, "event_matching.every_start"),
        (DroppedBefore, "dropped_before"),
        (InState, "in_state"),
        (FunnelLossRate, "funnel_loss_rate"),
        (FromInception, "from_inception"),
        (BoundedCompletenessDeclarationV1, "BoundedCompletenessDeclarationV1.create"),
        (SourceOriginCompletenessDeclarationV1, "SourceOriginCompletenessDeclarationV1.create"),
    ):
        target = type_value.__name__
        descriptors.append(
            value_type(
                target,
                type_value,
                summary=f"Exact {target} type_value contract.",
                acquisition="Use its registered constructor with exact semantic handles.",
                producers=(producer,),
            )
        )
        exports.append(ExportInput(target, type_value, target))
    return DisclosureProvider("domains", tuple(descriptors), tuple(exports))
