# AGENTS.md

## Stack
- Backend: FastAPI + stdlib `sqlite3` (no ORM), Vue3 + Vite frontend. No tests, lint, typecheck, or CI.
- Entrypoints: `app/main.py` (app + SPA hosting), `app/store.py` (SQLite+FTS+facets/filters), `app/scanner.py` (scan/match flow), `app/tmdb.py` (TMDB client), `app/regions.py` (country→region mapping, single source), `app/routers/` (`health|movies|files|jobs|persons`), `app/nfo.py` (Kodi NFO).

## Run
- Backend (WSL dev, hot-reload via `docker-compose.override.yml`): `cp .env.example .env && mkdir -p sample_media/电影 data && docker compose up --build`, check `http://localhost:8080/docs`.
- Backend without docker: `pip install -r requirements.txt && uvicorn app.main:app --port 8080` (needs `MEDIA_ROOT`/`DATA_DIR` env or defaults `./sample_media`/`./data`).
- Frontend dev: `npm run dev` in `frontend/` (5173, proxies `/api`,`/posters` → 8080). Prod build: `npm run build` → `frontend/dist`, served by FastAPI at `/` + `/assets`; `/m/*` falls back to index.
- No single-test command — there are no tests. Verify via `/api/health` and the scan/list endpoints in README §5.

## Env / paths (gotchas)
- Config is `os.getenv` in `app/config.py`; compose sets `MEDIA_ROOT=/media`, `DATA_DIR=/app/data` inside container. In code always use `settings.media_root` / `settings.data_dir`, never host paths (`MEDIA_HOST_PATH` is compose-only volume mapping).
- `TMDB_PROXY` is runtime httpx proxy for API + poster download; `BUILD_HTTP_PROXY` is build-time only (pip/npm in Dockerfile/compose args). `TMDB_READ_TOKEN` (Bearer) takes precedence over `TMDB_API_KEY`.
- Data: `./data/jzmedia.db` + `./data/posters/` (`<tmdb_id>.jpg` posters, `person_<tmdb_id>.jpg` w185 avatars; both served at `/posters`); NFO `movie.nfo` written next to video. All gitignored — never commit.

## DB / FTS rule
- FTS5 `movies_fts` has **no triggers** — after any `movies`/`persons`/`movie_person` write you must call `store.resync_fts(movie_id)` (`update_movie_meta` already does; manual SQL or `link_person` does not). `store.init_db()` + `rebuild_fts()` run at startup and self-heal old trigger schemas.

