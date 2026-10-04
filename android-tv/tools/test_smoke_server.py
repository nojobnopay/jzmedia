#!/usr/bin/env python3
"""Check synthetic search/browse contracts without FFmpeg, network or application imports.

Run: python android-tv/tools/test_smoke_server.py
These checks validate the isolated UI fixtures, not the production search engine.
"""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlencode

from smoke_server import Fixtures, Handler, poster_bytes


class MemoryConnection:
    """Exercise the real HTTP handler through in-memory streams, without a socket."""
    def __init__(self, request):
        self.request = request
        self.response = bytearray()

    def makefile(self, *_):
        return BytesIO(self.request)

    def sendall(self, data):
        self.response.extend(data)


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

    def actors(self, **params):
        return self.request("/api/tv-client/actors", **params)

    def actor_works(self, **params):
        return self.request("/api/tv-client/actor-works", **params)

    def http_get(self, path):
        connection = MemoryConnection(f"GET {path} HTTP/1.0\r\nHost: fixture\r\n\r\n".encode("ascii"))
        Handler(connection, ("127.0.0.1", 0), SimpleNamespace(fixtures=self.fixtures))
        head, _, body = bytes(connection.response).partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1").split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines[1:])
        self.assertEqual(int(headers["Content-Length"]), len(body))
        return int(lines[0].split()[1]), headers, body

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

    def test_card_metadata_uses_real_library_and_rating_fields_across_routes(self):
        libraries = {row["id"]: row["name"] for row in self.request("/api/media-libraries")["items"]}
        rows = [self.request("/api/movies")["items"][0], self.request("/api/tv/shows")["items"][0],
                self.request("/api/movies/recent-played")["items"][0], self.request("/api/tv/recent-played")["items"][0],
                self.search(q="SQ")["items"][0], self.search(q="YYDDT")["items"][0],
                self.actor_works(actor="tmdb:9001")["items"][0]]
        for row in rows:
            with self.subTest(item=row["id"]):
                self.assertEqual(row["media_library_name"], libraries[row["media_library_id"]])
                self.assertGreater(row["tmdb_rating"], 0)
                self.assertNotIn("vote_average", row)
        collection = self.search(q="KHSG")["items"][0]
        self.assertEqual(collection["media_library_name"], libraries[collection["media_library_id"]])
        self.assertNotIn("tmdb_rating", collection)

    def test_card_stress_preserves_same_movie_in_distinct_libraries(self):
        self.fixtures = Fixtures(Path(self.temp.name), card_stress=True)
        rows = self.search(q="XJHC44")["items"]
        self.assertEqual([row["id"] for row in rows], [44, 45])
        for field in ("title", "year", "poster_path", "tmdb_rating"):
            self.assertEqual(rows[0][field], rows[1][field])
        self.assertEqual({row["media_library_id"] for row in rows}, {1, 2})
        self.assertNotEqual(rows[0]["media_library_name"], rows[1]["media_library_name"])
        self.assertGreater(len(rows[1]["media_library_name"]), 16)
        for library_id, movie_id in ((1, 44), (2, 45)):
            self.assertEqual([row["id"] for row in self.search(q="XJHC44", media_library=library_id)["items"]], [movie_id])
        browse = self.request("/api/movies", limit=6)["items"]
        self.assertEqual([row["id"] for row in browse], [46, 45, 44, 43, 42, 41])
        self.assertGreater(len(browse[3]["title"]), 20)
        self.assertIsNone(browse[4]["tmdb_rating"])
        self.assertEqual(browse[5]["tmdb_rating"], 0)

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

    def test_actor_candidates_match_initials_full_pinyin_and_names(self):
        for query in ("ZXH", "zxh", "ＺＸＨ", "周"):
            with self.subTest(query=query):
                result = self.actors(q=query)
                self.assertEqual([row["name"] for row in result["items"]], ["周星河", "周晓河"])
                self.assertEqual(result["total"], 2)
        for query in ("zhouxinghe", "周星河"):
            self.assertEqual(self.actors(q=query)["items"][0]["key"], "tmdb:9001")
            self.assertEqual(self.actors(q=query)["total"], 1)
        self.assertEqual(self.actors(q="zhouxiaohe")["items"][0]["key"], "tmdb:9002")
        self.assertEqual(self.actors(q="nonexistent")["total"], 0)

    def test_empty_actor_query_browses_paginated_local_people(self):
        first = self.actors(limit=24)
        second = self.actors(q="", limit=24, offset=24)
        self.assertEqual((first["total"], second["total"]), (37, 37))
        self.assertEqual((len(first["items"]), len(second["items"])), (24, 13))
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))
        self.assertEqual(len({row["key"] for row in first["items"] + second["items"]}), 37)
        self.assertEqual(self.actors(q="---")["total"], 0)

    def test_actor_counts_follow_current_library_scope(self):
        all_libraries = self.actors(q="zhouxinghe")["items"][0]
        first_library = self.actors(q="zhouxinghe", media_library=1)["items"][0]
        second_library = self.actors(q="zhouxinghe", media_library=2)["items"][0]
        self.assertEqual((all_libraries["movie_count"], all_libraries["show_count"], all_libraries["work_count"]), (32, 1, 33))
        self.assertEqual((first_library["movie_count"], first_library["show_count"], first_library["work_count"]), (31, 1, 32))
        self.assertEqual((second_library["movie_count"], second_library["show_count"], second_library["work_count"]), (1, 0, 1))
        self.assertEqual(self.actors(media_library=1)["total"], 36)
        self.assertEqual({row["name"] for row in self.actors(media_library=2)["items"]}, {"周星河", "顾远舟"})
        self.assertEqual(self.actors(q="GYZ", media_library=1)["items"], [])
        self.assertEqual(self.actors(media_library=999)["total"], 0)

    def test_actor_name_identity_and_missing_avatar_tv_only(self):
        actor = self.actors(q="LWT")["items"][0]
        self.assertEqual(actor["key"], "name:林雾汀")
        self.assertEqual(actor["avatar_path"], "")
        self.assertEqual((actor["movie_count"], actor["show_count"]), (0, 1))
        works = self.actor_works(actor=actor["key"])
        self.assertEqual([(row["kind"], row["id"]) for row in works["items"]], [("show", 100)])
        self.assertEqual(self.actor_works(actor=actor["key"], kind="movie")["total"], 0)
        self.assertEqual(self.actor_works(actor=actor["key"], kind="movie")["actor"], actor)

    def test_same_name_does_not_merge_different_actor_keys(self):
        people = self.actors(q="HCTMYY")["items"]
        self.assertEqual({row["key"] for row in people}, {"tmdb:9201", "tmdb:9202", "name:合成同名演员"})
        self.assertEqual({row["name"] for row in people}, {"合成同名演员"})
        work_ids = {self.actor_works(actor=person["key"])["items"][0]["id"] for person in people}
        self.assertEqual(work_ids, {1, 2, 3})

    def test_actor_work_pages_deduplicate_movie_versions_and_keep_types(self):
        first = self.actor_works(actor="tmdb:9001", limit=24)
        second = self.actor_works(actor="tmdb:9001", limit=24, offset=24)
        self.assertEqual((first["total"], second["total"]), (33, 33))
        self.assertEqual((len(first["items"]), len(second["items"])), (24, 9))
        self.assertEqual((first["has_more"], second["has_more"]), (True, False))
        self.assertEqual(first["actor"], second["actor"])
        self.assertEqual(first["items"][0]["version_count"], 2)
        identities = [(row["kind"], row["id"]) for row in first["items"] + second["items"]]
        self.assertEqual(len(set(identities)), 33)
        self.assertIn(("movie", 46), identities)
        self.assertIn(("show", 100), identities)
        self.assertEqual(self.actor_works(actor="tmdb:9001", kind="movie")["total"], 32)
        self.assertEqual(self.actor_works(actor="tmdb:9001", kind="show")["total"], 1)
        scoped = self.actor_works(actor="tmdb:9001", media_library=2)
        self.assertEqual([row["id"] for row in scoped["items"]], [45])
        self.assertEqual(scoped["actor"]["work_count"], 1)

    def test_actor_work_missing_or_outside_scope_is_not_found(self):
        for params in ({"actor": "tmdb:1"}, {"actor": "name:不存在"}, {"actor": "tmdb:9004", "media_library": 1},
                       {"actor": "name:林雾汀", "media_library": 2}):
            with self.subTest(params=params), self.assertRaises(KeyError):
                self.actor_works(**params)

    def test_actor_parameter_validation_and_feature_flag(self):
        for params in ({"limit": 0}, {"limit": 61}, {"offset": -1}, {"q": "x" * 81}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.actors(**params)
        with self.assertRaises(ValueError):
            self.actor_works(actor="tmdb:9001", kind="collection")
        self.assertIn("tv_actor_search", self.request("/api/stream/client-info")["features"])

    def test_actor_work_details_show_consistent_synthetic_cast(self):
        movie = self.request("/api/movies/46")
        self.assertEqual({row["name"] for row in movie["persons"]}, {"周星河", "周晓河"})
        show = self.request("/api/tv/shows/100")
        self.assertEqual({row["name"] for row in show["cast"]}, {"周星河", "周晓河", "林雾汀"})
        anonymous = next(row for row in show["cast"] if row["name"] == "林雾汀")
        self.assertIsNone(anonymous["tmdb_id"])

    def test_season_stress_exposes_real_summary_fields_and_varied_states(self):
        self.fixtures = Fixtures(Path(self.temp.name), season_stress=True)
        show = self.request("/api/tv/shows/100")
        seasons = show["seasons"]
        self.assertEqual([row["season"] for row in seasons], list(range(8)))
        self.assertEqual(show["season_count"], 8)
        self.assertEqual((show["episode_count"], show["watched_count"]), (75, 45))
        self.assertEqual(seasons[0]["name"], "特别篇")
        self.assertGreater(len(seasons[2]["name"]), 20)
        self.assertEqual(seasons[3]["name"], "")
        self.assertEqual(seasons[7]["name"], "")
        self.assertTrue(seasons[0]["done"])
        self.assertTrue(seasons[4]["done"])
        self.assertTrue(seasons[5]["has_partial"])
        self.assertEqual((seasons[5]["total"], seasons[5]["watched_count"], seasons[5]["next_episode_num"]), (6, 2, 3))
        self.assertEqual(seasons[3]["watched_count"], 0)
        expected_fields = {"season", "episode_count", "total", "watched_count", "distinct", "versions", "done",
                           "has_partial", "next_episode_num", "name", "overview", "air_date", "poster_path", "cast"}
        for row in seasons:
            self.assertEqual(set(row), expected_fields)

    def test_season_stress_detail_episode_and_next_routes_keep_season_identity(self):
        self.fixtures = Fixtures(Path(self.temp.name), season_stress=True)
        identities = set()
        for summary in self.request("/api/tv/shows/100")["seasons"]:
            number = summary["season"]
            detail = self.request(f"/api/tv/shows/100/seasons/{number}")
            self.assertEqual((detail["season"], detail["name"]), (number, summary["name"]))
            self.assertEqual((detail["total"], detail["watched_count"]), (summary["total"], summary["watched_count"]))
            self.assertEqual(detail["versions"], [{"version": 1, "count": detail["total"]}])
            for expected in detail["episodes"]:
                row = self.request(f"/api/tv/episodes/{expected['id']}")
                self.assertEqual(row["season"], number)
                self.assertEqual(row["episode"], expected["episode"])
                self.assertIn(f"/S{number:02d}E", row["file_path"])
                self.assertNotIn(row["id"], identities)
                identities.add(row["id"])
                next_row = self.request(f"/api/tv/episodes/{row['id']}/next")["next"]
                if next_row is not None:
                    self.assertEqual((next_row["season"], next_row["episode"]), (number, row["episode"] + 1))
            self.assertIsNone(detail["episodes"][-1]["next_episode"])
        self.assertEqual(len(identities), 75)

    def test_season_stress_paging_and_watch_mutations_are_scoped_to_chosen_season(self):
        self.fixtures = Fixtures(Path(self.temp.name), season_stress=True)
        pages = [self.request("/api/tv/shows/100/seasons/6", limit=10, offset=offset) for offset in (0, 10, 20)]
        self.assertEqual([len(row["episodes"]) for row in pages], [10, 10, 4])
        self.assertEqual([row["has_more"] for row in pages], [True, True, False])
        self.assertEqual([row["total"] for row in pages], [24, 24, 24])
        self.assertEqual(self.request("/api/tv/shows/100/seasons/6", version=2)["total"], 0)
        self.fixtures.handle("POST", "/api/tv/shows/100/seasons/3/watched", {}, {"watched": True})
        seasons = self.request("/api/tv/shows/100")["seasons"]
        self.assertEqual((seasons[3]["watched_count"], seasons[2]["watched_count"]), (8, 4))
        self.fixtures.handle("POST", "/api/tv/shows/100/seasons/0/watched", {}, {"watched": False})
        self.assertEqual(self.request("/api/tv/shows/100/seasons/0")["watched_count"], 0)
        self.fixtures.handle("POST", "/api/tv/shows/100/watched", {}, {"watched": True})
        self.assertEqual(self.request("/api/tv/shows/100")["watched_count"], 75)

    def test_season_and_episode_stress_can_combine_without_changing_actor_relations(self):
        self.fixtures = Fixtures(Path(self.temp.name), browse_stress=True, season_stress=True)
        show = self.request("/api/tv/shows/100")
        self.assertEqual((show["episode_count"], show["watched_count"]), (192, 48))
        first = self.season()
        self.assertEqual(first["total"], 120)
        self.assertEqual(first["versions"], [{"version": 1, "count": 119}, {"version": 2, "count": 1}])
        self.assertEqual(self.request("/api/tv/shows/100/seasons/2")["total"], 12)
        self.assertEqual(self.request("/api/tv/episodes/1210")["season"], 1)
        self.assertEqual(self.actor_works(actor="name:林雾汀")["total"], 1)
        self.assertEqual(self.actor_works(actor="tmdb:9001")["actor"]["work_count"], 33)

    def test_extra_seasons_require_flag_and_unknown_seasons_are_not_found(self):
        with self.assertRaises(KeyError):
            self.request("/api/tv/shows/100/seasons/0")
        self.fixtures = Fixtures(Path(self.temp.name), season_stress=True)
        with self.assertRaises(KeyError):
            self.request("/api/tv/shows/100/seasons/8")
        with self.assertRaises(KeyError):
            self.request("/api/tv/episodes/10003")

    def test_season_poster_paths_match_detail_and_keep_four_original_images(self):
        self.fixtures = Fixtures(Path(self.temp.name), season_stress=True)
        show = self.request("/api/tv/shows/100")
        paths = {row["season"]: row["poster_path"] for row in show["seasons"]}
        self.assertEqual(paths, {0: "tv/poster0.png", 1: "tv/poster2.png", 2: "tv/poster1.png", 3: "",
                                 4: "tv/missing-season.png", 5: "tv/invalid-season.png", 6: "tv/poster3.png", 7: ""})
        self.assertEqual(show["poster_path"], "tv/poster2.png")
        for season, path in paths.items():
            detail = self.request(f"/api/tv/shows/100/seasons/{season}")
            self.assertEqual(detail["poster_path"], path)
            self.assertEqual(detail["show_poster"], show["poster_path"])

    def test_season_artwork_http_status_and_bytes_cover_load_and_decode_failures(self):
        # File responses use only the four original PNGs; no FFmpeg or network needed.
        self.fixtures = Fixtures(Path(self.temp.name), season_stress=True)
        images = []
        for index in range(4):
            expected = poster_bytes(index)
            (Path(self.temp.name) / f"poster{index}.png").write_bytes(expected)
            status, headers, body = self.http_get(f"/posters/tv/poster{index}.png")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(body, expected)
            images.append(body)
        self.assertEqual(len(set(images)), 4)
        status, _, body = self.http_get("/posters/tv/missing-season.png")
        self.assertEqual(status, 404)
        self.assertIn("detail", json.loads(body))
        status, headers, body = self.http_get("/posters/tv/invalid-season.png")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body), {"synthetic": True, "fixture": "not-an-image"})
        self.fixtures = Fixtures(Path(self.temp.name))
        self.assertEqual(self.http_get("/posters/tv/invalid-season.png")[0], 404)

    def test_no_show_poster_removes_fallback_consistently_without_changing_season_art(self):
        with_art = Fixtures(Path(self.temp.name), season_stress=True)
        self.fixtures = Fixtures(Path(self.temp.name), season_stress=True, no_show_poster=True)
        show = self.request("/api/tv/shows/100")
        self.assertEqual(show["poster_path"], "")
        self.assertEqual(show["seasons"], with_art.show()["seasons"])
        self.assertEqual(show["next_episode"]["show_poster"], "")
        for season in range(8):
            detail = self.request(f"/api/tv/shows/100/seasons/{season}")
            self.assertEqual(detail["show_poster"], "")
            self.assertTrue(all(row["show_poster"] == row["poster_path"] == "" for row in detail["episodes"]))
        self.assertEqual(self.request("/api/tv/episodes/201")["show_poster"], "")
        self.assertEqual(self.request("/api/tv/shows")["items"][0]["poster_path"], "")
        self.assertEqual(self.request("/api/tv/recent-played")["items"][0]["poster_path"], "")
        self.assertEqual(self.search(q="YYDDT")["items"][0]["poster_path"], "")
        self.assertEqual(self.actor_works(actor="name:林雾汀")["items"][0]["poster_path"], "")
        self.assertEqual(self.fixtures.movie(1), with_art.movie(1))

    def test_season_poster_stress_preserves_first_season_playback_fixtures(self):
        for browse_stress in (False, True):
            with self.subTest(browse_stress=browse_stress):
                baseline = Fixtures(Path(self.temp.name), browse_stress=browse_stress)
                self.fixtures = Fixtures(Path(self.temp.name), browse_stress=browse_stress, season_stress=True)
                expected = baseline.handle("GET", "/api/tv/shows/100/seasons/1", {}, {})
                self.assertEqual(self.season(), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
