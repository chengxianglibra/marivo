"""Tests for semantic readiness reports."""

from __future__ import annotations

import json
import textwrap

from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_READY_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource=md.ref("datasource.warehouse"),
        source=ms.table("orders"),
        primary_key=["order_id"],
        ai_context=ms.ai_context(business_definition="One row per paid order."),
    )

    @ms.dimension(
        entity=orders,
        ai_context=ms.ai_context(business_definition="Gross order amount in USD."),
    )
    def amount(table):
        return table.amount

    @ms.time_dimension(
        entity=orders,
        granularity="day",
        parse=ms.timestamp(timezone="UTC"),
        ai_context=ms.ai_context(business_definition="Timestamp when the order was created."),
    )
    def created_at(table):
        return table.created_at

    @ms.metric(
        entities=[orders],
        additivity="additive",
        ai_context=ms.ai_context(business_definition="Sum of order amount."),
    )
    def total_amount(table):
        return table.amount.sum()
""")


def test_readiness_report_to_dict_is_json_safe() -> None:
    report = ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(
            ReadinessIssue(
                kind="fragile_string_ref",
                severity="warning",
                refs=("sales.orders",),
                message="string ref used",
                suggested_action="Use stable object refs.",
            ),
        ),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
        ),
        checked_at="2026-05-29T00:00:00Z",
    )

    payload = report.to_dict()

    assert payload["status"] == "ready_with_warnings"
    assert payload["warnings"][0]["kind"] == "fragile_string_ref"
    assert payload["input_summary"]["tables"] == ["sales.orders"]
    assert json.loads(json.dumps(payload))["analysis_ready_refs"] == ["sales.total_amount"]
    assert "preview_summary" not in payload
    assert "parity_summary" not in payload
    assert "richness_summary" not in payload


def test_readiness_report_target_fields_are_json_safe() -> None:
    report = ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=("sales.total_amount",),
        blockers=(),
        warnings=(
            ReadinessIssue(
                kind="fragile_string_ref",
                severity="warning",
                refs=("sales.orders",),
                message="string ref used",
                suggested_action="Use stable object refs.",
            ),
        ),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.total_amount",),
            tables=("sales.orders",),
        ),
        checked_at="2026-05-29T00:00:00Z",
    )

    payload = report.to_dict()

    assert payload["input_summary"]["refs"] == ["sales.total_amount"]
    assert json.loads(json.dumps(payload))["analysis_ready_refs"] == ["sales.total_amount"]


def test_project_readiness_accepts_refs_argument(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    report = project.readiness(refs=("sales.orders",))

    assert report.input_summary.refs == ("sales.orders",)


def test_project_readiness_accepts_semantic_ref_objects(
    semantic_project_factory,
) -> None:
    """readiness() must accept SemanticRef objects from catalog.list("metric").refs()."""
    from marivo.semantic.catalog import SemanticKind
    from marivo.semantic.refs import make_ref

    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    refs = (make_ref("sales.orders", SemanticKind.ENTITY),)
    report = project.readiness(refs=refs)

    assert report.input_summary.refs == ("sales.orders",)
    assert "unknown_ref" not in _issue_kinds(report.blockers)


def test_readiness_expands_relationship_join_key_dependencies(
    semantic_project_factory,
) -> None:
    project = _project(
        semantic_project_factory,
        textwrap.dedent("""\
            import marivo.datasource as md
            import marivo.semantic as ms

            orders = ms.entity(
                name="orders",
                datasource=md.ref("datasource.warehouse"),
                source=ms.table("orders"),
                primary_key=["order_id"],
                ai_context=ms.ai_context(business_definition="One row per paid order."),
            )
            customers = ms.entity(
                name="customers",
                datasource=md.ref("datasource.warehouse"),
                source=ms.table("customers"),
                primary_key=["customer_id"],
                ai_context=ms.ai_context(business_definition="One row per customer."),
            )

            @ms.dimension(
                entity=orders,
                ai_context=ms.ai_context(business_definition="Customer linked to the order."),
            )
            def customer_id(table):
                return table.customer_id

            @ms.dimension(
                entity=customers,
                name="id",
                ai_context=ms.ai_context(business_definition="Stable customer identifier."),
            )
            def customer_pk(table):
                return table.customer_id

            ms.relationship(
                name="orders_to_customers",
                from_entity=orders,
                to_entity=customers,
                keys=[ms.join_on(customer_id, customer_pk)],
                ai_context=ms.ai_context(
                    business_definition="Orders join to customers through customer id."
                ),
            )
        """),
    )

    report = project.readiness(refs=("sales.orders_to_customers",))

    assert report.input_summary.refs == (
        "sales.orders_to_customers",
        "sales.orders",
        "sales.customers",
        "sales.orders.customer_id",
        "sales.customers.id",
    )
    assert "unknown_ref" not in _issue_kinds(report.blockers)


def test_readiness_blocks_unknown_requested_ref(semantic_project_factory) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    report = project.readiness(refs=("sales.missing_metric",))

    assert report.status == "blocked"
    assert report.analysis_ready_refs == ()
    assert "unknown_ref" in _issue_kinds(report.blockers)
    assert report.blockers[0].refs == ("sales.missing_metric",)


def test_readiness_accepts_domain_ref(semantic_project_factory) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    report = project.readiness(refs=("sales",))

    assert "unknown_ref" not in _issue_kinds(report.blockers)
    assert "sales" in report.input_summary.refs


def test_readiness_no_dtype_advisory_when_parse_deferred(semantic_project_factory) -> None:
    """When parse is omitted (deferred), dtype advisory is skipped at readiness time."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms

                ms.domain(name="sales", owner='Mina Zhang')

                orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))

                @ms.time_dimension(entity=orders, granularity="day")
                def order_date(table):
                    return table.dt.cast("date")
            """)
        }
    )

    report = project.readiness()

    # With deferred parse, the dtype advisory is not emitted at readiness time;
    # dtype mismatch is caught at analysis time instead.
    assert not any("dtype" in issue.kind for issue in report.warnings)


def test_readiness_warns_for_missing_business_definition(
    semantic_project_factory,
):
    project = _project(semantic_project_factory, _COMMENTLESS_DOMAIN_PY)

    report = project.readiness()
    assert report.status == "blocked"
    assert "missing_business_definition" in _issue_kinds(report.blockers)


def test_readiness_strict_enrichment_is_ready_when_only_guardrails_missing(
    semantic_project_factory,
):
    project = semantic_project_factory(
        {
            "sales/_domain.py": textwrap.dedent("""\
                import marivo.datasource as md
                import marivo.semantic as ms

                ms.domain(name="sales", owner='Mina Zhang')

                orders = ms.entity(
                    name="orders",
                    datasource=md.ref("datasource.warehouse"),
                    source=ms.table("orders"),
                    ai_context=ms.ai_context(business_definition="One row per paid order."),
                )

                @ms.dimension(
                    entity=orders,
                    ai_context=ms.ai_context(business_definition="Gross order amount in USD."),
                )
                def amount(table):
                    return table.amount
            """),
        }
    )

    report = project.readiness(refs=("sales.orders.amount",))

    assert report.status == "ready"
    assert "missing_business_definition" not in _issue_kinds(report.blockers)
    assert "missing_guardrails" not in _issue_kinds(report.warnings)


_COMMENTLESS_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), primary_key=["order_id"], source=ms.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.metric(
        entities=[orders],
        additivity='additive',
    )
    def total_amount(table):
        return table.amount.sum()
""")


