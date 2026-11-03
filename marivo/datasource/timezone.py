"""Provider checks for native named-zone conversion availability."""

from ibis.backends import BaseBackend

from marivo._timezones import normalize_timezone
from marivo.datasource.engines import profile_for_backend


def verify_source_timezone(backend: BaseBackend, name: str) -> None:
    """Verify a selected native projection timezone where the provider requires it."""
    profile = profile_for_backend(backend)
    if profile.name == "mysql":
        from marivo.datasource.engines.mysql import verify_source_timezone as verify_mysql

        normalized = normalize_timezone(name)
        # Fixed offsets use clock arithmetic and UTC is lowered to +00:00;
        # neither path depends on MySQL's optional named time zone tables.
        if normalized.name != "UTC" and normalized.kind == "iana":
            verify_mysql(backend, normalized.name)
