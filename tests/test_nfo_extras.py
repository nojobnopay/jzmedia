"""D6 NFO 扩展（Plex NFO Agent 字段）与所有权保护（外部改动不覆盖）。"""
import xml.etree.ElementTree as ET

from app import scanner, store
from app.nfo import write_movie_nfo


def test_nfo_writes_plex_fields(tmp_path):
    movie = {
        "id": 1, "title": "标题", "original_title": "Original", "year": 2020,
        "overview": "剧情", "tmdb_id": 123, "imdb_id": "tt123",
        "tmdb_rating": 7.8, "douban_rating": 8.1, "custom_rating": 9.0,
        "genres": ["剧情"], "tags": ["tag1"],
        "origin_countries": ["CN"], "origin_country": "CN",
        "persons": [{"name": "演员 A", "role": "actor", "character_name": "角色",
                     "cast_order": 0},
                    {"name": "导演 B", "role": "director", "character_name": ""}],
        "_tmdb_cache": {"premiered": "2020-05-01", "tagline": "一句台词",
                        "runtime": 121, "studios": ["Studio X"],
                        "collection_name": "测试系列"},
    }
    p = tmp_path / "movie.nfo"
    write_movie_nfo(movie, str(p))
    root = ET.parse(str(p)).getroot()
    assert root.findtext("premiered") == "2020-05-01"
    assert root.findtext("tagline") == "一句台词"
    assert root.findtext("runtime") == "121"
    assert root.findtext("studio") == "Studio X"
    assert root.findtext("set/name") == "测试系列"
    ratings = root.find("ratings/rating[@name='themoviedb']")
    assert ratings is not None and ratings.findtext("value") == "7.8"
    actor = root.find("actor")
    assert actor is not None and actor.findtext("order") == "0"


def test_nfo_ownership_skips_external_edit(media_root):
    rel = "nfoown/Movie.2020.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title="Own", year=2020, tmdb_id=555001)
    try:
        assert scanner.nfo_link.sync_nfos_for(mid, str(p))["ok"] is True
        nfo = p.parent / "movie.nfo"
        assert nfo.is_file()
        assert (store.get_movie(mid) or {}).get("nfo_hash")

        nfo.write_text(nfo.read_text(encoding="utf-8") + "<!-- user edit -->",
                       encoding="utf-8")
        assert scanner.nfo_link.sync_nfos_for(mid, str(p))["ok"] is True
        assert "user edit" in nfo.read_text(encoding="utf-8")   # 不覆盖外部改动

        assert scanner.nfo_link.sync_nfos_for(mid, str(p), force=True)["ok"] is True
        assert "user edit" not in nfo.read_text(encoding="utf-8")
        # 重建后哈希已更新：普通同步不再视为外部改动
        scanner.nfo_link.sync_nfos_for(mid, str(p))
        assert "user edit" not in nfo.read_text(encoding="utf-8")
    finally:
        store.delete_movie(mid)
