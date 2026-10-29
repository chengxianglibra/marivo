"""Semantic-owned parameter meaning and acquisition for authoring Help."""

from marivo._authoring.model import AuthoringInputRequirement
from marivo.introspection.live.model import LiveHelpTarget


def _links(*targets: str) -> tuple[LiveHelpTarget, ...]:
    return tuple(
        LiveHelpTarget(surface="datasource", canonical_id=target.removeprefix("datasource."))
        if target.startswith("datasource.")
        else LiveHelpTarget(surface="semantic", canonical_id=target)
        for target in targets
    )


# Identities here use the native registry's pre-publication names. Its naming
# pass canonicalizes these edges together with every other semantic route.
_FAMILIES: dict[str, tuple[str, tuple[LiveHelpTarget, ...]]] = {
    "DatasourceSymbol": (
        "Use a declared datasource's .ref or an exact datasource lookup.",
        _links("ref.datasource", "datasource.connections"),
    ),
    "EntitySymbol": (
        "Reuse a constructed Entity symbol, a closed Catalog Entity, or an exact declaration lookup.",
        _links("objects.entity", "ref.entity", "SemanticCatalog.get"),
    ),
    "EntitySourceIR": (
        "Pass a structured table/file source; a table-name string is not a source descriptor.",
        _links("datasource.sources"),
    ),
    "ColumnName": (
        "Use an exact column in the Entity output relation; inspect physical names before authoring its mapping.",
        _links("datasource.inspect"),
    ),
    "DomainSymbol": (
        "Omit to use model collection's default Domain; otherwise pass a constructed Domain or exact lookup.",
        _links("domain", "ref.domain"),
    ),
    "AiContextValue": (
        "Omit for no additional context; supply attributable business meaning and guardrails when needed.",
        _links("ai_context"),
    ),
    "Additivity": (
        "Choose permitted aggregation axes from business authority; naming or numeric types grant no permission.",
        _links("objects.measure"),
    ),
    "ParseVariant": (
        "Omit for supported native time values; choose an explicit parser for other encodings.",
        _links("time.parsing"),
    ),
    "TimeDimensionSymbol": (
        "Reuse a time field or exact TimeDimension lookup with the required Entity ownership.",
        _links("objects.time_dimension", "ref.time_dimension"),
    ),
    "DimensionSymbol": (
        "Reuse a governed Dimension or exact lookup.",
        _links("objects.dimension", "ref.dimension"),
    ),
    "RelationshipEndpoint": (
        "Pass the directed endpoint Entity; use its governed stable identity.",
        _links("objects.entity", "ref.entity"),
    ),
    "JoinKey": ("Pair the complete source and target keys in order.", _links("join_on")),
    "SemanticCatalog": (
        "Acquire the current project snapshot before lookup or validation.",
        _links("load"),
    ),
    "CatalogEntry | LookupSymbol": (
        "Select exact identities in the loaded scope; the consuming check owns its accepted kinds.",
        _links("SemanticCatalog.get", "ref"),
    ),
    "SourceCheck": (
        "Omit for connectivity/schema checks; declared data checks require an explicit bounded scope.",
        _links("source_check"),
    ),
    "AuthoringScope": (
        "Choose explicit row and timeout guards and the applicable pruning boundary.",
        _links("datasource.inspection"),
    ),
    "Deferred": (
        "Compose native governed inputs in their semantic role; declaration lookups need loader binding.",
        _links("expressions"),
    ),
    "DimensionSymbol | TimeDimensionSymbol": (
        "Select native coordinates from the same contribution root.",
        _links("objects.dimension", "objects.time_dimension"),
    ),
}

