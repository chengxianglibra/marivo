"""Source-domain authority and source-free binding reuse across backend dispatch."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo._compat import Never
from marivo.analysis.compiler.placement import ParquetBinding, SourceBinding
from marivo.analysis.materialization import admission
from marivo.analysis.observation.contracts import source_owner_of
from tests.lazy_binding_cold_worker import snapshot
from tests.lazy_local_fixtures import REVENUE, setup_local
from tests.lazy_observation_fixtures import make_sources


@pytest.mark.parametrize(
    "field",
    [
        "session",
        "store",
        "catalog",
        "semantic-registry",
        "sidecar",
        "binding-scopes",
        "action-port",
    ],
)
def test_source_domain_rejects_each_independent_authority_change(
    field: str,
) -> None:
    owner = source_owner_of(make_sources().observe(REVENUE))
    other = source_owner_of(
        make_sources(session_id="other-session", store_id="other-store").observe(REVENUE)
    )
    binding = SourceBinding(owner, "warehouse", "duckdb")
    equivalent = replace(binding, owner=replace(owner))
    assert binding.same_domain(equivalent)
    assert equivalent.same_domain(binding)
    changed_owners = {
        "session": replace(owner, session_id=other.session_id),
        "store": replace(owner, store_id=other.store_id),
        "catalog": replace(owner, catalog_identity=object()),
        "semantic-registry": replace(owner, semantic_registry=other.semantic_registry),
        "sidecar": replace(owner, sidecar=other.sidecar),
        "binding-scopes": replace(owner, binding_scopes=other.binding_scopes),
        "action-port": replace(owner, action_port=other.action_port),
    }
    changed = replace(binding, owner=changed_owners[field])
    assert not binding.same_domain(changed)
    assert not changed.same_domain(binding)


@pytest.mark.parametrize("field", ["datasource", "backend"])
def test_source_domain_requires_exact_datasource_and_backend(field: str) -> None:
    owner = source_owner_of(make_sources().observe(REVENUE))
    binding = SourceBinding(owner, "warehouse", "duckdb")
    changed = (
        replace(binding, datasource_id="other-warehouse")
        if field == "datasource"
        else replace(binding, adapter="postgres")
    )
    assert not binding.same_domain(changed)
    assert not changed.same_domain(binding)


@pytest.mark.parametrize("field", ["owner", "datasource", "digest"])
def test_parquet_domain_requires_exact_retained_authority(field: str) -> None:
    owner = source_owner_of(make_sources().observe(REVENUE))
    binding = ParquetBinding(owner, "warehouse", "retained-digest")
    assert binding.same_domain(replace(binding))
    changed = (
        replace(binding, owner=replace(owner))
        if field == "owner"
        else replace(binding, datasource_id="other-warehouse")
        if field == "datasource"
        else replace(binding, domain_digest="other-digest")
    )
    assert not binding.same_domain(changed)
    assert not changed.same_domain(binding)


def test_retained_and_source_domains_cannot_share_authority() -> None:
    owner = source_owner_of(make_sources().observe(REVENUE))
    source = SourceBinding(owner, "warehouse", "duckdb")
    retained = ParquetBinding(owner, "warehouse", "retained-digest")
    assert not source.same_domain(retained)
    assert not retained.same_domain(source)


@pytest.mark.runtime
def test_exact_binding_hit_skips_dispatch_credentials_and_source_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, database = setup_local(tmp_path)
    logical = sources.observe(REVENUE)
    retained = logical.execute()
    run_ref = runtime.last_run_ref
    assert run_ref is not None
    before = snapshot(runtime)
    database.rename(database.with_suffix(".offline"))

    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("Exact binding recovery attempted new source execution")

    for name in (
        "place",
        "require_profile_for_backend_type",
        "_effective_kwargs",
        "_build_backend_from_effective",
    ):
        monkeypatch.setattr(admission, name, forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.duckdb_execution.open_native_backend", forbidden
    )

    recovered = logical.execute()
    assert recovered.state.artifact_ref == retained.state.artifact_ref
    assert runtime.last_run_ref == run_ref
    assert snapshot(runtime) == before
    assert runtime.statistics.events == {"reconciliation": 1}