def _project(semantic_project_factory, model_py: str):
    return semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": model_py,
        }
    )


def _issue_kinds(issues):
    return {issue.kind for issue in issues}


def test_readiness_sql_parity_unverified_warning(semantic_project_factory) -> None:
    """Metric with SQL provenance should get a warning, not a blocker."""
    domain_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"), ai_context=ms.ai_context(business_definition="One row per order."))

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM orders", dialect="duckdb"),
            ai_context=ms.ai_context(business_definition="Sum of amount."),
        )
        def total_amount(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": domain_py,
        }
    )

    report = project.readiness(refs=("sales.total_amount",))
    assert "requires_raw_sql" not in _issue_kinds(report.blockers)
    assert "sql_parity_unverified" in _issue_kinds(report.warnings)


def test_readiness_cross_datasource_unfederated(semantic_project_factory) -> None:
    domain_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse_a"), source=ms.table("orders"), ai_context=ms.ai_context(business_definition="Orders A."))
        items = ms.entity(name="items", datasource=md.ref("datasource.warehouse_b"), source=ms.table("items"), ai_context=ms.ai_context(business_definition="Items B."))

        @ms.metric(
            entities=[orders, items],
            root_entity=orders,
            additivity="additive",
            ai_context=ms.ai_context(business_definition="Cross-datasource metric."),
        )
        def cross_metric(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": domain_py,
        }
    )

    report = project.readiness(refs=("sales.cross_metric",))
    assert "cross_datasource_unfederated" in _issue_kinds(report.blockers)


