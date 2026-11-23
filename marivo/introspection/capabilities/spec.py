"""Shared Help specification and registry interfaces."""

from typing import Protocol, TypeVar

from marivo._kernel.errors import HelpSurface


class CapabilitySpec(Protocol):
    """Minimal native-descriptor facts consumed by neutral resolution."""

    @property
    def canonical_id(self) -> str:
        """Return the canonical target accepted by the owning help surface."""
        ...

    @property
    def public_entrypoint(self) -> str | None:
        """Return the public invocation shape, when the target is invokable."""
        ...

    @property
    def callable_path(self) -> str | None:
        """Return the registered callable path, when one exists."""
        ...

    @property
    def summary(self) -> str:
        """Return a bounded factual summary used for suggestions."""
        ...


DescriptorT_co = TypeVar(
    "DescriptorT_co",
    bound=CapabilitySpec,
    covariant=True,
)


class RegistryView(Protocol[DescriptorT_co]):
    """Read-only native registry view required by the neutral resolver."""

    @property
    def surface(self) -> HelpSurface:
        """Return the owning help surface."""
        ...

    def canonical_ids(self) -> tuple[str, ...]:
        """Return canonical help ids in deterministic registry order."""
        ...

    def discovery_ids(self) -> tuple[str, ...]:
        """Return canonical ids intended for progressive public discovery."""
        ...

    def by_canonical_id(self, canonical_id: str) -> DescriptorT_co:
        """Return the original native descriptor for a canonical id."""
        ...

    def by_callable(self, value: object) -> DescriptorT_co:
        """Return the original descriptor for an exact callable identity."""
        ...
