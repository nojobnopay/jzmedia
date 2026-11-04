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
    search_filters: bool = False
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


class CatalogSearchSelection(_Public):
    category_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_-]*$")
    category_scope: Literal["exact", "descendants"] = "exact"
    year: str = Field(default="", max_length=4, pattern=r"^(?:[1-9][0-9]{3})?$")
    offset: int = Field(default=0, ge=0, le=5000)
    limit: int = Field(default=20, ge=1, le=100)


class CatalogSearchSessionCreate(CatalogSearchSelection):
    source_id: SourceId
    q: str = Field(min_length=1, max_length=100)
    refresh: bool = False


class CatalogSearchSession(_Public):
    session_id: str = Field(pattern=r"^srch_[0-9a-f]{32}$")
    version: int
    source: CatalogSource
    configuration_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    query: str
    status: Literal["loading", "partial", "complete", "error"]
    items: list[CatalogSearchItem]
    categories: list[CatalogCategory]
    scanned: int
    found: int
    total: int | None
    has_more: bool
    can_continue: bool
    error_code: str = ""
    validated_at: int


class CatalogBackgroundReady(CatalogSearch):
    status: Literal["ready"]
    configuration_token: str = Field(pattern=r"^[0-9a-f]{64}$")


class CatalogBackgroundDeferred(_Public):
    status: Literal["deferred"]


CatalogSearchResponse = CatalogBackgroundReady | CatalogBackgroundDeferred | CatalogSearch


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
