"""Public online metadata deliberately excludes provider URLs and headers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import ItemId, OnlinePlaybackIdentity, SourceId


class _Public(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CatalogCapabilities(_Public):
    search: bool = True
    episodes: bool = True
    refresh: bool = True
    browse: bool = True


class CatalogSource(_Public):
    id: SourceId
    name: str
    provider: Literal["maccms", "open_catalog"]


class CatalogSourceSummary(CatalogSource):
    priority: int
    capabilities: CatalogCapabilities = Field(default_factory=CatalogCapabilities)
    configuration_token: str = ""


class CatalogSources(_Public):
    enabled: bool
    items: list[CatalogSourceSummary]
    scope_token: str = ""


class CatalogSearchItem(_Public):
    external_id: str
    title: str
    year: str = ""
    kind: Literal["movie", "tv", "unknown"] = "unknown"
    description: str = ""
    category: str = ""
    poster_path: str = ""


class CatalogCategory(_Public):
    id: str
    name: str


class CatalogSearch(_Public):
    source: CatalogSource
    items: list[CatalogSearchItem]
    page: int
    page_count: int
    total: int
    categories: list[CatalogCategory] = Field(default_factory=list)


class CatalogItem(_Public):
    id: ItemId
    source_id: SourceId
    kind: Literal["movie", "tv"]
    title: str


class CatalogEntry(_Public):
    label: str
    identity: OnlinePlaybackIdentity | None
    transport: Literal["mp4", "hls"] | None
    supported: bool
    reason: str = ""


class CatalogLine(_Public):
    id: str
    label: str
    entries: list[CatalogEntry]


class CatalogAttribution(_Public):
    label: str
    url: str


class CatalogDetail(_Public):
    source: CatalogSource
    item: CatalogItem
    description: str
    year: str
    lines: list[CatalogLine]
    attribution: list[CatalogAttribution] = Field(default_factory=list)
    poster_path: str = ""
