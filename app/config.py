import os
from pydantic import BaseModel


class Settings(BaseModel):
    app_port: int = int(os.getenv("APP_PORT", "8080"))
    env: str = os.getenv("ENV", "dev")
    media_root: str = os.getenv("MEDIA_ROOT", "./sample_media")
    data_dir: str = os.getenv("DATA_DIR", "./data")
    tmdb_api_key: str = os.getenv("TMDB_API_KEY", "")
    tmdb_read_token: str = os.getenv("TMDB_READ_TOKEN", "")
    tmdb_proxy: str = os.getenv("TMDB_PROXY", "")
    tmdb_language: str = os.getenv("TMDB_LANGUAGE", "zh-CN")
    tmdb_image_base: str = os.getenv("TMDB_IMAGE_BASE", "https://image.tmdb.org")


settings = Settings()
