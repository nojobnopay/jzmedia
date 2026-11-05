"""Service-wide, user-retained episode packages and their durable work queue."""

import copy
import hashlib
import json
import shutil
import threading
import time
import uuid

from ..store import online as online_store
from ..store import online_cache as ledger
from . import cache_files as files
from .cache_download import Download, minimum_free
from .cache_files import CacheError
from .models import OnlinePlaybackIdentity


_mutation = threading.RLock()
_condition = threading.Condition()
_active = {}
_verified = {}
_usage_lock = threading.Lock()
_usage = {"key": None, "until": 0, "used_bytes": 0}
_thread = None
_stopping = False
_PUBLIC = ("identity", "label", "line_label", "downloaded_bytes", "total_bytes", "completed_resources",
           "total_resources", "speed_bytes_per_second", "error_code", "duration_seconds", "transport")


def _key(identity):
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def public_entry(row):
    return {"id": row["id"], "task_id": row["id"], "state": row["state"],
            **{key: row.get(key) for key in _PUBLIC}}


def _wake():
    with _condition:
        _condition.notify_all()


def _invalidate_usage():
    with _usage_lock:
        _usage["until"] = 0


def _verify_root(*, initialize=False):
    try:
        expected = ledger.root_identity()
        if expected is None and initialize:
            expected = ledger.root_identity(files.identity())
        files.verify_identity(expected)
        return expected
    except ValueError:
        raise CacheError("online_cache_storage_unavailable", 503) from None


def _poster(access, detail):
    path = detail.get("poster_path", "")
    prefix = "/api/online/catalog/artwork/"
    if not isinstance(path, str) or not path.startswith(prefix):
        return None
    try:
        from . import artwork
        source_id, token = path.removeprefix(prefix).split("/")
        body, mime = artwork.fetch(access, source_id, token)
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]
        return f"poster.{extension}", body
    except Exception:
        # A poster failure never prevents saving playable episodes.
        return None


def enqueue(source_id, item_id, line_id, episode_ids=None, *, access):
    """Refresh trusted details and snapshot one selected line, never client JSON."""
    from . import catalog
    item = online_store.get_online_item(item_id)
    if item is None or item["source_id"] != source_id:
        raise CacheError("online_cache_item_not_found", 404)
    if item["kind"] != "tv":
        raise CacheError("online_cache_episodes_only", 422)
    detail = catalog.detail(access, source_id, item["external_id"])
    lines = [line for line in detail["lines"] if line["id"] == line_id]
    if len(lines) != 1:
        raise CacheError("online_cache_line_not_found", 404)
    line = lines[0]
    supported = [entry for entry in line["entries"] if entry.get("supported") and entry.get("identity")]
    if episode_ids is not None:
        if not isinstance(episode_ids, list) or len(episode_ids) > 20000 or any(not isinstance(x, str) for x in episode_ids):
            raise CacheError("invalid_online_cache_request", 422)
        selected = set(episode_ids)
        chosen = [entry for entry in supported if entry["identity"].get("episode_id") in selected]
        if item["kind"] == "movie" and not selected:
            chosen = supported
        elif len(chosen) != len(selected):
            raise CacheError("online_cache_episode_not_found", 404)
    else:
        chosen = supported
    if not chosen:
        raise CacheError("online_cache_no_episodes", 422)
    poster = _poster(access, detail)
    result = []
    snapshot_at = time.time_ns()
    with _mutation:
        for existing in ledger.rows():
            files.verify_identity(existing.get("storage_identity"))
        storage_identity = _verify_root(initialize=True)
        detail_id = ledger.save_snapshot(detail)
        for entry in chosen:
            identity = OnlinePlaybackIdentity.model_validate(entry["identity"]).model_dump(mode="json", exclude_none=True)
            value = {"id": "oc_" + uuid.uuid4().hex, "identity": identity, "label": entry["label"],
                     "line_label": line["label"], "state": "queued", "detail": detail,
                     "external_id": item["external_id"], "source_name": detail["source"]["name"],
                     "downloaded_bytes": 0, "total_bytes": None, "completed_resources": 0,
                     "total_resources": 0, "speed_bytes_per_second": 0, "error_code": "",
                     "duration_seconds": None, "transport": entry.get("transport"), "package": {}}
            value["storage_identity"] = storage_identity
            value["snapshot_at"] = snapshot_at
            value["detail_id"] = detail_id
            row = ledger.insert(value, _key(identity))
            files.directory(row["id"], create=True)
            if poster is not None and not row.get("poster_name"):
                try:
                    files.atomic_bytes(row["id"], poster[0], poster[1])
                    row = ledger.update(row["id"], poster_name=poster[0])
                except CacheError:
                    pass
            result.append(public_entry(row))
    _wake()
    _invalidate_usage()
    return {"tasks": result}


