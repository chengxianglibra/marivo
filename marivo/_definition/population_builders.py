"""Pure construction of closed coordinate domains and allocation rules.

Public adapters and symbolic binding use these same builders. They consume
definition facts only; no Session, source reader or public semantic wrapper
belongs in this module.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo._definition.correspondence import (
    ComparisonAxis,
    coordinate_matches,
    coordinate_position,
)
from marivo._definition.expressions import PredicateExpression
from marivo._definition.relations import (
    LiteralPopulation,
    MembershipBinding,
    PopulationClassify,
    PopulationDependency,
    PopulationFilter,
    PopulationImage,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    condition_populations,
    merge_time_axis,
)
from marivo._definition.types import (
    AttributeTime,
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    EntityIdentityRead,
    ExactRef,
    FieldRead,
    IntervalCoordinate,
    Owner,
    PathParameter,
    RelationshipStepParameter,
    RowAttributeTime,
    invalid,
)
from marivo._temporal import InstantAnchor


def axis(read: FieldRead) -> AxisBinding:
    return AxisBinding(
        read.entity,
        read.field,
        read.path,
        "field",
        read.at,
        source_at=read.source_at,
        step_ats=read.step_ats,
        path_root=read.path_root,
        path_facts=read.path_facts,
        versioned=read.versioned,
        unit=read.unit,
    )


def axes(values: tuple[ComparisonAxis, ...]) -> tuple[ComparisonAxis, ...]:
    if sum(isinstance(item, (CalendarCoordinate, IntervalCoordinate)) for item in values) > 1:
        invalid("coordinates allow at most one output time axis")
    for index, left in enumerate(values):
        for other, right in enumerate(values[index + 1 :], index + 1):
            if coordinate_matches(left, right):
                invalid(
                    f"Coordinate axes must be distinct: positions {index} and {other} "
                    f"both bind {axis_description(left)}. Remove the repeated coordinate."
                )
    return values


def anchor(value: InstantAnchor | None) -> None:
    if value is not None and (not isinstance(value, InstantAnchor) or value.utcoffset() is None):
        invalid("an identity version selection requires an exact InstantAnchor")


def validate_path_read(
    path: PathParameter,
    terminal_at: AttributeTime | None,
    terminal_row: bool,
    *,
    source_row: bool,
    scalar: bool,
    terminal_required: bool = True,
) -> None:
    """Validate the node reads required by a concrete path consumer."""
    if scalar and any(
        step.relationship.cardinality not in ("one_to_one", "many_to_one") for step in path.steps
    ):
        invalid(
            "Select a directed to-one Path for a scalar read; use image or preimage for multiple targets."
        )
    if not source_row and path.steps[0].relationship.from_versioned and path.source_at is None:
        invalid(
            f"Set source_at=session.instant(...) on mv.path to read versioned node 0 ({path.source.ref.key})."
        )
    for position, step in enumerate(path.steps[:-1], 1):
        if step.relationship.to_versioned and step.target_at is None:
            invalid(
                f"Use relationship.at(session.instant(...)) to read versioned node {position} ({step.relationship.to_entity.ref.key})."
            )
    if not terminal_row and terminal_at is not None:
        invalid(
            f"Remove the terminal anchor at node {len(path.steps)} ({path.target.ref.key}). "
            "The complete foreign key already determines its identity. Select target existence with session.members(..., at=...)."
        )
    if (
        terminal_row
        and terminal_required
        and path.steps[-1].relationship.to_versioned
        and terminal_at is None
    ):
        invalid(
            f"Set an explicit anchor for versioned terminal node {len(path.steps)} ({path.target.ref.key})."
        )


def identity(target: ExactRef | PathParameter) -> AxisBinding:
    if isinstance(target, ExactRef):
        return AxisBinding(target, None, (), "identity")
    relationships = target.relationships
    terminal_at = target.steps[-1].target_at
    validate_path_read(
        target,
        terminal_at,
        not relationships[-1].target_keys_covered,
        source_row=True,
        scalar=False,
    )
    return AxisBinding(
        target.target,
        None,
        tuple(step.ref for step in relationships),
        "identity",
        terminal_at,
        source_at=target.source_at,
        step_ats=target.step_ats,
        path_root=target.source,
        path_facts=relationships,
        versioned=relationships[-1].to_versioned,
    )


def membership(
    reads: tuple[FieldRead, ...],
    paths: tuple[PathParameter, ...],
    unmatched: Literal["exclude", "null"],
) -> MembershipBinding:
    if unmatched not in ("exclude", "null"):
        invalid("membership unmatched must be exclude or null")
    if not reads or len({read.entity.ref for read in reads}) != 1:
        invalid("membership requires nonempty correlated fields from one member Entity")
    if any(read.path for read in reads):
        invalid("membership fields must be direct; put allocation paths in via")
    starts = set()
    for path in paths:
        if path.target.ref != reads[0].entity.ref:
            invalid("membership paths must end at the member Entity")
        if path.source.ref in starts:
            invalid("membership accepts one explicit path per contribution root")
        starts.add(path.source.ref)
        terminal_at = membership_anchor(reads, path)
        validate_path_read(path, terminal_at, True, source_row=True, scalar=False)
    axes(tuple(axis(read) for read in reads))
    return MembershipBinding(reads, paths, unmatched)


def membership_anchor(reads: tuple[FieldRead, ...], path: PathParameter) -> InstantAnchor | None:
    times = {read.at for read in reads}
    target_at = path.steps[-1].target_at
    if target_at is not None:
        if times != {None}:
            invalid(
                "Membership has two terminal time authorities; anchor the Path or the fields once."
            )
        return target_at
    if len(times) != 1:
        invalid("Membership fields require one shared exact member version anchor.")
    value = next(iter(times))
    if isinstance(value, RowAttributeTime):
        invalid("Use a fixed session.instant(...) for membership fields.")
    return value


def owner(definition: PopulationDependency) -> Owner | None:
    return definition.owner if isinstance(definition, PopulationProgram) else None


def combined_owner(definitions: tuple[PopulationDependency, ...]) -> Owner | None:
    owners = {owner(item) for item in definitions} - {None}
    if len(owners) > 1:
        invalid("populations must have one Session owner", kind="ownership_conflict")
    return next(iter(owners), None)


def coordinates(
    values: tuple[ComparisonAxis, ...], keys: tuple[tuple[CoordinateValue, ...], ...]
) -> LiteralPopulation:
    axes(values)
    if any(not isinstance(value, AxisBinding) for value in values):
        invalid("literal coordinates require fields or Entity identity axes")
    return LiteralPopulation(
        tuple(value for value in values if isinstance(value, AxisBinding)), keys, "coordinates-v1"
    )


def project(
    definition: PopulationDependency, values: tuple[ComparisonAxis, ...]
) -> PopulationProgram:
    captured = tuple(
        value
        if _axis_exists(definition.axes, value)
        else _captured_axis(definition.axes, value, None)
        for value in values
    )
    if any(value is None for value in captured):
        missing = tuple(
            axis_description(value)
            for value, match in zip(values, captured, strict=True)
            if match is None
        )
        invalid(
            f"Projection requires complete axes already present in the Population. Missing: {missing!r}."
        )
    selected = axes(tuple(value for value in captured if value is not None))
    positions = tuple(coordinate_position(definition.axes, value) for value in selected)
    return PopulationProgram(
        PopulationProject(definition, positions),
        owner(definition),
        tuple(definition.axes[position] for position in positions),
    )


def set_operation(
    left: PopulationDependency,
    right: PopulationDependency,
    operation: Literal["union", "intersection", "difference"],
) -> PopulationProgram:
    owner_ = combined_owner((left, right))
    missing = tuple(value for value in left.axes if not _axis_exists(right.axes, value))
    extra = tuple(value for value in right.axes if not _axis_exists(left.axes, value))
    if missing or extra:
        invalid(
            f"Set axes differ: missing on right {tuple(map(axis_description, missing))!r}; "
            f"extra on right {tuple(map(axis_description, extra))!r}. "
            "Use project(by=...) to select the same complete bindings on both sides."
        )
    positions = tuple(coordinate_position(right.axes, value) for value in left.axes)
    if positions != tuple(range(len(left.axes))):
        right = PopulationProgram(
            PopulationProject(right, positions),
            owner(right),
            tuple(right.axes[position] for position in positions),
        )
    values = tuple(merge_time_axis(a, b) for a, b in zip(left.axes, right.axes, strict=True))

    def rebind(operand: PopulationDependency) -> PopulationDependency:
        keys = tuple(
            (index, tuple(target.bounds.index(bounds) for bounds in source.bounds))
            for index, (source, target) in enumerate(zip(operand.axes, values, strict=True))
            if isinstance(source, IntervalCoordinate)
            and isinstance(target, IntervalCoordinate)
            and source.bounds != target.bounds
        )
        if not keys:
            return operand
        return PopulationProgram(
            PopulationProject(operand, tuple(range(len(values))), keys), owner(operand), values
        )

    return PopulationProgram(PopulationSet(rebind(left), rebind(right), operation), owner_, values)


def product(factors: tuple[PopulationDependency, ...]) -> PopulationProgram:
    if len(factors) < 2:
        invalid("product requires at least two complete Population factors")
    values = axes(tuple(value for factor in factors for value in factor.axes))
    return PopulationProgram(PopulationProduct(factors), combined_owner(factors), values)


def _axis_exists(values: tuple[ComparisonAxis, ...], value: ComparisonAxis) -> bool:
    return any(coordinate_matches(candidate, value) for candidate in values)


def axis_description(value: ComparisonAxis) -> str:
    if isinstance(value, CalendarCoordinate):
        return f"{value.grain} calendar in {value.timezone}"
    if isinstance(value, IntervalCoordinate):
        return f"interval in {value.timezone}"
    name = (value.field or value.entity).ref.key
    if value.path:
        name += " via " + " -> ".join(step.ref.key for step in value.path)
    if value.source_at is not None or value.step_ats:
        name += f" (source_at={value.source_at}, intermediate_at={value.step_ats})"
    if value.at is not None:
        name += f" at {value.at}"
    if value.unit is not None:
        name += f" [{value.unit}]"
    return name


def select_subject(
    definition: PopulationDependency,
    roots: tuple[ExactRef, ...],
    on: ComparisonAxis | None = None,
) -> int:
    """Locate the single complete existing identity supporting every new read."""
    identities = tuple(
        (index, value)
        for index, value in enumerate(definition.axes)
        if isinstance(value, AxisBinding) and value.role == "identity"
    )
    candidates = tuple(
        (index, value)
        for index, value in identities
        if all(value.entity.ref == root.ref for root in roots)
    )
    choices = tuple(f"axes[{i}] {axis_description(value)}" for i, value in candidates[:6])
    if on is not None:
        for index, value in candidates:
            if coordinate_matches(value, on):
                return index
        invalid(
            f"on={axis_description(on)} must select an existing complete identity that supports "
            f"this operation. Available identity axes: {choices or 'none'}. "
            "Pass the corresponding Population.axes entry."
        )
    if len(candidates) == 1:
        return candidates[0][0]
    if candidates:
        invalid(
            f"Multiple identity subjects support these reads: {choices}. "
            "Pass on=population.axes[index] for the intended role."
        )
    invalid(
        f"No existing complete identity supports roots {tuple(root.ref.key for root in roots)!r}. "
        "Use a Population with the matching identity, or read from separate subjects in separate calls."
    )


def _subject_axis(definition: PopulationDependency, index: int) -> AxisBinding:
    value = definition.axes[index]
    assert isinstance(value, AxisBinding) and value.role == "identity"
    return value


def _read_axis(
    value: AxisBinding, subject: AxisBinding
) -> tuple[AxisBinding, FieldRead | EntityIdentityRead]:
    """Bind reads relative to captured subject keys and normalize their output roles."""
    if isinstance(value.at, RowAttributeTime):
        invalid(
            "Population scalar reads require a fixed time. Pass field.at(session.instant(...))."
        )
    if value.field is None and not value.path:
        invalid(
            "New identities require mv.identity(mv.path(...)); use product for independent identities."
        )
    assert not isinstance(subject.at, RowAttributeTime)
    at = value.at
    source_at = value.source_at
    if not value.path and at is None and subject.path:
        at = subject.at
    effective = _effective_axis(value, subject, at)
    if value.path:
        assert value.path_facts
        if source_at is None and subject.path:
            source_at = subject.at
        path = PathParameter(
            tuple(
                RelationshipStepParameter(
                    step, value.step_ats[index] if index < len(value.step_ats) else None
                )
                for index, step in enumerate(value.path_facts)
            ),
            source_at,
        )
        terminal_row = value.field is not None or not value.path_facts[-1].target_keys_covered
        validate_path_read(path, at, terminal_row, source_row=False, scalar=True)
    elif value.versioned and at is None:
        invalid(
            f"{axis_description(value)} requires an exact version anchor. "
            "Bind it with field.at(session.instant(...))."
        )
    elif not value.versioned and at is not None:
        invalid(f"{axis_description(value)} is unversioned. Remove field.at(...).")
    if value.field is None:
        read: FieldRead | EntityIdentityRead = EntityIdentityRead(
            value.entity,
            value.path,
            at,
            value.path_facts[-1].target_keys,
            source_at=source_at,
            step_ats=value.step_ats,
            path_root=value.path_root,
            path_facts=value.path_facts,
        )
    else:
        read = FieldRead(
            value.field,
            value.entity,
            value.path,
            at,
            source_at=source_at,
            step_ats=value.step_ats,
            path_root=value.path_root,
            path_facts=value.path_facts,
            versioned=value.versioned,
            unit=value.unit,
        )
    return effective, read


def _effective_axis(
    value: AxisBinding,
    subject: AxisBinding,
    at: InstantAnchor | None,
) -> AxisBinding:
    if not subject.path:
        return replace(value, at=at)
    if not value.path:
        if subject.at is not None and value.at is not None:
            invalid(
                f"{axis_description(subject)} and field.at({value.at}) both anchor the same "
                "path node. Keep one explicit anchor."
            )
        return replace(
            value,
            at=at,
            path=subject.path,
            path_root=subject.path_root,
            path_facts=subject.path_facts,
            source_at=subject.source_at,
            step_ats=subject.step_ats,
        )
    if subject.at is not None and value.source_at is not None:
        invalid(
            f"{axis_description(subject)} and the appended path source_at={value.source_at} "
            "both anchor the same path node. Keep one explicit anchor."
        )
    assert not isinstance(subject.at, RowAttributeTime)
    intermediate = (
        *subject.step_ats,
        *((None,) * (len(subject.path) - 1 - len(subject.step_ats))),
        value.source_at or subject.at,
        *value.step_ats,
    )
    last = max(
        (index for index, anchor_ in enumerate(intermediate) if anchor_ is not None), default=-1
    )
    return replace(
        value,
        at=at,
        path=(*subject.path, *value.path),
        path_root=subject.path_root,
        path_facts=(*subject.path_facts, *value.path_facts),
        source_at=subject.source_at,
        step_ats=intermediate[: last + 1],
    )


def extend(
    definition: PopulationDependency,
    values: tuple[ComparisonAxis, ...],
    on: ComparisonAxis | None = None,
    *,
    exact: tuple[bool, ...] = (),
) -> tuple[PopulationProgram, tuple[int, ...]]:
    """Resolve captured values first, then append each distinct scalar read once."""
    explicit_subject = (
        _subject_axis(definition, select_subject(definition, (), on)) if on is not None else None
    )
    requests: list[tuple[ComparisonAxis, bool]] = []
    roots = []
    for index, value in enumerate(values):
        existing = (
            (value if _axis_exists(definition.axes, value) else None)
            if exact and exact[index]
            else _captured_axis(definition.axes, value, explicit_subject)
        )
        if existing is not None:
            requests.append((existing, True))
            continue
        if not isinstance(value, AxisBinding):
            invalid(
                f"{axis_description(value)} is not an existing coordinate; use product for time axes."
            )
        if value.role == "identity" and not value.path:
            invalid(
                "New identities require mv.identity(mv.path(...)); use product for independent identities."
            )
        requests.append((value, False))
        roots.append(value.path_root or value.entity)
    if not roots:
        program = (
            definition
            if isinstance(definition, PopulationProgram)
            else project(definition, definition.axes)
        )
        return program, tuple(coordinate_position(definition.axes, value) for value, _ in requests)
    subject_index = select_subject(definition, tuple(roots), on)
    subject = _subject_axis(definition, subject_index)
    output = definition.axes
    reads: list[FieldRead | EntityIdentityRead] = []
    positions = []
    for value, captured in requests:
        if not captured:
            assert isinstance(value, AxisBinding)
            value, read = _read_axis(value, subject)
            if not _axis_exists(output, value):
                reads.append(read)
                output = (*output, value)
        positions.append(coordinate_position(output, value))
    program = (
        PopulationProgram(
            PopulationClassify(definition, subject_index, tuple(reads)), owner(definition), output
        )
        if reads
        else definition
        if isinstance(definition, PopulationProgram)
        else project(definition, definition.axes)
    )
    return program, tuple(positions)


def _captured_axis(
    values: tuple[ComparisonAxis, ...],
    value: ComparisonAxis,
    subject: AxisBinding | None,
) -> ComparisonAxis | None:
    if not isinstance(value, AxisBinding) or value.field is None or value.path:
        return value if _axis_exists(values, value) else None
    if subject is not None and subject.entity.ref == value.entity.ref:
        assert not isinstance(subject.at, RowAttributeTime)
        at = subject.at if value.at is None and subject.path else value.at
        if not isinstance(at, RowAttributeTime):
            bound = _effective_axis(value, subject, at)
            return bound if _axis_exists(values, bound) else None
    matches = tuple(
        axis
        for axis in values
        if isinstance(axis, AxisBinding)
        and coordinate_matches(
            replace(axis, path=(), path_root=None, source_at=None, step_ats=()), value
        )
    )
    if len(matches) > 1:
        invalid(
            f"{axis_description(value)} matches multiple captured fields "
            f"{tuple(map(axis_description, matches[:6]))}. Specify the field Path or on= identity role."
        )
    return matches[0] if matches else None


def classify(
    definition: PopulationDependency,
    values: tuple[ComparisonAxis, ...],
    on: ComparisonAxis | None = None,
    *,
    exact: tuple[bool, ...] = (),
) -> PopulationProgram:
    return extend(definition, values, on, exact=exact)[0]


def identities(definition: PopulationDependency, on: ComparisonAxis) -> PopulationProgram:
    position = select_subject(definition, (), on)
    subject = _subject_axis(definition, position)
    return PopulationProgram(
        PopulationProject(definition, (position,)),
        owner(definition),
        (AxisBinding(subject.entity, None, (), "identity", versioned=subject.versioned),),
    )


def predicate_fields(
    values: tuple[ComparisonAxis, ...], predicate: PredicateExpression
) -> tuple[tuple[str, int], ...]:
    """Bind predicate reads to existing field coordinates, including qualified roles."""
    from marivo._definition.expressions import RowExpression, RowField
    from marivo._definition.graph import walk
    from marivo._definition.types import field_read_key

    slots: dict[str, int] = {}
    leaves = tuple(item for item in predicate.inputs if isinstance(item, RowExpression))
    for item in walk(leaves, resolve=lambda _: None).order:
        if not isinstance(item, RowField):
            continue
        read = item.read or FieldRead(item.ref, item.root)
        requested = axis(read)
        matches = [i for i, value in enumerate(values) if coordinate_matches(value, requested)]
        if not matches and not read.path:
            matches = [
                i
                for i, value in enumerate(values)
                if isinstance(value, AxisBinding)
                and value.field is not None
                and value.field.ref == read.field.ref
                and value.entity.ref == read.entity.ref
                and value.at == read.at
            ]
        if len(matches) != 1:
            received = tuple(values[i] for i in matches) if matches else values
            invalid(
                f"Population.where requires one existing coordinate for {read.field.ref.key}; "
                f"received {received!r}. "
                + (
                    "Qualify the field with its existing relationship path."
                    if matches
                    else "Use classify to add the field before filtering its captured value."
                )
            )
        key = field_read_key(read) if item.read is not None else item.ref.ref.path
        slots[key] = matches[0]
    return tuple(slots.items())


def where(definition: PopulationDependency, predicate: PredicateExpression) -> PopulationProgram:
    predicate_fields(definition.axes, predicate)
    dependencies = condition_populations(predicate)
    for dependency in dependencies:
        if any(
            not any(coordinate_matches(value, original) for original in definition.axes)
            for value in dependency.axes
        ):
            invalid("predicate domains must project onto a subset of receiver axes")
    return PopulationProgram(
        PopulationFilter(definition, predicate),
        combined_owner((definition, *dependencies)),
        definition.axes,
    )


def identity_path(
    definition: PopulationDependency, path: PathParameter, on: ComparisonAxis | None
) -> int:
    validate_path_read(
        path,
        path.steps[-1].target_at,
        not path.steps[-1].relationship.target_keys_covered,
        source_row=False,
        scalar=False,
    )
    return select_subject(definition, (path.source,), on)


def image(
    definition: PopulationDependency,
    path: PathParameter,
    on: ComparisonAxis | None = None,
) -> PopulationProgram:
    position = identity_path(definition, path, on)
    return PopulationProgram(
        PopulationImage(definition, path, position),
        owner(definition),
        (identity(path.target),),
    )


def preimage(
    definition: PopulationDependency,
    targets: PopulationDependency,
    path: PathParameter,
    on: ComparisonAxis | None = None,
) -> PopulationProgram:
    position = identity_path(definition, path, on)
    if len(targets.axes) != 1 or not coordinate_matches(targets.axes[0], identity(path.target)):
        invalid("preimage requires the path target identity axis")
    return PopulationProgram(
        PopulationPreimage(definition, targets, path, position),
        combined_owner((definition, targets)),
        definition.axes,
    )
