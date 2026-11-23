"""Invocation-owned proof of ordinary stored source fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ibis.expr.operations as ops

from marivo.datasource import credentials
from marivo.datasource.engines.column_storage import proves_stored_projection
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.runtime.read_types import BoundSource, issued_by

if TYPE_CHECKING:
    from ibis.backends import BaseBackend

    from marivo.datasource.engines.base import EngineProfile
    from marivo.datasource.ir import DatasourceIR


class StoredProjectionFacts:
    """Retain metadata admission only inside the source that issued the binding."""

    def __init__(
        self, profile: EngineProfile, datasource: DatasourceIR, backend: BaseBackend, owner: object
    ) -> None:
        self._profile = profile
        self._datasource = datasource
        self._backend = backend
        self._owner = owner
        self._stored: dict[str, bool] = {}

    def proves(self, binding: BoundSource) -> bool:
        """Admit only a verified stored catalog input without reading its rows."""
        if not issued_by(binding, self._owner) or not isinstance(binding.source, TableSourceIR):
            return False
        identity = binding.facts.source_identity
        if identity not in self._stored:
            relations = tuple(binding.relation.op().find(ops.DatabaseTable))
            stored = False
            if len(relations) == 1:
                try:
                    with credentials.backend_errors(self._backend):
                        stored = proves_stored_projection(
                            self._backend, self._profile, self._datasource, relations[0]
                        )
                except Exception:
                    # Metadata denial leaves the original separate reads qualified.
                    stored = False
            self._stored[identity] = stored
        return self._stored[identity]