def test_readiness_no_backend_access_required(semantic_project_factory) -> None:
    """Readiness is a pure in-memory check — no datasource connection needed."""
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    # No _patch_connection_service, no backend setup — readiness should still work.
    report = project.readiness()

    assert report.status in {"ready", "ready_with_warnings", "blocked"}


def test_readiness_does_not_require_internal_audit_decisions(semantic_project_factory):
    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n",
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'),\n"
                "    ai_context=ms.ai_context(business_definition='One row per order.'))\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue', \n"
                "    ai_context=ms.ai_context(business_definition='Sum of amount.'))\n"
                "def revenue(orders):\n    return orders.amount.sum()\n"
            ),
        }
    )

    report = project.readiness()
    assert report.status == "ready"
    assert report.blockers == ()


# -- enrichment predicates ---------------------------------------------------


def test_missing_business_definition_predicate():
    from types import SimpleNamespace

    from marivo.datasource.ir import AiContextIR
    from marivo.semantic.readiness import _missing_business_definition

    assert _missing_business_definition(SimpleNamespace(ai_context=AiContextIR()))
    assert _missing_business_definition(
        SimpleNamespace(ai_context=AiContextIR(business_definition="   "))
    )
    assert not _missing_business_definition(
        SimpleNamespace(ai_context=AiContextIR(business_definition="One row per order."))
    )
    # description alone does NOT satisfy the strict floor.
    assert _missing_business_definition(SimpleNamespace(ai_context=AiContextIR()))


def test_strict_enrichment_issues_flags_bare_ref(semantic_project_factory):
    from marivo.semantic.readiness import _object_maps, _strict_enrichment_issues

    project = semantic_project_factory(
        {
            "sales/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n",
            "sales/objects.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'),\n"
                "    ai_context=ms.ai_context(business_definition='One row per order.',\n"
                "               guardrails=['Exclude test orders.']))\n"
                "@ms.dimension(entity=orders, name='amount',\n"
                "    ai_context=ms.ai_context(business_definition='Gross amount.',\n"
                "               guardrails=['USD only.']))\n"
                "def amount(table):\n    return table.amount\n"
                "@ms.dimension(entity=orders, name='region')\n"
                "def region(table):\n    return table.region\n"
            ),
        }
    )

    kinds, objects = _object_maps(project)
    blockers, warnings = _strict_enrichment_issues(tuple(kinds), kinds, objects)

    blocker_refs = {ref for issue in blockers for ref in issue.refs}

    # The bare field is flagged; the fully enriched dataset and field are not.
    assert "sales.orders.region" in blocker_refs
    assert "sales.orders" not in blocker_refs
    assert "sales.orders.amount" not in blocker_refs
    assert warnings == []
    assert all(issue.kind == "missing_business_definition" for issue in blockers)
    assert all(issue.severity == "blocker" for issue in blockers)


# -- issue kind validation ---------------------------------------------------


def test_strict_enrichment_issue_kinds_are_valid():
    from typing import get_args

    from marivo.semantic.readiness import ReadinessIssueKind

    kinds = get_args(ReadinessIssueKind)
    assert "missing_business_definition" in kinds
    assert "missing_guardrails" not in kinds
    assert "unresolved" + "_clarification" not in kinds


# -- check CLI ---------------------------------------------------------------


def test_semantic_check_run_check_returns_json_ready_report(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)
    workspace_dir = project.workspace_dir

    from marivo.semantic.check import run_check

    payload = run_check(
        workspace_dir=workspace_dir,
        readiness=True,
        format="json",
    )

    # Structural readiness: python_native metric with all definitions
    assert payload["readiness"]["status"] in {"ready", "ready_with_warnings", "blocked"}
    assert "status" in payload["readiness"]