# Supporting facts are attached to exact parameters, including those previously
# present only in the reflected signature. Literal choices have no fake producer.
_PARAMETERS: dict[str, tuple[str, str, tuple[LiveHelpTarget, ...]]] = {
    "domain": ("DomainSymbol", *_FAMILIES["DomainSymbol"]),
    "ai_context": ("AiContextValue", *_FAMILIES["AiContextValue"]),
    "unit": (
        "Text",
        "Omit to leave a physical leaf's unit unspecified or inherit an expression's known unit; new unit assertions require business authority.",
        (),
    ),
    "primary_key": (
        "Primary_key",
        "Omit for no declared stable key; otherwise use unique Entity output column names, excluding version coordinates. Naive timestamp keys retain exact wall-clock values without a timezone; parser and Session timezones do not change identity.",
        _links("objects.entity"),
    ),
    "versioning": (
        "ValiditySpec",
        "Omit for an unversioned Entity; choose snapshot or validity history explicitly.",
        _links("snapshot", "validity"),
    ),
    "is_default": (
        "bool",
        "False leaves the Entity's default business time unchanged; True declares this field as that default.",
        (),
    ),
    "default": (
        "bool",
        "True makes this Domain the file's default; False requires explicit domain selection for its declarations.",
        (),
    ),
    "status_time_dimension": (
        "TimeDimensionSymbol",
        "Omit for no status-time assertion; otherwise select the Entity's status axis and keep it fixed in additivity.",
        _links("objects.time_dimension", "ref.time_dimension"),
    ),
    "sample_interval": (
        "tuple[int, minute | hour]",
        "Optional positive minute/hour source cadence. The encoding must represent the interval; missing clocks are allowed.",
        (),
    ),
    "boundary_timezone": (
        "Text",
        "Use the authoritative timezone for calendar or occurrence boundaries.",
        (),
    ),
    "coverage": (
        "DateCoverage",
        "Pass the finite (start_date, end_date) civil-date coverage required by this temporal authority.",
        (),
    ),
    "workspace_dir": (
        "Text",
        "Exact workspace root; None resolves MARIVO_PROJECT_ROOT, nearest ancestor manifest, then cwd. Empty projects are valid.",
        (),
    ),
    "domains": (
        "Text",
        "None loads all domains; an explicit name or sequence must include every required dependency domain.",
        (),
    ),
    "timezone": (
        "Text",
        "Use the authoritative IANA business timezone; snapshot requires it. Validity DATE bounds require it and timestamp bounds forbid it.",
        (),
    ),
    "interval": (
        "Text",
        "Choose closed_open ([start, end)) or closed_closed ([start, end]) explicitly.",
        (),
    ),
    "open_end": (
        "SourceScalarSequence",
        "Required nonempty tuple of valid_to sentinels; None represents SQL NULL.",
        (),
    ),
    "business_definition": (
        "Text",
        "None omits the narrative; otherwise state attributable reusable business meaning.",
        (),
    ),
    "guardrails": (
        "SourceScalarSequence",
        "None omits guardrails; otherwise state the definition's interpretation limits.",
        (),
    ),
    "aliases": (
        "SourceScalarSequence",
        "None omits aliases; otherwise provide explicit search phrases, not additional semantic identities.",
        (),
    ),
}

_EXTRAS: dict[str, tuple[str, ...]] = {
    "load": ("workspace_dir", "domains"),
    "snapshot": ("timezone",),
    "validity": ("interval", "open_end", "timezone"),
    "domain": ("default", "ai_context"),
    "entity": ("primary_key", "versioning", "domain", "ai_context"),
    "dimension": ("domain", "ai_context"),
    "dimension_column": ("domain", "ai_context"),
    "time_dimension": ("is_default", "domain", "ai_context"),
    "time_dimension_column": ("is_default", "domain", "ai_context"),
    "measure_column": ("unit", "domain", "ai_context"),
    "relationship": ("domain", "ai_context"),
    "period_calendar": ("boundary_timezone", "coverage", "domain", "ai_context"),
    "temporal_set": ("boundary_timezone", "coverage", "domain", "ai_context"),
    "ai_context": ("business_definition", "guardrails", "aliases"),
}


