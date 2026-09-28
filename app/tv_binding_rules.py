"""Pure directory ownership/numbering rules, shared by preview and scanning."""
import posixpath
import re


def directory_path(value: str) -> str:
    raw = str(value or '').strip().replace('\\', '/')
    parts = raw.split('/')
    if (not raw or raw.startswith('/') or ':' in raw or '\x00' in raw
            or any(p in ('.', '..', '') for p in parts)):
        raise ValueError('请选择视频库内的具体目录')
    return posixpath.normpath(raw)


def contains(directory: str, path: str) -> bool:
    return path == directory or path.startswith(directory + '/')


def binding_for(path: str, rules: list[dict]) -> dict | None:
    return next((r for r in sorted(rules, key=lambda x: -len(x['path']))
                 if contains(r['path'], path)), None)


def bound_numbers(numbers: dict, rule: dict | None) -> dict:
    out = dict(numbers)
    out['binding_conflict'] = False
    if not rule or rule.get('season') is None or not out.get('ok'):
        return out
    target = int(rule['season'])
    explicit = out.get('explicit_season')
    # A seasonal directory can still contain Specials/OVA. Keep their explicit S00.
    if out.get('special') or explicit == 0:
        return out
    if explicit is not None and explicit != target and not rule.get('override_season'):
        out['binding_conflict'] = True
        return out
    out.update(season=target, absolute=None)
    return out


def release_identity(name: str) -> tuple[str, int | None]:
    """Search identity only; never strip a sequel number from the source directory."""
    s = re.sub(r'\{[^{}]+\}', ' ', name)
    year = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', s)
    y = int(year[1]) if year else None
    if year:
        s = s[:year.start()]
    s = re.split(r'(?i)(?:2160p|1080p|720p|WEB-DL|BluRay|HDTV)', s)[0]
    # Chinese + English release names: use the Chinese title for search, retaining
    # the original path as evidence in the UI.
    chinese = re.match(r'^([\u3400-\u9fff\d：:·之\s]+)', s)
    if chinese and re.search(r'[\u3400-\u9fff]', chinese[1]):
        s = chinese[1]
    return re.sub(r'[._]+', ' ', s).strip(' -()[]'), y


def season_suggestions(path: str, episodes: list[int], detail: dict) -> list[dict]:
    title, year = release_identity(posixpath.basename(path))
    key = re.sub(r'\W', '', title).casefold()
    out = []
    for season in detail.get('seasons') or []:
        sn = int(season.get('season_number') or 0)
        if sn <= 0:
            continue
        name = str(season.get('name') or '')
        skey = re.sub(r'\W', '', name).casefold()
        reasons, score = [], 0
        if len(skey) >= 2 and skey in key and not re.fullmatch(r'(?:season|第)?\d+季?', skey):
            reasons.append('目录副标题与季名称相符')
            score += 5
        if year and str(season.get('air_date') or '').startswith(str(year)):
            reasons.append('目录年份与本季播出年份相符')
            score += 3
        count = int(season.get('episode_count') or 0)
        if count and set(episodes) == set(range(1, count + 1)):
            reasons.append(f'集号覆盖本季 {count} 集（需核对剪辑版本）')
            score += 1
        if score:
            out.append({'season': sn, 'name': name, 'score': score, 'reasons': reasons})
    return sorted(out, key=lambda r: (-r['score'], r['season']))