def test_semantic_check_main_prints_json(
    semantic_project_factory,
    capsys,
) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)

    import marivo.semantic.check as semantic_check

    exit_code = semantic_check.main(
        [
            "--workspace-dir",
            str(project.workspace_dir),
            "--format",
            "json",
            "--readiness",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "readiness" in payload
    assert payload["readiness"]["status"] in {"ready", "ready_with_warnings", "blocked"}


# -- naive timezone blockers ---------------------------------------------------


def _naive_tz_report(
    semantic_project_factory,
    time_dim_kwargs: str,
    *,
    time_dim_prelude: str = "",
) -> object:
    """Build a readiness report with a single time dimension using the given kwargs."""
    domain_py = textwrap.dedent(f"""\
        import marivo.datasource as md
        import marivo.semantic as ms

        ms.domain(name="sales", owner='Mina Zhang')

        orders = ms.entity(
            name="orders",
            datasource=md.ref("datasource.warehouse"),
            source=ms.table("orders"),
            ai_context=ms.ai_context(business_definition="One row per order."),
        )

        {time_dim_prelude}

        @ms.time_dimension(
            entity=orders,
            {time_dim_kwargs}
        )
        def created_at(table):
            return table.created_at
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": domain_py,
        }
    )
    return project.readiness()


def test_missing_datetime_timezone_does_not_block_readiness(semantic_project_factory) -> None:
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="hour", parse=ms.datetime(), '
        'ai_context=ms.ai_context(business_definition="When the order was created.")',
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


def test_missing_timestamp_timezone_does_not_block_readiness(semantic_project_factory) -> None:
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="hour", parse=ms.timestamp(), '
        'ai_context=ms.ai_context(business_definition="When the order was updated.")',
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


def test_declared_timezone_clears_blocker(semantic_project_factory) -> None:
    """time_dimension with timezone='UTC' does NOT trigger naive_timezone_undetermined."""
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="day", parse=ms.datetime(timezone="UTC"), '
        'ai_context=ms.ai_context(business_definition="When the order was created.")',
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


def test_date_data_type_does_not_block(semantic_project_factory) -> None:
    """ms.date() has no timezone ambiguity; should not trigger blocker."""
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="day", ai_context=ms.ai_context(business_definition="Date of the order.")',
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


def test_day_only_string_format_does_not_block(semantic_project_factory) -> None:
    """string data_type with day-only date_format (e.g. %Y%m%d) has no TZ ambiguity."""
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="day", parse=ms.strptime("%Y%m%d"), '
        'ai_context=ms.ai_context(business_definition="Day partition key.")',
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


def test_time_bearing_string_format_without_timezone_does_not_block(
    semantic_project_factory,
) -> None:
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="hour", parse=ms.strptime("%Y-%m-%d %H:%M:%S"), '
        'ai_context=ms.ai_context(business_definition="Timestamp as string.")',
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


def test_time_bearing_integer_format_without_timezone_does_not_block(
    semantic_project_factory,
) -> None:
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="hour", parse=ms.strptime("%Y%m%d%H%M%S"), '
        'ai_context=ms.ai_context(business_definition="Timestamp as integer.")',
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


def test_required_prefix_does_not_block(semantic_project_factory) -> None:
    """Hour-only dimensions (hour_prefix) are partition encodings, not TZ-relevant."""
    report = _naive_tz_report(
        semantic_project_factory,
        'granularity="hour", parse=ms.hour_prefix(order_date), '
        'ai_context=ms.ai_context(business_definition="Hour partition key.")',
        time_dim_prelude=textwrap.dedent("""\
            @ms.time_dimension(
                entity=orders,
                granularity="day",
                parse=ms.strptime("%Y%m%d"),
                ai_context=ms.ai_context(business_definition="Day partition key."),
            )
            def order_date(table):
                return table.dt
        """),
    )
    assert "naive_timezone_undetermined" not in _issue_kinds(report.blockers)


# -- is_time_bearing_format unit tests -----------------------------------------


def test_is_time_bearing_format_day_only() -> None:
    from marivo.semantic.ir import is_time_bearing_format

    assert not is_time_bearing_format(None)
    assert not is_time_bearing_format("%Y%m%d")
    assert not is_time_bearing_format("%Y-%m-%d")


def test_is_time_bearing_format_hour_only_no_date() -> None:
    from marivo.semantic.ir import is_time_bearing_format

    assert not is_time_bearing_format("%H")
    assert not is_time_bearing_format("%H%M")


def test_is_time_bearing_format_time_bearing() -> None:
    from marivo.semantic.ir import is_time_bearing_format

    assert is_time_bearing_format("%Y-%m-%d %H:%M:%S")
    assert is_time_bearing_format("%Y%m%d%H%M%S")
    assert is_time_bearing_format("%Y-%m-%d %H")
    assert is_time_bearing_format("%Y%m%d%H")


# -- column helper readiness parity -------------------------------------------


_COLUMN_HELPER_PROJECT_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))
    amount = ms.measure_column(
        name="amount",
        entity=orders,
        column="amount",
        additivity="additive",
        unit="USD",
    )
    region = ms.dimension_column(name="region", entity=orders, column="region")
    created_at = ms.time_dimension_column(
        name="created_at",
        entity=orders,
        column="created_at",
        granularity="day",
        parse=ms.timestamp(timezone="UTC"),
        is_default=True,
    )
    total_amount = ms.aggregate(name="total_amount", measure=amount, agg="sum")
""")


def test_column_helper_objects_participate_in_readiness(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/columns.py": _COLUMN_HELPER_PROJECT_PY,
        }
    )
    report = project.readiness(refs=("sales.orders.amount", "sales.total_amount"))
    # Column helper refs are recognized (no unknown_ref blockers).
    blocker_kinds = {b.kind for b in report.blockers}
    assert "unknown_ref" not in blocker_kinds


# -- fix hints and ready_with_warnings handoff -------------------------------


def test_readiness_render_surfaces_suggested_action_fix_hints(
    semantic_project_factory,
) -> None:
    """render() must surface suggested_action as a per-issue fix hint."""
    report = _project(semantic_project_factory, _COMMENTLESS_DOMAIN_PY).readiness()
    assert report.blockers  # missing_business_definition blockers present

    text = report.render()
    # the listing format appends "-> fix: <suggested_action>" per issue
    assert "-> fix:" in text
    assert any(issue.suggested_action in text for issue in report.blockers + report.warnings)


def test_readiness_ready_with_warnings_renders_handoff_decision(
    semantic_project_factory,
) -> None:
    """A ready_with_warnings report must render an explicit handoff decision."""
    domain_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"), ai_context=ms.ai_context(business_definition="One row per order."))

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM orders", dialect="duckdb"),
            ai_context=ms.ai_context(business_definition="Sum of amount."),
        )
        def total_amount(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": domain_py,
        }
    )

    report = project.readiness(refs=("sales.total_amount",))
    assert report.status == "ready_with_warnings"
    assert report.warnings  # sql_parity_unverified warning present

    text = report.render()
    assert "handoff" in text.lower()
    assert "ready_with_warnings" in text


