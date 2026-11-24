"""Require a tracked formal witness for every executable Analysis rewrite rule."""

from __future__ import annotations

import ast
import importlib
import inspect
import symtable
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from marivo.analysis.model.optimization_rules import (
    OPTIMIZATION_MILESTONE_RULES,
    OPTIMIZATION_RULE_IDS,
    OptimizationRuleId,
)

_ROOT = Path(__file__).resolve().parents[2]
_FORMAL = Path(__file__).resolve().parent
_TERMINAL_MODULE = "materialization/frame_terminal_fusion.py"

# The original ten channels include rules used without emitting a plan
# decision. Milestones O1–O9 use the production milestone directory.
_ORIGINAL_CHANNELS: Final[tuple[tuple[OptimizationRuleId, ...], ...]] = (
    ("L1",),
    ("required_slots",),
    ("source_merge",),
    ("source_sample_reduction",),
    (
        "source_key_fusion",
        "source_parameter_block",
        "source_population_check_fusion",
        "source_member_count",
        "dead_population_transport",
        "source_output_population_coverage",
        "source_shared_key_fusion",
    ),
    ("source_terminal_fusion", "native_check_summary"),
    ("pending_frame_top",),
    ("unique_keys",),
    ("encoded_time_predicate", "injective_time_bucket"),
    ("attribute_frequency_sharing", "shapley_summary_state", "frame_evaluation_cache"),
)

_WITNESSES: Final[dict[OptimizationRuleId, str]] = {
    "L1": "query_rewrites::test_public_l1_emitted_filter_preserves_selection_and_second_mask_demand",
    "required_slots": "query_rewrites::test_backward_projection_retains_source_obligations",
    "source_merge": "algebra_semantics::test_common_source_and_terminal_relation_preserve_values_and_na",
    "source_sample_reduction": "algebra_semantics::test_shared_sample_folds_preserve_each_finish",
    "source_key_fusion": "source_key_transport::test_native_query_key_parameters_preserve_complete_membership",
    "source_parameter_block": "source_key_transport::test_native_query_key_parameters_preserve_complete_membership",
    "source_population_check_fusion": "algebra_semantics::test_native_population_check_fusion_keeps_complete_selected_support",
    "source_member_count": "algebra_semantics::test_source_member_counts_keep_complete_tuples_and_null_categories",
    "dead_population_transport": "source_key_transport::test_dead_query_transport_removes_only_an_unused_population",
    "source_output_population_coverage": "algebra_semantics::test_explicit_population_coverage_after_fact_predicates",
    "source_shared_key_fusion": "source_key_transport::test_shared_query_key_grid_preserves_each_group_value",
    "source_terminal_fusion": "algebra_semantics::test_common_source_and_terminal_relation_preserve_values_and_na",
    "native_check_summary": "cross_engine_conformance::test_live_obligatory_composite_witness",
    "pending_frame_top": "algebra_semantics::test_pending_integer_top_issued_sql_differs_with_unchanged_result",
    "unique_keys": "unique_keys_soundness::test_join_only_transfers_keys_when_matches_are_unique",
    "encoded_time_predicate": "windows_and_encoded_time::test_authored_encoded_filter_preserves_canonical_membership",
    "injective_time_bucket": "windows_and_encoded_time::test_native_day_key_deferral_uses_actual_calendar_geometry",
    "attribute_frequency_sharing": "attribution_axioms::test_qualified_state_and_native_coalitions_are_exactly_equal",
    "shapley_summary_state": "attribution_axioms::test_qualified_state_and_native_coalitions_are_exactly_equal",
    "frame_evaluation_cache": "query_rewrites::test_partition_memo_keeps_distinct_partition_values",
    "common_read_cse": "algebra_semantics::test_common_read_cse_projects_each_logical_value_and_check",
    "L5_select_fusion": "select_projection_composition::test_l5_keeps_first_expression_or_carrier_failure_in_compiled_projection_chain",
    "conditional_source_merge": "conditional_aggregation::test_shared_scan_preserves_each_prefix_and_its_demand",
    "unique_keys_degenerate_per": "singleton_partitions::test_native_extremum_is_singleton_identity_in_the_exact_carrier",
    "unique_keys_degenerate_over": "singleton_partitions::test_source_sum_finisher_preserves_a_present_singleton_cell",
    "shared_output_coverage_check": "singleton_partitions::test_exact_coverage_support_reuses_one_witness_per_key",
    "population_product_factorization": "population_rewrites::test_independent_witness_fibers_distribute_over_product",
    "L3_left_total_elimination": "population_rewrites::test_preimage_of_same_image_requires_full_left_support",
    "classify_after_filter": "population_rewrites::test_scalar_graph_extension_commutes_but_identity_check_can_fail_on_dropped_row",
    "L7_key_pushdown": "key_predicate_pushdown::test_cropped_literal_publication_retains_full_domain_coverage",
    "l6_window_prereduction": "window_prereduction_runtime::test_live_native_windows_preserve_sparse_keys_and_exact_payloads",
    "implicit_per_collapse": "sample_rollup::test_implicit_per_collapse_removes_only_the_sample_group",
    "attribute_grouping_sets": "sample_rollup::test_attribute_grouping_separates_native_null_class_from_parent",
    "frame_top_float_decimal": "frame_terminal_fusion::test_float_top_rank_and_complete_key_order",
    "frame_terminal_fix": "frame_terminal_fusion::test_fix_null_safe_complete_key",
    "frame_terminal_reduce": "frame_terminal_fusion::test_reduce_empty_scalar_and_presence",
    "frame_terminal_over": "frame_terminal_fusion::test_over_keeps_rows_and_null_divisor",
    "frame_terminal_align": "frame_terminal_fusion::test_align_composite_key_validity_and_missing_side",
}


