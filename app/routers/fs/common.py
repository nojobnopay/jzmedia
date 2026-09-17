"""routers.fs.common（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
from fastapi import APIRouter

from ...log import get_logger

router = APIRouter(prefix="/api/fs")
logger = get_logger("fs")
__all__ = ['router', 'logger']