def authoring_inputs(
    canonical_id: str, inputs: tuple[AuthoringInputRequirement, ...]
) -> tuple[AuthoringInputRequirement, ...]:
    """Attach explicit native facts, without guessing from Python annotations."""
    enriched: list[AuthoringInputRequirement] = []
    for requirement in inputs:
        fact = _FAMILIES.get(requirement.family)
        if len(requirement.parameter_names) == 1:
            parameter = _PARAMETERS.get(requirement.parameter_names[0])
            if parameter is not None:
                fact = parameter[1:]
            if requirement.parameter_names == ("unit",) and requirement.min_count:
                fact = (
                    "Declare the explicit target unit; the author owns conversion correctness.",
                    (),
                )
        if canonical_id == "join_on":
            fact = (
                "Use native source and target endpoint fields, each owned by its respective Entity.",
                _links("objects.dimension", "objects.time_dimension"),
            )
        if canonical_id == "readiness" and requirement.role == "subject":
            fact = (
                "Select current entries, values or exact lookup symbols, including declared Measures and Metrics. Their physical dependencies are checked automatically. None checks all loaded physical objects; analysis_ready_inputs contains only directly requested ready physical refs.",
                _links("SemanticCatalog.get", "ref"),
            )
        if canonical_id == "validate" and requirement.role == "scope":
            fact = (
                "Required with data checks; omit when checks is empty. Pass one scope for one checked Entity, or exact (Entity symbol, scope) pairs for multiple checked Entities.",
                _links("datasource.unpruned", "datasource.time_range", "datasource.partition"),
            )
        if canonical_id.startswith("source_check."):
            if requirement.role == "subject":
                fact = (
                    "Pass native lookup symbols from ms.ref or a Catalog entry's .ref; closed .value expressions are not accepted.",
                    _links("ref", "SemanticCatalog.get"),
                )
            elif requirement.parameter_names == ("side",):
                fact = (
                    "'from' checks source keys for target matches; 'both' also checks target keys for source matches. Both use only the selected bounded scopes.",
                    (),
                )
            elif requirement.parameter_names == ("expected",):
                fact = (
                    "Choose 'one_to_one', 'many_to_one', 'one_to_many' or 'many_to_many' in the declared from_entity -> to_entity direction.",
                    (),
                )
            elif requirement.parameter_names == ("values",):
                fact = ("Pass a nonempty sequence of unique str, int, float or bool values.", ())
            elif requirement.parameter_names == ("max_age",):
                fact = ("Pass a strictly positive datetime.timedelta.", ())
        if fact is not None:
            requirement = requirement.model_copy(
                update={"description": fact[0], "acquisition": fact[1]}
            )
        enriched.append(requirement)
    for name in _EXTRAS.get(canonical_id, ()):
        family, description, acquisition = _PARAMETERS[name]
        enriched.append(
            AuthoringInputRequirement(
                role="dependency",
                family=family,
                parameter_names=(name,),
                min_count=1
                if name in {"boundary_timezone", "coverage", "interval", "open_end"}
                or (canonical_id == "snapshot" and name == "timezone")
                else 0,
                description=description,
                acquisition=acquisition,
            )
        )
    return tuple(enriched)


_EXAMPLE_INPUTS = {
    "domain": "accountable_owner: an established human owner",
    "entity": "a registered warehouse datasource",
    "dimension": "orders: the constructed Entity",
    "dimension_column": "orders: the constructed Entity",
    "time_dimension": "orders: the constructed Entity",
    "time_dimension_column": "orders: the constructed Entity",
    "measure": "orders: the constructed Entity",
    "measure_column": "orders: the constructed Entity",
    "metric": "orders: the constructed Entity",
    "relationship": "orders, customers: Entities; order_customer_id, customer_id: their governed key fields",
}


def authoring_guidance(canonical_id: str) -> tuple[str, ...]:
    """Own project startup and local-versus-published declaration facts."""
    if canonical_id == "load":
        return (
            "Project sources (init is optional; empty projects are valid):",
            "  models/datasources/<name>.py: connection declarations",
            "  models/semantic/<domain>/_domain.py: Domain declaration",
            "  models/semantic/<domain>/<module>.py: coherent model slice",
            "Built-in default is an in-memory DuckDB datasource, including in an empty project.",
        )
    example_inputs = (
        ("Example inputs: " + _EXAMPLE_INPUTS[canonical_id],)
        if canonical_id in _EXAMPLE_INPUTS
        else ()
    )
    if canonical_id in {"measure", "metric"}:
        identity = (
            "measure:<domain>.<entity>.<function_name>"
            if canonical_id == "measure"
            else "metric:<domain>.<function_name>"
        )
        return (
            *example_inputs,
            "Local use: naming an expression does not publish a Catalog definition.",
            "Project publication: save the declaration at module top level in",
            "  models/semantic/<domain>/<module>.py; ms.load() collects declarations",
            "  defined in that model, binds their dependencies and publishes the Catalog.",
            f"Retrieve after loading: catalog.get('{identity}')",
            'Project Help: marivo.help("semantic.load")',
        )
    return example_inputs