class _UnresolvedRuleError(ValueError):
    """A rule argument that does not statically resolve to a closed ID set."""


def _rule_literals(argument: ast.expr, constants: Mapping[str, str]) -> frozenset[str]:
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return frozenset((argument.value,))
    if isinstance(argument, ast.Name) and argument.id in constants:
        return frozenset((constants[argument.id],))
    if isinstance(argument, ast.IfExp):
        return _rule_literals(argument.body, constants) | _rule_literals(argument.orelse, constants)
    raise _UnresolvedRuleError(ast.unparse(argument))


def _rule_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for statement in tree.body:
        targets: tuple[ast.expr, ...]
        if isinstance(statement, ast.AnnAssign):
            targets, value = (statement.target,), statement.value
        elif isinstance(statement, ast.Assign):
            targets, value = tuple(statement.targets), statement.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name) or not target.id.isupper():
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                constants[target.id] = value.value
            else:
                constants.pop(target.id, None)
    return constants


def _terminal_rule_family(tree: ast.Module, constants: Mapping[str, str]) -> frozenset[str]:
    # The operation dispatcher is the owner of the narrow rule type and maps
    # each admitted Frame operation to a concrete ID.
    function = next(
        (
            item
            for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == "_terminal_rule"
        ),
        None,
    )
    if function is None:
        raise _UnresolvedRuleError("_terminal_rule")
    return frozenset(
        rule
        for item in ast.walk(function)
        if isinstance(item, ast.Return)
        and item.value is not None
        and not (isinstance(item.value, ast.Constant) and item.value.value is None)
        for rule in _rule_literals(item.value, constants)
    )


def _rule_argument(node: ast.Call) -> ast.expr | None:
    name = (
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else node.func.id
        if isinstance(node.func, ast.Name)
        else ""
    )
    if name not in ("RuleDecision", "allows"):
        return None
    if node.args:
        return node.args[0]
    return next((arg.value for arg in node.keywords if arg.arg == "rule"), None)


def _scope(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> ast.FunctionDef | None:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef):
            return node
        if isinstance(node, (ast.AsyncFunctionDef, ast.Lambda, ast.comprehension, ast.ClassDef)):
            return None
    return None


def _is_terminal_dispatch(
    function: ast.FunctionDef | None, parents: Mapping[ast.AST, ast.AST]
) -> bool:
    if function is None:
        return False
    parent = parents.get(function)
    replaces_plan = function.name == "_replace_plan" and isinstance(parent, ast.Module)
    dispatches = (function.name == "fuse_frame_terminal" and isinstance(parent, ast.Module)) or (
        function.name == "operation"
        and isinstance(parent, ast.ClassDef)
        and parent.name == "_TerminalLowerer"
        and isinstance(parents.get(parent), ast.Module)
    )
    if not replaces_plan and not dispatches:
        return False
    writes = tuple(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id == "rule" and isinstance(node.ctx, ast.Store)
    )
    if not writes:
        return replaces_plan and any(
            arg.arg == "rule"
            and isinstance(arg.annotation, ast.Name)
            and arg.annotation.id == "FrameTerminalRuleId"
            for arg in function.args.args
        )
    if not dispatches or len(writes) != 1:
        return False
    symbols = symtable.symtable(ast.unparse(function), _TERMINAL_MODULE, "exec").get_children()[0]
    if "_terminal_rule" not in symbols.get_identifiers():
        return False
    dispatcher = symbols.lookup("_terminal_rule")
    if not dispatcher.is_global() or dispatcher.is_assigned():
        return False
    return any(
        isinstance(statement, ast.Assign)
        and any(target is writes[0] for target in statement.targets)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "_terminal_rule"
        for statement in ast.walk(function)
    )


def _rule_sites(tree: ast.Module, file: str) -> Iterator[tuple[int, frozenset[str]]]:
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    constants = _rule_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        argument = _rule_argument(node)
        if argument is None:
            continue
        try:
            if (
                file == _TERMINAL_MODULE
                and isinstance(argument, ast.Name)
                and argument.id == "rule"
                and _is_terminal_dispatch(_scope(node, parents), parents)
            ):
                rules = _terminal_rule_family(tree, constants)
            else:
                rules = _rule_literals(argument, constants)
        except _UnresolvedRuleError as error:
            pytest.fail(f"{file}:{node.lineno}: resolve rule expression {error!s} to a closed ID")
        yield node.lineno, rules


