"""Process-local source recipes survive completion without fixed-data history."""

import gc
import weakref

import marivo.analysis as mv
from marivo.analysis.core.frame_data import MemoryFrameBacking
from tests.shared_fixtures import DslCaseFactory, catalog_value


def test_completed_selection_retains_only_source_recipes_and_reopen_has_none(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = catalog_value(case.session.catalog, "sales.order.amount", kind="field")
    source = case.session.observe({"q": amount.sum()})
    selected = source.select({"renamed": source["q"]})
    previous = weakref.ref(selected)
    completed = selected.compute()
    assert completed._derivation is not None
    assert [item.path for item in completed._derivation.outputs] == [("renamed",)]
    assert completed._derivation.outputs[0].target.current.path == ("q",)
    del selected
    gc.collect()
    assert previous() is None
    saved = completed.persist()
    assert saved._derivation is completed._derivation
    reopened = case.session.persisted_frame(saved.artifact.id)
    assert reopened._input_id == saved._input_id
    assert reopened._derivation is None
    assert reopened.compute()._derivation is None


def test_compare_tracks_every_alias_and_rejects_column_arithmetic_promotion(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = catalog_value(case.session.catalog, "sales.order.amount", kind="field")
    source = case.session.observe({"q": amount.sum()}).compute()
    compared = source.compare({"a": mv.baseline(source), "b": mv.baseline(source)})
    assert compared._derivation is not None
    outputs = {output.path: output.target for output in compared._derivation.outputs}
    assert outputs[("a", "absolute", "q")].mode == "absolute"
    assert outputs[("b", "relative", "q")].mode == "relative"
    assert outputs[("a", "absolute", "q")].pairing != outputs[("b", "absolute", "q")].pairing
    arithmetic = source.select({"q": source["q"] + 1})
    assert arithmetic._derivation is not None
    assert arithmetic._derivation.outputs == ()
    wrapped = arithmetic.compare({"a": mv.baseline(arithmetic)})
    assert wrapped._derivation is not None
    assert wrapped._derivation.outputs == ()
    aligned = source.align(source)
    difference = aligned.select({"delta": aligned["left.q"] - aligned["right.q"]})
    assert difference._derivation is not None
    assert difference._derivation.outputs[0].target.mode == "absolute"
    doubled = aligned.select({"delta": aligned["left.q"] + aligned["right.q"]})
    assert doubled._derivation is not None
    assert doubled._derivation.outputs == ()


def test_frame_key_dependencies_become_coordinate_only_checkpoints(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = catalog_value(case.session.catalog, "sales.order.amount", kind="field")
    region = catalog_value(case.session.catalog, "sales.customer.region", kind="field")
    source = case.session.observe({"q": amount.sum()}, by=(region,))
    selected = source.top(1, by=source["q"])
    population = case.session.population_from(selected, on=((selected.axes[0], region),))
    target = case.session.observe(
        {"q": amount.sum()}, by=(region,), where=mv.in_population(population)
    )
    selected_reference = weakref.ref(selected)
    completed = target.compute()
    assert completed._derivation is not None
    binding = completed._derivation.outputs[0].target.current.binding
    dependency = binding.frame_key_inputs[0].frame
    assert dependency is not selected
    assert dependency._derivation is None
    assert isinstance(dependency._backing, MemoryFrameBacking)
    assert dependency.output_names == ()
    assert dependency._backing.data.row_count == 1
    del target, selected, population
    gc.collect()
    assert selected_reference() is None


def test_align_difference_uses_projected_slots_after_direct_rename(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = catalog_value(case.session.catalog, "sales.order.amount", kind="field")
    first = case.session.observe({"a": amount.sum()})
    second = case.session.observe({"b": amount.sum()})
    left = first.select({"q": first["a"]})
    right = second.select({"q": second["b"]})
    aligned = left.align(right)
    difference = aligned.select({"delta": aligned["left.q"] - aligned["right.q"]})
    assert difference._derivation is not None
    target = difference._derivation.outputs[0].target
    assert target.mode == "absolute"
    assert target.current.path == ("a",)
    assert target.baseline is not None
    assert target.baseline.path == ("b",)
