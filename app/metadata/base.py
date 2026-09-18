"""元数据 Provider 抽象（E 阶段）：统一候选结构与链式调度约定。"""


class Candidate:
    """跨来源候选：本地缓存/NFO/无 key API/爬虫统一形态。"""

    __slots__ = ("title", "original_title", "year", "tmdb_id", "imdb_id",
                 "source", "source_id", "score", "payload")

    def __init__(self, title: str = "", original_title: str = "",
                 year: int | None = None, tmdb_id: int | None = None,
                 imdb_id: str = "", source: str = "", source_id: str = "",
                 score: float = 0.0, payload: dict | None = None):
        self.title = title or ""
        self.original_title = original_title or ""
        self.year = year
        self.tmdb_id = tmdb_id
        self.imdb_id = imdb_id or ""
        self.source = source or ""
        self.source_id = str(source_id or "")
        self.score = float(score or 0)
        self.payload = payload or {}

    def to_dict(self) -> dict:
        return {"title": self.title, "original_title": self.original_title,
                "year": self.year, "tmdb_id": self.tmdb_id,
                "imdb_id": self.imdb_id, "source": self.source,
                "source_id": self.source_id, "score": round(self.score, 1)}

    def __repr__(self):  # pragma: no cover - 调试友好
        return f"<Candidate {self.source}:{self.source_id} {self.title!r} {self.year} s={self.score:.0f}>"


__all__ = ['Candidate']