def test_missing_business_definition_suggested_action_mentions_ai_context(
    semantic_project_factory,
) -> None:
    report = _project(semantic_project_factory, _COMMENTLESS_DOMAIN_PY).readiness()
    issues = [i for i in report.blockers if i.kind == "missing_business_definition"]
    assert issues

    for issue in issues:
        assert "ai_context=ms.ai_context(business_definition=...)" in issue.suggested_action


def test_unknown_ref_suggested_action_mentions_catalog_browse(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory, _READY_DOMAIN_PY)
    report = project.readiness(refs=("sales.missing_metric",))

    issues = [i for i in report.blockers if i.kind == "unknown_ref"]
    assert issues

    for issue in issues:
        assert (
            "catalog.list(...).show()" in issue.suggested_action
            or "catalog.get(...).details().show()" in issue.suggested_action
        )


def test_sql_parity_unverified_suggested_action_mentions_parity_check_and_non_blocking(
    semantic_project_factory,
) -> None:
    domain_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"), ai_context=ms.ai_context(business_definition="One row per order."))

        @ms.metric(
            entities=[orders],
            additivity="additive",
            provenance=ms.from_sql(sql="SELECT SUM(amount) AS total_amount FROM orders", dialect="duckdb"),
            ai_context=ms.ai_context(business_definition="Sum of amount."),
        )
        def total_amount(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": domain_py,
        }
    )

    report = project.readiness(refs=("sales.total_amount",))
    issues = [i for i in report.warnings if i.kind == "sql_parity_unverified"]
    assert issues

    for issue in issues:
        assert "ms.parity_check(" in issue.suggested_action
        assert "non-blocking" in issue.suggested_action
