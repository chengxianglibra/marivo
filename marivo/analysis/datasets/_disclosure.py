"""Terminal Dataset capabilities are owned by the native Artifact provider."""

from marivo.analysis._capabilities.dataset_model import DisclosureProvider


def provider() -> DisclosureProvider:
    return DisclosureProvider("core", (), ())
