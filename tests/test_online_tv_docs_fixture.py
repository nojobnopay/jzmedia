"""The TV documentation artwork remains isolated from the normal catalogue."""

from copy import deepcopy
import json
from pathlib import Path

from app.online.maccms import parse_detail
from scripts.online_fixture.catalog_origin import FixtureOrigin
from scripts.online_fixture.tv_docs import install_documentation_artwork


class DemoOrigin:
    def __init__(self, root):
        self.root = root
        self.base_url = "http://fixture.invalid:12345"
        self.assets = set()
        self.taxonomy = False

    def records(self, mode="normal"):
        del mode
        return deepcopy([
            {"vod_id": f"demo-{index}", "vod_name": "未替换",
             "vod_pic": "original.png", "vod_year": "2026", "type_name": "都市脑洞",
             "vod_play_from": "MP4直链$$$HLS直链",
             "vod_play_url": "第1集$http://fixture.invalid/1.mp4#第2集$http://fixture.invalid/2.mp4"
                             "$$$第1集$http://fixture.invalid/1.m3u8#第2集$http://fixture.invalid/2.m3u8"}
            for index in range(14)
        ])


def test_documentation_artwork_matches_metadata_and_keeps_the_default_fixture(tmp_path):
    normal_records = FixtureOrigin.records
    isolated = DemoOrigin(tmp_path)
    untouched = DemoOrigin(tmp_path)
    install_documentation_artwork(isolated)

    manifest = json.loads(Path("design/demo-artwork/manifest.json").read_text(encoding="utf-8"))
    artwork = [item for item in manifest["assets"] if item["id"].startswith(("movie-", "show-"))]
    rows = isolated.records()
    assert isolated.taxonomy is True
    for row, item in zip(rows, artwork):
        kind, number = item["id"].split("-")
        year = 2014 + int(number) if kind == "movie" else 2023 - int(number)
        filename = f"tv-doc-{item['id']}.png"
        assert (row["vod_name"], row["vod_year"], row["vod_pic"]) == (
            item["title"], str(year), f"{isolated.base_url}/files/{filename}")
        assert filename in isolated.assets
        assert (tmp_path / filename).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        groups = row["vod_play_url"].split("$$$")
        detail = parse_detail({"id": "src_" + "a" * 32, "allowed_hosts": ["fixture.invalid"]}, row)
        assert all(not entry.reason and entry.url for line in detail.lines for entry in line.entries)
        if kind == "movie":
            assert row["type_name"] == "电影"
            assert all(group.startswith("正片$") and "#" not in group for group in groups)
        else:
            assert row["type_name"] == "剧集"
            assert all(group.count("#") == 1 for group in groups)

    assert FixtureOrigin.records is normal_records
    assert untouched.records()[0]["vod_name"] == "未替换"
    assert not untouched.taxonomy and not untouched.assets