def storage_info():
    # Progress polls do not rescan thousands of HLS files. Disk availability
    # and free space remain live; physical occupied-byte inventory lasts 15s.
    used = _usage["used_bytes"]
    try:
        expected = _verify_root()
        root = files.root()
        key = str(root), expected
        with _usage_lock:
            if _usage["key"] != key or time.monotonic() >= _usage["until"]:
                used = 0
                for row in ledger.rows():
                    try:
                        files.verify_identity(row.get("storage_identity"))
                        directory = files.directory(row["id"])
                        for entry in directory.iterdir():
                            used += files.path(row["id"], entry.name).stat().st_size
                    except (CacheError, OSError) as error:
                        if getattr(error, "category", "") == "online_cache_storage_unavailable":
                            raise
                _usage.update(key=key, until=time.monotonic() + 15, used_bytes=used)
            else:
                used = _usage["used_bytes"]
        disk = shutil.disk_usage(root)
    except (CacheError, OSError):
        return {"ok": False, "used_bytes": used, "available_bytes": None,
                "min_free_bytes": minimum_free(), "error_code": "online_cache_storage_unavailable"}
    return {"ok": True, "used_bytes": used, "available_bytes": disk.free, "total_bytes": disk.total,
            "min_free_bytes": minimum_free(), "error_code": ""}


def list_items():
    groups = {}
    for row in ledger.rows():
        item_id = row["identity"]["item_id"]
        if item_id not in groups:
            groups[item_id] = {"item_id": item_id, "source_id": row["identity"]["source_id"],
                               "external_id": row["external_id"], "title": row["detail"]["item"]["title"],
                               "source_name": row["source_name"], "poster_path": "", "ready_count": 0,
                               "total_count": 0, "entries": []}
        group = groups[item_id]
        group["entries"].append(public_entry(row))
        group["total_count"] += 1
        group["ready_count"] += int(row["state"] == "ready")
        if row.get("poster_name"):
            group["poster_path"] = f"/api/online/cache/artwork/{item_id}"
    return {"items": list(groups.values()), "storage": storage_info()}


def _snapshot_identity(value):
    if not isinstance(value, dict):
        return None
    return tuple(value.get(field) for field in ("source_id", "item_id", "kind", "line_id", "episode_id"))


def _restore_snapshot_member(current, previous, selected, identity):
    """Insert a missing member beside surviving exact identities, never labels."""
    keys = [identity(value) for value in current]
    selected_key = identity(selected)
    old_keys = [identity(value) for value in previous]
    position = old_keys.index(selected_key)
    # A surviving successor preserves runs of removed entries irrespective of
    # the order in which their independent cache tasks were created.
    for key in old_keys[position + 1:]:
        if key is not None and key in keys:
            current.insert(keys.index(key), copy.deepcopy(selected))
            return
    for key in reversed(old_keys[:position]):
        if key is not None and key in keys:
            current.insert(keys.index(key) + 1, copy.deepcopy(selected))
            return
    current.append(copy.deepcopy(selected))


def _retained_snapshot(rows):
    detail = copy.deepcopy(rows[-1]["detail"])
    lines = detail["lines"]
    entry_identity = lambda entry: _snapshot_identity(entry.get("identity"))
    present = {entry_identity(entry) for line in lines for entry in line["entries"]}
    for row in reversed(rows):
        identity = row["identity"]
        key = _snapshot_identity(identity)
        if (key in present or identity["source_id"] != detail["source"]["id"]
                or identity["item_id"] != detail["item"]["id"]):
            continue
        old_lines = row["detail"]["lines"]
        old_line = next((line for line in old_lines if line["id"] == identity.get("line_id")), None)
        if old_line is None:
            continue
        old_entry = next((entry for entry in old_line["entries"] if entry_identity(entry) == key), None)
        if old_entry is None:
            continue
        line = next((value for value in lines if value["id"] == old_line["id"]), None)
        if line is None:
            restored_line = {**old_line, "entries": []}
            _restore_snapshot_member(lines, old_lines, restored_line, lambda value: value["id"])
            line = next(value for value in lines if value["id"] == old_line["id"])
        _restore_snapshot_member(line["entries"], old_line["entries"], old_entry, entry_identity)
        present.add(key)
    return detail


