"""AI settings are opt-in; upstream auth errors must not become application auth prompts."""

from fastapi import APIRouter, HTTPException

from ..ai import client, settings

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/settings")
def get_settings():
    try:
        return settings.public_settings()
    except ValueError:
        raise HTTPException(400, "智能辅助配置无效，请检查 AI 环境变量或重新保存设置") from None


@router.patch("/settings")
def update_settings(body: dict):
    try:
        result = settings.update(body)
    except ValueError as exc:
        # Validation messages are fixed labels only, never secret values or provider bodies.
        raise HTTPException(400, str(exc)) from None
    client.clear_cache()
    return result


@router.post("/check")
def check():
    with client.operation_session():
        return client.check_connection()
