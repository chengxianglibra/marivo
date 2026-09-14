"""Native task navigation, separate from the exact type and member inventory."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    DisclosureProvider,
    FamilyInput,
    NavigationInput,
    invalid,
)

# This registry owns group meaning and parentage. Capability membership is
# declared by each native provider, never inferred from descriptor ordering.
_GROUPS = (
    ("inputs.population", "inputs", "Select or sample exact Entity membership."),
    ("inputs.time", "inputs", "Choose observation windows, grains and comparison alignment."),
    ("inputs.events", "inputs", "Build participant patterns and explicit Event completeness."),
    ("inputs.lifecycle", "inputs", "Choose state selection, inception and source completeness."),
    ("inputs.forecast", "inputs", "Choose forecast horizon and model assumptions."),
    ("filters", "inputs", "Construct typed predicates over governed inputs or owned fields."),
    ("forecast_models", "inputs.forecast", "Choose naive, drift or seasonal baseline forecasts."),
    ("event_matching", "inputs.events", "Choose first or repeated starts per subject."),
    ("methods.metric", "methods", "Add axes, aggregate, roll up or select a Metric."),
    ("methods.compare", "methods", "Compare scopes and attribute Metric or funnel changes."),
    ("methods.rows", "methods", "Filter result rows, rank them or retain an ordered prefix."),
    ("methods.association", "methods", "Measure descriptive association, including time lags."),
    ("methods.forecast", "methods", "Project a governed time series under explicit assumptions."),
    ("discovery", "methods", "Screen anomalies, shifts, outliers and candidate driver axes."),
    ("methods.events", "methods", "Inspect funnels and time to event, or select subjects."),
    (
        "methods.lifecycle",
        "methods",
        "Inspect state distributions, transitions, dwell and violations.",
    ),
    ("runtime.sessions", "runtime", "Find, inspect or activate an existing Session."),
    ("session.namespace", "runtime.sessions", "Create, resume or inspect project-local Sessions."),
    ("runtime.runs", "runtime", "Read bounded Run history or one exact Run."),
    ("runtime.values", "runtime", "Render a retained Runtime or Evidence value."),
)

_HUBS = (
    NavigationInput(
        "entry",
        "Start from governed inputs or resume an exact committed analysis branch.",
        (),
        "decision_hub",
        guidance=(
            "New question: create a named Session, resolve only the required semantic inputs, then choose a source below.",
            "Known handoff: use its exact refs and scope; do not browse the whole catalog again.",
            "Existing work: resume the Session and follow runtime reads; do not replay successful sources.",
        ),
        related=("session.get_or_create", "session.resume", "catalog", "runtime"),
    ),
    NavigationInput(
        "methods",
        "Choose the analytical intent; an existing Dataset contract narrows legal continuations.",
        (),
        "decision_hub",
    ),
    NavigationInput(
        "inputs",
        "Acquire only the missing governed input, policy or explicit scope.",
        (),
        "decision_hub",
    ),
    NavigationInput(
        "artifacts",
        "Understand Dataset row meaning, states, fields and retained reads.",
        (),
        "decision_hub",
    ),
    NavigationInput(
        "evidence",
        "Inspect the evidence needed for a conclusion and preserve its limitations.",
        (),
        "decision_hub",
        guidance=(
            "Start with materialized.show() and materialized.evidence_digest; read selected Findings when the question needs detail.",
            "Revalidation checks Artifact, storage and Evidence integrity, not current semantic authority or source freshness.",
        ),
        related=("ArtifactDigest", "runtime.values", "actions.show"),
    ),
    NavigationInput(
        "runtime",
        "Recover committed work and audit exact Session, Run and Artifact identities.",
        (),
        "decision_hub",
        guidance=(
            "Cold recovery: resume a Session, read bounded Run history, then recover its exact committed Artifact.",
            "Use a scoped graph only for factual adjacency; recovery does not execute origin queries.",
        ),
    ),
)


def navigation(providers: tuple[DisclosureProvider, ...]) -> tuple[NavigationInput, ...]:
    """Assemble owner-declared discovery groups without paging a type inventory."""
    buckets: dict[str, list[str]] = {hub.canonical_id: [] for hub in _HUBS}
    buckets.update((target, []) for target, _, _ in _GROUPS)
    nested = {
        member
        for provider in providers
        for descriptor in provider.descriptors
        if isinstance(descriptor, NavigationInput)
        for member in descriptor.members
    }
    group: str | None
    for provider in providers:
        for descriptor in provider.descriptors:
            if descriptor.canonical_id in nested:
                continue
            if isinstance(descriptor, FamilyInput):
                group = "artifacts"
            elif isinstance(descriptor, (CallableInput, NavigationInput)):
                group = descriptor.discovery_group
            else:
                continue
            if group is not None:
                if group not in buckets:
                    raise invalid("an existing native discovery group", group)
                buckets[group].append(descriptor.canonical_id)
    for target, parent, _ in _GROUPS:
        buckets[parent].append(target)
    result = [
        NavigationInput(target, summary, tuple(buckets[target])) for target, _, summary in _GROUPS
    ]
    result.extend(replace(hub, members=tuple(buckets[hub.canonical_id])) for hub in _HUBS)
    result.append(
        NavigationInput(
            "",
            "Governed lazy analysis: construct, explicitly execute, inspect committed results.",
            tuple(hub.canonical_id for hub in _HUBS),
            "root",
            guidance=(
                "Imports: import marivo; import marivo.analysis as mv; import marivo.semantic as ms",
                "The agent owns the question, method choice and interpretation; Marivo owns typed computation and Evidence.",
                "Logical Dataset construction reads no source rows. execute() returns the paired Materialized Dataset.",
                "Use dataset.contract().show() for current legal calls; materialized.show() reads bounded committed results.",
                "Follow one relevant route and its input links, then write and run the smallest useful analysis.",
            ),
        )
    )
    return tuple(result)