## Conventions / constraints
- New GET pages: don't add bare `GET /...` routes — the catch-all `GET /{full_path:path}` in `main.py` serves the SPA. API routes must live under `/api` routers.
- `POST /api/jobs/douban-fetch` is intentionally `501` (no Douban scraping by default) — keep the stub.
- `POST /api/files/rename` 与 `POST /api/files/organize`（统一口，`mode=inplace|relocate`）默认 `dry_run:true`；总是先看预览。`organize mode=relocate` 用 `from_prefix/to_dir/group_by_region` 做两级 region 搬迁。命名模板 `标题 (年份)[-版本][-规格][-分卷][-版本N].ext`（单 `-` 直连）；冲突分 `suspect_mismatch`（等人工重匹配，不自动加后缀）与规格变体（最小后缀消解，兜底 `-版本N`）；执行时识别到的版本/编号落库（手工值优先）。`organize mode=relocate` 全量收敛：`from_prefix` 子树之外，已在目标根下但分区过期/未分区的行一并修正（目标已规范的行无计划）；就地模式父目录为片目录形态即视为已归档（防套娃，不碰顶层分区）。
- 花絮/样片：`scanner.is_sidecar` 判定（文件名关键词 + Plex 全集子目录；`scenes/other/shorts` 仅片内子目录生效）→ `extras` 表归属（标题/原标题+年份±1，样片永不归属）；整理时跟随进 `extras/` 子目录；`POST /api/files/clean-sidecars` 清历史脏行（删行留文件）；`unmatched` 另有 `suspect_title_high/info`（英文标题两档）与 `orphan_extras`。
- `PATCH /api/movies/{id}` allowlist only: `title, overview_override, douban_rating, custom_rating, tags, edition, spec` (ratings 0–10, tags must be list). Display logic: `overview_override` wins via `overview_display`.
- Filtering: `GET /api/movies` + `GET /api/search` accept repeatable `genre/region/country/year/decade/tag` (comma or repeated keys; declare as `list[str]|None = Query`) plus single `min_rating` + `rating_source` (`tmdb|douban|custom`, whitelisted to column in `store.RATING_SOURCES`, defaults tmdb). Semantics: OR within a facet, AND across facets — except `tag` multi-select is AND and `min_rating` is a single `>=` threshold. `country` matches any `origin_countries` entry (co-productions); `decade=2020` means 2020–2029. `GET /api/facets` returns live counts (only nonzero) incl. `ratings.{tmdb,douban,custom}` cumulative `[{min,count}]` for steps 9/8/7/6; `countries` uses involvement counting to match filter results.
- Frontend ratings: shared `src/ratings.js` (`hasScore` treats null/0 as missing — never render `-` placeholders) + `src/components/ScoreBadge.vue` (poster overlay; `♥` for custom, `★` otherwise). Display rule: hide any source with no score. Cast `character_name` is contributor free-text, not localized — only show it when `original_language` starts with `en` (EN descriptions/romaji for CJK films read as noise).
- Origin data: `movies` has `origin_country` (primary ISO) + `origin_countries` (JSON) + `original_language` + `region` (derived via `regions.resolve`: production_countries[0], fallback `original_language`; 华语=CN/HK/TW/MO). Never hardcode region lists elsewhere — edit `app/regions.py` only. Tags are normalized server-side (`regions.normalize_tags`: trim/dedup/cap 20 chars × 20).
- Persons/avatars: `persons.avatar` holds local rel path (or `'-'` = confirmed no TMDB photo, so backfill won't retry it). `upsert_person` only overwrites avatar when arg is not None. Person writes go through `scanner.sync_persons` (directors + top-10 cast, skips existing avatar files); `link_person` alone does not touch avatars or FTS.
- Person page: `GET /api/persons/{tmdb_id}` pure-local instant response (`store.get_person`: acting/directing split, dedup by tmdb, year desc; never blocks on TMDB). Bio progressively filled: frontend fires `POST /api/persons/{tmdb_id}/refresh` in background only when `biography` empty AND `bio_fetched_at==0`, then renders; cached via `update_person_bio` (zh empty → retry en-US; empty result still stamps `bio_fetched_at` as negative cache — no retry storm, no bulk bio backfill). Frontend route `/p/:tmdb_id`; person clicks use `tmdb_id`, never name search (avoids 同名混淆).
- Backfill: `scan_one` skips cached rows, so new meta fields need `POST /api/jobs/backfill-meta {"limit":N,"force":bool}` (no poster/NFO rewrite; preserves manual `title`; always syncs persons/avatars for processed rows, skips completed ones unless `force`). NFO now also writes `<country>` per origin.
- `scanner.scan_one`: cached `tmdb_id` → `skipped_cached`; `guessit type==episode` → `skipped_episode_v1` (V1 movies only); year match tolerance ±1; fallback-query hits set `needs_review=1`. Manual fix flow: `GET /api/tmdb/search` → `POST /api/movies/{id}/match {"tmdb_id":…}`.

## Deploy
- `docker-compose.override.yml` is WSL-only (dev user + bind mounts + `--reload`). Delete / exclude it on NAS; set NAS `.env` to `MEDIA_HOST_PATH=/volume1/video`, `DATA_HOST_PATH=/volume1/docker/jzmedia/data`, correct `UID/GID`.
- WSL Docker Desktop proxy breakage is documented in README §7 (use `crane pull … && docker load`, or `BUILD_HTTP_PROXY=http://nas:7890`).

## Versioning
- Git (`main` branch, local-only, no remote): commit per feature, annotated tag per release (`v0.4.0` = filters/ratings/avatar-wall/person page). Code versions unified at `0.4.0` (`main.py` + `package.json`).
- Images: `image: jzmedia:${APP_VERSION:-latest}` in compose (local `.env` pins e.g. `v0.4.0`); release = `GIT_SHA=$(git rev-parse --short HEAD) docker compose build` then `docker tag jzmedia:vX.Y.Z jzmedia:latest`. Version/commit baked via Dockerfile OCI labels (`APP_VERSION`/`GIT_SHA` args). `docker image prune` clears dangling rebuilds.
