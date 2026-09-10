from fastapi import APIRouter

from ..config import settings

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"status": "ok", "phase": "phase2"}


@router.get("/settings")
def get_settings():
    return {
        "media_root": settings.media_root,
        "tmdb_language": settings.tmdb_language,
        "tmdb_configured": bool(settings.tmdb_read_token or settings.tmdb_api_key),
        "tmdb_proxy_set": bool(settings.tmdb_proxy),
        "tmdb_image_base": settings.tmdb_image_base,
    }
