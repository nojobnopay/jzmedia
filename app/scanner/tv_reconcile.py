"""Conservative reconciliation of complete external TV directory snapshots."""
from collections import defaultdict
import os
import re

from .. import storage, store


def incomplete_filename(path):
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]
    return bool(re.search(r'\.(?:part(?:ial)?|tmp|download)(?:[-.][\w-]+)?$', name, re.I)
                or re.search(r'\.(?:part(?:ial)?|tmp|download)(?:[-.][\w-]+)?$', stem, re.I))


def _key(row):
    # A split-part file is not interchangeable with another part or a whole file.
    part = re.search(r'-part(\d+)$', os.path.splitext(os.path.basename(row['file_path']))[0], re.I)
    return (int(row['show_id']), int(row['season']), int(row['episode']),
            int(row.get('episode_end') or 0), int(part[1]) if part else None)


def _uncertain_part(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    markers = re.search(r'(?:^|[. _-])(?:part|cd|disc|disk)[. _-]?\d+(?:$|[. _-])', stem, re.I)
    return bool(markers and not re.search(r'-part\d+$', stem, re.I))


def reconcile_snapshot(backend, before, entries, resolve, *, roots, skip_dirs, should_stop=None):
    """Only one absent old file and one new exact episode may share an identity.

    Re-enumerate before committing: File Station does not participate in the
    application's locks or change journal. No video bytes are read or moved.
    """
    result = {'replaced': 0, 'removed': 0, 'changed': 0, 'review': 0,
              'issues': [], 'incomplete': False, 'replacements': [], 'retained_paths': [], 'by_show': {}}

    def note(show_id, field, count=1, issue=None):
        row = result['by_show'].setdefault(int(show_id), {'replaced': 0, 'removed': 0,
            'changed': 0, 'review': 0, 'issues': [], 'incomplete': False})
        row[field] += count
        if issue:
            row['issues'].append(issue)

    def stopped():
        return bool(should_stop and should_stop())

    def copying():
        from ..routers.fs.copy import active_copy_jobs
        return bool(active_copy_jobs(backend.library_id))

    def defer(message):
        result.update(incomplete=True, review=1, replaced=0, removed=0, by_show={}, issues=[])
        issue = {'code': 'files_changing', 'message': message}
        result['issues'].append(issue)
        for sid in {row['show_id'] for row in before}:
            note(sid, 'review', issue=issue)
            result['by_show'][int(sid)]['incomplete'] = True
        return result

    def signature(items):
        return sorted((e.rel, bool(e.is_dir), int(e.size) if not e.is_dir else 0,
                       float(e.mtime)) for e in items)

    if stopped():
        return defer('检查已取消，保留原文件记录，稍后重新检查')
    if copying():
        return defer('该视频库仍在复制文件，保留原记录，请在复制任务结束后重试')
    if any(float(e.mtime) <= 0 or (not e.is_dir and int(e.size) <= 0) for e in entries):
        return defer('部分文件尚未写完或无法读取属性，保留原记录，请在复制完成后重试')
    if any(not e.is_dir and incomplete_filename(e.rel) for e in entries):
        return defer('目录中存在临时下载或复制文件，保留原记录，请在文件落盘完成后重试')
    current = []
    try:
        storage.clear_meta_cache(backend.library_id)
        for root in roots:
            backend.stat(root)
            current.extend(backend.iter_tree(root, skip_dirs=skip_dirs))
        if stopped() or signature(entries) != signature(current):
            return defer('目录内容在检查期间发生变化，保留原记录，请在复制完成后重试')
    except storage.StorageError:
        return defer('目录暂不可用，保留原记录，连接恢复后重新检查')
    paths = {e.rel for e in entries if not e.is_dir}
    old_paths = {e['file_path'] for e in before}
    old_groups, new_groups, existing_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    uncertain_shows = set()
    for entry in entries:
        if entry.is_dir or entry.rel in old_paths:
            continue
        candidate = resolve(entry)
        if not candidate:
            continue
        if not candidate.get('ok') or incomplete_filename(entry.rel):
            sid = int(candidate['show_id'])
            uncertain_shows.add(sid)
            issue = {'code': 'file_numbering_review', 'show_id': sid,
                     'message': '新文件的季集编号或目录归属尚未确认，请核对文件名与归属',
                     'file': entry.rel}
            result['review'] += 1
            result['issues'].append(issue)
            note(sid, 'review', issue=issue)
            continue
        candidate = dict(candidate, file_path=entry.rel, entry=entry)
        new_groups[_key(candidate)].append(candidate)
    excluded = []
    for episode in before:
        path = episode['file_path']
        if any(segment.startswith('.') or segment in skip_dirs for segment in path.split('/')):
            excluded.append(path)
            continue
        if not any(not root or path.startswith(root.rstrip('/') + '/') for root in roots):
            continue
        (existing_groups if path in paths else old_groups)[_key(episode)].append(episode)
    replacements, removed, retained = [], [], []
    for key, absent in old_groups.items():
        candidates = new_groups.get(key, [])
        def related(other):
            return (other[0] == key[0] and other[1] == key[1]
                    and max(key[2], other[2]) <= min(max(key[2], key[3]), max(other[2], other[3])))
        overlap = any(related(other) for other in existing_groups) or any(
            other != key and related(other) for other in set(new_groups) | set(old_groups))
        safe = (len(absent) == len(candidates) == 1 and not existing_groups.get(key)
                and not overlap
                and key[0] not in uncertain_shows and not absent[0].get('binding_conflict')
                and absent[0].get('absolute_number') is None and not candidates[0].get('binding_conflict')
                and candidates[0].get('absolute_number') is None
                and not _uncertain_part(absent[0]['file_path'])
                and not _uncertain_part(candidates[0]['file_path']))
        if safe:
            replacements.append({'old': absent[0], 'new': candidates[0]['file_path']})
            note(key[0], 'replaced')
        elif candidates or overlap or existing_groups.get(key) or key[0] in uncertain_shows:
            retained.extend(absent)
            result['review'] += len(absent)
            issue = {'code': 'replacement_review', 'show_id': key[0],
                'message': f'S{key[1]:02d}E{key[2]:02d} 的新旧版本无法唯一对应，已保留旧记录与观看进度，请核对文件与编号'}
            result['issues'].append(issue)
            note(key[0], 'review', len(absent), issue)
        else:
            removed.extend(absent)
            note(key[0], 'removed', len(absent))
    if stopped():
        return defer('检查已取消，保留原文件记录，稍后重新检查')
    # A second snapshot can still race an external writer. Recheck every path
    # whose disappearance or new identity would change catalogue references.
    try:
        storage.clear_meta_cache(backend.library_id)
        for row in [p['old'] for p in replacements] + removed + retained:
            if stopped():
                return defer('检查已取消，保留原文件记录，稍后重新检查')
            if backend.exists(row['file_path']):
                return defer('旧文件重新出现，保留原记录，请重新检查目录')
        for pair in replacements:
            if stopped():
                return defer('检查已取消，保留原文件记录，稍后重新检查')
            entry = next(e for e in entries if e.rel == pair['new'])
            stat = backend.stat(entry.rel)
            if stat.is_dir or stat.size != entry.size or stat.mtime != entry.mtime:
                return defer('替换文件仍在变化，保留原记录，请在复制完成后重试')
        if stopped():
            return defer('检查已取消，保留原文件记录，稍后重新检查')
        if copying():
            return defer('该视频库仍在复制文件，保留原记录，请在复制任务结束后重试')
        applied = store.reconcile_tv_paths(backend.library_id, replacements, removed, retained)
    except (storage.StorageError, ValueError):
        return defer('文件或剧集归属发生变化，保留原记录，请重新检查')
    result.update(applied, replacements=replacements,
                  retained_paths=excluded + [row['file_path'] for row in retained])
    return result
