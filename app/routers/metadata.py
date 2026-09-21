"""元数据 provider 状态（E 阶段补全）：降级链健康快照 + 冷却重置。

- `GET  /api/metadata/providers`：默认链 + 各 provider 冷却/失败/最近错误（纯本地）。
- `POST /api/metadata/providers/reset`：清冷却（body 缺省=全部；写操作受鉴权中间件保护）。
"""
from fastapi import APIRouter, HTTPException

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


@router.post("/providers/reset")
def reset_providers(body: dict | None = None):
    name = ""
    if isinstance(body, dict):
        name = str(body.get("name") or "").strip()
    if name and name not in chain.KNOWN_PROVIDERS:
        raise HTTPException(422, "unknown provider")
    state.reset(name or None)
    return _payload()
