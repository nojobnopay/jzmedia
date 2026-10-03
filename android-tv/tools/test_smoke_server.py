#!/usr/bin/env python3
"""Check synthetic search/browse contracts without FFmpeg, network or application imports.

Run: python android-tv/tools/test_smoke_server.py
These checks validate the isolated UI fixtures, not the production search engine.
"""
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlencode

from smoke_server import Fixtures


class BrowseFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jzmedia-tv-browse-test-")
        self.addCleanup(self.temp.cleanup)
        self.fixtures = Fixtures(Path(self.temp.name))

    def request(self, path, **params):
        return self.fixtures.handle("GET", path, parse_qs(urlencode(params)), {})

    def search(self, **params):
        return self.request("/api/tv-client/search", **params)

    def season(self, **params):
        return self.request("/api/tv/shows/100/seasons/1", **params)

    def test_search_chinese_initials_full_pinyin_and_partial_query(self):
        for query in ("SQ", "sqhs", "ＳＱ", "沙丘", "shaqiuhuisheng"):
            with self.subTest(query=query):
                result = self.search(q=query)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["items"][0]["title"], "沙丘回声")
                self.assertEqual(result["items"][0]["kind"], "movie")
        for query in ("YYDDT", "遥远", "yaoyuandedengta"):
            self.assertEqual(self.search(q=query)["items"][0]["kind"], "show")
        self.assertEqual(self.search(q="KHSG")["items"][0]["kind"], "collection")

    def test_search_kind_and_library_filters(self):
        self.assertEqual(self.search(q="SQ", kind="show")["items"], [])
        self.assertEqual(self.search(q="SQ", media_library=1)["total"], 1)
        self.assertEqual(self.search(q="SQ", media_library=2)["items"], [])
        second_library = self.search(q="XJHC", kind="movie", media_library=2)
        self.assertEqual([row["id"] for row in second_library["items"]], [45])
        self.assertEqual(self.search(q="XJHC", media_library=999)["total"], 0)
        self.assertEqual(self.search(q="YYDDT", media_library=2)["total"], 0)

    def test_search_pagination_keeps_total_and_unique_ids(self):
        first = self.search(q="XJHC", limit=24)
        second = self.search(q="XJHC", limit=24, offset=24)
        self.assertEqual((first["total"], second["total"]), (44, 44))
        self.assertEqual((len(first["items"]), len(second["items"])), (24, 20))
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))
        self.assertEqual((second["limit"], second["offset"]), (24, 24))
        ids = [row["id"] for row in first["items"] + second["items"]]
        self.assertEqual(len(set(ids)), 44)
        self.assertEqual(ids.count(1), 1)  # Film 1 has two physical playback versions.
        self.assertEqual(self.search(q="XJHC", offset=44)["items"], [])

    def test_empty_query_and_invalid_parameters(self):
        self.assertEqual(self.search(q="")["total"], 0)
        self.assertEqual(self.search(q="---")["items"], [])
        for params in ({"limit": 0}, {"limit": 61}, {"offset": -1}, {"kind": "episode"}, {"q": "x" * 81}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.search(**{"q": "SQ", **params})

    def test_default_three_episode_fixture_is_preserved(self):
        result = self.season()
        self.assertEqual([row["id"] for row in result["episodes"]], [201, 202, 203])
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["versions"], [{"version": 1, "count": 3}])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["episodes"][0]["progress"]["position"], 46)
        self.assertIsNone(result["episodes"][-1]["next_episode"])

    def test_stress_season_pages_cover_ranges_versions_and_states(self):
        self.fixtures = Fixtures(Path(self.temp.name), browse_stress=True)
        pages = [self.season(limit=50, offset=offset) for offset in (0, 50, 100)]
        self.assertEqual([len(page["episodes"]) for page in pages], [50, 50, 20])
        self.assertEqual([page["has_more"] for page in pages], [True, True, False])
        self.assertEqual([page["total"] for page in pages], [120, 120, 120])
        rows = [row for page in pages for row in page["episodes"]]
        self.assertEqual(len({row["id"] for row in rows}), 120)
        covered = {number for row in rows for number in range(row["episode"], (row["episode_end"] or row["episode"]) + 1)}
        self.assertEqual(covered, set(range(1, 121)))
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[205]["episode_end"], 6)
        self.assertEqual(by_id[205]["next_episode"]["id"], 207)
        self.assertGreater(len(by_id[207]["title"]), 24)
        self.assertEqual(by_id[203]["watched"], 1)
        self.assertEqual(by_id[208]["progress"]["position"], 135)
        self.assertFalse(by_id[212]["exists"])
        self.assertEqual((by_id[210]["version"], by_id[1210]["version"]), (1, 2))

    def test_stress_version_filter_and_pagination_match_counts(self):
        self.fixtures = Fixtures(Path(self.temp.name), browse_stress=True)
        result = self.season(version=2)
        self.assertEqual(result["total"], 1)
        self.assertEqual([row["id"] for row in result["episodes"]], [1210])
        self.assertEqual(result["next_episode"]["id"], 1210)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["versions"], [{"version": 1, "count": 119}, {"version": 2, "count": 1}])
        result = self.season(version=1, offset=100, limit=50)
        self.assertEqual(result["total"], 119)
        self.assertEqual(len(result["episodes"]), 19)
        self.assertFalse(result["has_more"])
        self.assertEqual(self.season(version=3)["total"], 0)

    def test_stress_watch_state_mutation_stays_in_memory(self):
        self.fixtures = Fixtures(Path(self.temp.name), browse_stress=True)
        self.fixtures.handle("POST", "/api/tv/shows/100/seasons/1/watched", {}, {"watched": True})
        self.assertEqual(self.season()["watched_count"], 120)
        self.assertIsNone(self.season()["next_episode"])
        self.fixtures.handle("POST", "/api/tv/shows/100/seasons/1/watched", {}, {"watched": False})
        self.assertEqual(self.season()["watched_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