def item_detail(item_id):
    rows = [row for row in ledger.rows() if row["identity"]["item_id"] == item_id]
    if not rows:
        raise CacheError("online_cache_item_not_found", 404)
    rows.sort(key=lambda row: row.get("snapshot_at", 0))
    detail = _retained_snapshot(rows)
    # Snapshot-only detail remains usable when the source is offline/disabled.
    detail["poster_path"] = f"/api/online/cache/artwork/{item_id}" if artwork_path(item_id) else ""
    return {"detail": detail, "entries": [public_entry(row) for row in rows], "external_id": rows[-1]["external_id"]}


def _ready(cache_id):
    row = ledger.get(cache_id)
    if row is None:
        raise CacheError("online_cache_not_found", 404)
    if row["state"] != "ready":
        raise CacheError("online_cache_not_ready")
    _verify_root()
    files.verify_identity(row.get("storage_identity"))
    return row


def mark_corrupt(cache_id, code="online_cache_corrupt"):
    with _mutation:
        row = ledger.get(cache_id)
        if row is not None and row["state"] != "deleting":
            ledger.update(cache_id, state="failed", error_code=code, speed_bytes_per_second=0)


def _validate(row, name):
    package = row.get("package") or {}
    record = package.get("records", {}).get(name)
    if name not in package.get("files", []) or record is None:
        raise CacheError("online_cache_corrupt")
    path = files.path(row["id"], name)
    try:
        info = path.stat()
        if info.st_size != record["size"] or info.st_mtime_ns != record["mtime_ns"]:
            raise CacheError("online_cache_corrupt")
        stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, record["sha256"])
        verification_key = row["id"], name
        with _mutation:
            cached = _verified.get(verification_key)
        stored_stamp = (record.get("dev"), record.get("ino"), record["size"], record["mtime_ns"],
                        record.get("ctime_ns"), record["sha256"])
        if cached != stamp and stored_stamp != stamp:
            actual = files.signature(row["id"], name)
            after = path.stat()
            after_stamp = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                           after.st_ctime_ns, record["sha256"])
            if (actual["sha256"] != record["sha256"] or actual["size"] != record["size"]
                    or after_stamp != stamp):
                raise CacheError("online_cache_corrupt")
        with _mutation:
            _verified[verification_key] = stamp
    except OSError:
        raise CacheError("online_cache_corrupt") from None
    return path


def lookup(identity):
    identity = OnlinePlaybackIdentity.model_validate(identity).model_dump(mode="json", exclude_none=True)
    row = next((candidate for candidate in ledger.rows() if _key(candidate["identity"]) == _key(identity)), None)
    if row is None:
        return None
    if row["state"] != "ready":
        if row.get("error_code") == "online_cache_corrupt":
            raise CacheError("online_cache_corrupt")
        return None
    try:
        package = package_files(row["id"])
    except CacheError as error:
        if error.category == "online_cache_storage_unavailable":
            raise
        mark_corrupt(row["id"])
        raise CacheError("online_cache_corrupt") from None
    return {**row, "cache_id": row["id"], "transport": package["transport"],
            "entry_name": package["entry_name"]}


def package_files(cache_id):
    row = _ready(cache_id)
    package = row.get("package") or {}
    names = package.get("files", [])
    if not names or package.get("entry_name") not in names or package.get("transport") not in {"mp4", "hls"}:
        raise CacheError("online_cache_corrupt")
    for name in names:
        _validate(row, name)
    return {"entry_name": package["entry_name"], "transport": package["transport"], "files": list(names)}


def asset_path(cache_id, name):
    row = _ready(cache_id)
    try:
        return _validate(row, name)
    except CacheError as error:
        if error.category == "online_cache_storage_unavailable":
            raise
        mark_corrupt(cache_id)
        raise CacheError("online_cache_corrupt") from None


def artwork_path(item_id):
    for row in ledger.rows():
        if row["identity"]["item_id"] == item_id and row.get("poster_name"):
            try:
                _verify_root()
                files.verify_identity(row.get("storage_identity"))
                return files.path(row["id"], row["poster_name"])
            except CacheError:
                continue
    return None


def _wait(work):
    if work is None:
        return
    work.abort()
    if not work.done.wait(30):
        raise CacheError("online_cache_busy", 503)


def _clear_invalid_package(row):
    _verify_root()
    files.verify_identity(row.get("storage_identity"))
    directory = files.directory(row["id"], create=True)
    for entry in directory.iterdir():
        if entry.name != row.get("poster_name"):
            files.path(row["id"], entry.name).unlink()
    return ledger.update(row["id"], package={}, downloaded_bytes=0, total_bytes=None,
                         completed_resources=0, total_resources=0)


