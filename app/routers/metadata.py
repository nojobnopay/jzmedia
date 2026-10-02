"""元数据 provider 状态（E 阶段补全）：降级链健康快照 + 冷却重置。

- `GET  /api/metadata/providers`：默认链 + 各 provider 冷却/失败/最近错误（纯本地）。
- `POST /api/metadata/providers/reset`：清冷却（body 缺省=全部；写操作受鉴权中间件保护）。
"""
import time

from fastapi import APIRouter, HTTPException, Query

from .. import store
from ..metadata import chain, state

router = APIRouter(prefix="/api/metadata")


def _payload() -> dict:
    names = sorted(chain.KNOWN_PROVIDERS)
    return {"default_chain": list(chain.DEFAULT_CHAIN),
            "known": names,
            "fail_threshold": state.FAIL_THRESHOLD,
            "cooldown_sec": state.COOLDOWN_SEC,
            "providers": state.snapshot(names)}


@router.get("/providers")
def list_providers():
    return _payload()


@router.get("/test-search")
def test_search(library: int = Query(..., gt=0),
                q: str = Query(..., min_length=1, max_length=200)):
    """Test the selected library's saved provider order without binding media."""
    lib = store.get_library(library)
    if not lib:
        raise HTTPException(404, "video library not found")
    term = q.strip()
    if not term:
        raise HTTPException(422, "search text is required")
    kind = "tv" if lib.get("kind") == "tv" else "movie"
    started = time.monotonic()
    hits = chain.search(term, kind=kind, library_id=library, limit=10)
    return {"library_id": library, "kind": kind,
            "chain": chain.chain_for(library),
            "items": [hit.to_dict() for hit in hits],
            "source": hits[0].source if hits else None,
            "elapsed_ms": round((time.monotonic() - started) * 1000)}


@router.post("/providers/reset")
def reset_providers(body: dict | None = None):
    name = ""
    if isinstance(body, dict):
        name = str(body.get("name") or "").strip()
    if name and name not in chain.KNOWN_PROVIDERS:
        raise HTTPException(422, "unknown provider")
    state.reset(name or None)
    return _payload()
