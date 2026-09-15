from dataclasses import replace

from marivo.analysis.materialization import resources

"""Connection resource proof remains independent of caller computation."""


def test_connection_relation_proof_is_exact_and_retires_after_its_own_discharge() -> None:
    execution = resources.backend_reservation("run_a", "domain_a")
    relation = replace(
        execution,
        resource_kind="planner_temporary_relation",
        safe_locator=execution.safe_locator + "/relation",
    )
    resources.prove_local_termination(execution)
    assert not resources.confirm_execution_termination(replace(relation, run_ref="run_b"))
    assert not resources.confirm_execution_termination(
        replace(relation, execution_domain_id="domain_b")
    )
    assert resources.confirm_execution_termination(relation)
    resources.forget_local_termination((execution,))
    assert not resources.confirm_execution_termination(execution)
    assert resources.confirm_execution_termination(relation)
    resources.forget_local_termination((relation,))
    assert not resources.confirm_execution_termination(relation)