def action(task_id, requested_action):
    if requested_action not in {"pause", "resume", "retry"}:
        raise CacheError("invalid_online_cache_action", 422)
    with _mutation:
        row = ledger.get(task_id)
        if row is None:
            raise CacheError("online_cache_not_found", 404)
        if row["state"] in {"ready", "deleting"}:
            raise CacheError("online_cache_action_unavailable")
        if requested_action == "pause":
            ledger.update(task_id, state="paused", speed_bytes_per_second=0, error_code="")
            with _condition:
                work = _active.get(task_id)
            _wait(work)
        else:
            if row["state"] == "downloading":
                return public_entry(row)
            if row.get("error_code") in {"online_cache_corrupt", "online_cache_probe_failed",
                                         "online_cache_duration_mismatch"}:
                row = _clear_invalid_package(row)
            ledger.update(task_id, state="queued", error_code="", speed_bytes_per_second=0)
        result = public_entry(ledger.get(task_id))
    _wake()
    return result


def delete(ids, *, before_delete=None):
    if (not isinstance(ids, list) or len(ids) > 20000
            or any(not isinstance(task_id, str) or not files._ID.fullmatch(task_id) for task_id in ids)):
        raise CacheError("invalid_online_cache_request", 422)
    results = []
    with _mutation:
        for task_id in dict.fromkeys(ids):
            try:
                row = ledger.get(task_id)
                if row is not None:
                    ledger.update(task_id, state="deleting", speed_bytes_per_second=0)
                    with _condition:
                        work = _active.get(task_id)
                    _wait(work)
                    if before_delete is not None:
                        before_delete(task_id)
                    _verify_root()
                    files.verify_identity(row.get("storage_identity"))
                    files.remove(task_id)
                    ledger.remove(task_id)
                    for key in [key for key in _verified if key[0] == task_id]:
                        _verified.pop(key, None)
                results.append({"id": task_id, "deleted": True, "error_code": ""})
            except (CacheError, OSError) as error:
                results.append({"id": task_id, "deleted": False,
                                "error_code": getattr(error, "category", "online_cache_delete_failed")})
    _invalidate_usage()
    return {"results": results}


def run_next():
    """Run one queued task synchronously; shared by the worker and isolated tests."""
    with _mutation:
        row = next((candidate for candidate in ledger.rows() if candidate["state"] == "queued"), None)
        if row is None:
            return False
        work = Download(row)
        with _condition:
            if _active or _stopping:
                return False
            _active[row["id"]] = work
        ledger.update(row["id"], state="downloading", error_code="", speed_bytes_per_second=0)
    try:
        work.run()
    except Exception as error:
        current = ledger.get(row["id"])
        if current is not None and current["state"] == "downloading":
            code = getattr(error, "category", "online_cache_download_failed")
            state = "paused" if code in {"online_cache_space_low", "online_cache_storage_unavailable"} else "failed"
            ledger.update(row["id"], expected_state="downloading", state=state,
                          error_code=code, speed_bytes_per_second=0)
    finally:
        with _condition:
            _active.pop(row["id"], None)
            work.done.set()
            _condition.notify_all()
    return True


def _worker():
    while True:
        with _condition:
            if _stopping:
                return
        try:
            ran = run_next()
        except Exception:
            ran = False
        if not ran:
            with _condition:
                if _stopping:
                    return
                _condition.wait(1)


def start():
    global _thread, _stopping
    with _mutation:
        if _thread is not None and _thread.is_alive():
            return
        _stopping = False
        ledger.recover()
        for row in ledger.rows():
            if row["state"] == "deleting":
                try:
                    files.verify_identity(row.get("storage_identity"))
                    files.remove(row["id"])
                    ledger.remove(row["id"])
                except (CacheError, OSError):
                    pass
        _thread = threading.Thread(target=_worker, name="online-cache", daemon=True)
        _thread.start()


def shutdown():
    global _thread, _stopping
    with _condition:
        _stopping = True
        active = tuple(_active.values())
        _condition.notify_all()
    # Keep unfinished tasks queued for restart, but never resurrect paused tasks.
    for work in active:
        row = ledger.get(work.id)
        if row is not None and row["state"] == "downloading":
            ledger.update(work.id, state="queued", speed_bytes_per_second=0)
        work.abort()
    thread = _thread
    if thread is not None:
        thread.join(timeout=35)
        if thread.is_alive():
            raise CacheError("online_cache_busy", 503)
    _thread = None