def _unregistered_calls(
    file: str, sites: Iterator[tuple[int, frozenset[str]]], registered: Mapping[str, str]
) -> tuple[str, ...]:
    return tuple(
        f"{file}:{line}: unregistered optimization rule {rule!r}"
        for line, rules in sites
        for rule in sorted(rules)
        if rule not in registered
    )


def _is_formal_case(module: ModuleType, name: str) -> bool:
    function = getattr(module, name, None)
    return (
        name.startswith("test_")
        and inspect.isfunction(function)
        and (getattr(function, "__test__", True) is not False)
    )


def test_every_optimization_rule_has_a_formal_witness() -> None:
    original = frozenset(rule for channel in _ORIGINAL_CHANNELS for rule in channel)
    milestones = frozenset(
        rule for rules in OPTIMIZATION_MILESTONE_RULES.values() for rule in rules
    )
    assert len(_ORIGINAL_CHANNELS) == 10
    assert len(OPTIMIZATION_MILESTONE_RULES) == 9
    assert original | milestones == OPTIMIZATION_RULE_IDS
    assert set(_WITNESSES) == OPTIMIZATION_RULE_IDS
    modules: dict[str, ModuleType] = {}
    for rule, entry in _WITNESSES.items():
        module, separator, test = entry.partition("::")
        assert separator, (rule, entry)
        if module not in modules:
            document = _FORMAL / f"verify_{module}.py"
            assert document.is_file(), (rule, entry)
            loaded = importlib.import_module(f"tests.formal.verify_{module}")
            assert loaded.__file__ is not None and Path(loaded.__file__).resolve() == document
            modules[module] = loaded
        assert _is_formal_case(modules[module], test), (rule, entry)


def test_executable_rewrite_calls_match_the_registry() -> None:
    directory = _ROOT / "marivo" / "analysis"
    registered: dict[str, str] = dict(_WITNESSES.items())
    failures: list[str] = []
    observed: set[str] = set()
    for path in sorted(directory.rglob("*.py")):
        relative = path.relative_to(directory).as_posix()
        source = path.read_text(encoding="utf-8")
        sites = tuple(_rule_sites(ast.parse(source, filename=relative), relative))
        failures.extend(_unregistered_calls(relative, iter(sites), registered))
        for _, rules in sites:
            observed.update(rules)
    assert not failures, "\n".join(failures)
    assert observed == OPTIMIZATION_RULE_IDS


@pytest.mark.parametrize(
    "expression",
    (
        'policy.allows("new_rule_without_evidence")',
        'RuleDecision("new_rule_without_evidence", "adopted", (), "example")',
        'RuleDecision("L1" if same else "new_rule_without_evidence", "refused", (), "example")',
    ),
)
def test_new_rule_needs_a_registered_formal_witness(expression: str) -> None:
    file = "compiler/example.py"
    sites = _rule_sites(ast.parse(expression, filename=file), file)
    assert _unregistered_calls(file, sites, {"L1": "example"}) == (
        "compiler/example.py:1: unregistered optimization rule 'new_rule_without_evidence'",
    )


def test_named_policy_rule_needs_a_registered_formal_witness() -> None:
    file = "compiler/example.py"
    source = '_RULE: Final = "new_rule_without_evidence"\npolicy.allows(_RULE)'
    sites = _rule_sites(ast.parse(source, filename=file), file)
    assert _unregistered_calls(file, sites, {"L1": "example"}) == (
        "compiler/example.py:2: unregistered optimization rule 'new_rule_without_evidence'",
    )


def test_terminal_rule_name_cannot_hide_a_new_rule() -> None:
    source = """
def _terminal_rule(stage):
    return "L1"

def other(policy):
    rule = cast(FrameTerminalRuleId, "new_rule_without_evidence")
    policy.allows(rule)
"""
    with pytest.raises(pytest.fail.Exception, match="resolve rule expression rule"):
        tuple(_rule_sites(ast.parse(source), _TERMINAL_MODULE))


def test_witness_must_be_a_collected_test_function() -> None:
    module = ModuleType("verify_example")
    module.__dict__["test_nonfunction"] = 3
    assert not _is_formal_case(module, "test_nonfunction")


@pytest.mark.parametrize(
    "signature,binding",
    (
        ("policy, _terminal_rule", ""),
        ("policy", "    def _terminal_rule(stage): return 'unregistered_rule'\n"),
        ("policy", "    from elsewhere import _terminal_rule\n"),
    ),
)
def test_shadowing_the_terminal_dispatcher_refuses_unresolved_rule(
    signature: str, binding: str
) -> None:
    source = (
        'def _terminal_rule(stage): return "L1"\n'
        f"def fuse_frame_terminal({signature}):\n"
        f"{binding}    rule = _terminal_rule(None)\n"
        "    return policy.allows(rule)\n"
    )
    with pytest.raises(pytest.fail.Exception, match="resolve rule expression rule"):
        tuple(_rule_sites(ast.parse(source), _TERMINAL_MODULE))
