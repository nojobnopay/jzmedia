"""Server-only provider results for enabled, administrator-owned sources.

HTTP requests supply stable catalogue identities, never a playback URL or
upstream header policy. Tests can replace resolve() with an isolated provider.
"""

from dataclasses import dataclass, field
import math

from ..network.headers import HeaderPolicy
from .models import OnlinePlaybackIdentity


class ProviderError(RuntimeError):
    def __init__(self, category="provider_not_ready"):
        if category not in {"provider_not_ready", "provider_resolution_failed", "invalid_provider_result",
                            "invalid_provider_response", "provider_response_limit", "provider_upstream_error",
                            "provider_rate_limited", "provider_auth_failed", "provider_not_found",
                            "provider_network_error", "provider_timeout", "provider_cancelled",
                            "provider_ambiguous_identity", "provider_line_required", "provider_line_not_found",
                            "provider_media_unsupported", "provider_episode_not_found", "provider_catalog_busy"}:
            category = "provider_resolution_failed"
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class ResolvedPlayback:
    url: str = field(repr=False)
    transport: str
    seekable: bool = True
    duration_seconds: float | None = None
    header_policy: HeaderPolicy | None = field(default=None, repr=False)
    source_fingerprint: str | None = field(default=None, repr=False)

    def __post_init__(self):
        duration = self.duration_seconds
        if (not isinstance(self.url, str) or not self.url or len(self.url) > 8192
                or self.transport not in {"mp4", "hls"} or type(self.seekable) is not bool
                or duration is not None and (type(duration) not in {float, int}
                                             or not math.isfinite(duration) or duration <= 0)
                or self.header_policy is not None and not isinstance(self.header_policy, HeaderPolicy)
                or self.source_fingerprint is not None and (not isinstance(self.source_fingerprint, str)
                                                           or not self.source_fingerprint
                                                           or len(self.source_fingerprint) > 65536)):
            raise ProviderError("invalid_provider_result")


def resolve(identity: OnlinePlaybackIdentity) -> ResolvedPlayback:
    """Refresh the exact server-owned item/line/episode before creating media."""
    from .catalog import resolve as resolve_catalog
    return resolve_catalog(identity)
