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
    year_filter: bool = False
    related: bool = False


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


class CatalogMetadata(_Public):
    category: str = ""
    category_id: str = ""
    parent_category_id: str = ""
    remarks: str = ""
    genres: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    directors: list[str] = Field(default_factory=list)
    area: str = ""
    language: str = ""
    subtitle: str = ""


class CatalogSearchItem(CatalogMetadata):
    external_id: str
    title: str
    year: str = ""
    kind: Literal["movie", "tv", "unknown"] = "unknown"
    description: str = ""
    poster_path: str = ""


class CatalogRelatedItem(CatalogSearchItem):
    reason: Literal["inferred_season", "same_category", "name_related"]
    season: int | None = Field(default=None, ge=1, le=999)


class CatalogRelated(_Public):
    source: CatalogSource
    external_id: str
    current_season: int | None = Field(default=None, ge=1, le=999)
    status: Literal["ready", "deferred", "unsupported"]
    partial: bool = False
    series: list[CatalogRelatedItem] = Field(default_factory=list, max_length=12)
    related: list[CatalogRelatedItem] = Field(default_factory=list, max_length=12)
    name_related: list[CatalogRelatedItem] = Field(default_factory=list, max_length=12)


class CatalogCategory(_Public):
    id: str
    name: str
    parent_id: str = ""


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


class CatalogDetail(CatalogMetadata):
    source: CatalogSource
    item: CatalogItem
    description: str
    year: str
    lines: list[CatalogLine]
    attribution: list[CatalogAttribution] = Field(default_factory=list)
    poster_path: str = ""
