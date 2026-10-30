"""Private original images survive restart without escaping their budget or scope."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time

import pytest

from app.online import artwork_disk as disk


JPEG = b"\xff\xd8\xff\xe0\x00\x04JZ\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 17
WEBP = b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 4
RESULT = JPEG, "image/jpeg"
CHECK = lambda: None


def key(number=1, *, configuration="2" * 64):
    return ("src_" + "1" * 32, configuration,
            hashlib.sha256(f"https://private.example/{number}?token=private-value".encode()).digest())


@pytest.fixture
def cache(tmp_path):
    now = [1000.0]
    value = disk.ArtworkDiskCache(tmp_path / "online-artwork", clock=lambda: now[0])
    return value, now, tmp_path / "online-artwork"


def image_file(root):
    return next(root.glob("*.art"))


@pytest.mark.parametrize("limits", [{"capacity": 0}, {"capacity": 2049}, {"byte_budget": 0},
    {"byte_budget": 128 * 1024**2 + 1}, {"ttl": 0}, {"ttl": 86401}, {"ttl": float("nan")}])
def test_limits_are_bounded(tmp_path, limits):
    with pytest.raises(ValueError):
        disk.ArtworkDiskCache(tmp_path, **limits)


def test_original_bytes_private_metadata_and_complete_file_accounting(cache):
    value, _now, root = cache
    assert value.get(key(), check=CHECK) is None
    assert not root.exists(), "A first miss does not create an empty disk cache"
    assert value.put(key(), RESULT, check=CHECK)
    hit = value.get(key(), check=CHECK)
    assert hit.result == RESULT and hit.remaining == 86400
    file = image_file(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / ".lock").stat().st_mode) == 0o600
    assert len(file.name) == 68 and set(file.stem) <= set("0123456789abcdef")
    contents = file.read_bytes()
    assert contents.endswith(JPEG)
    assert all(secret not in contents for secret in (b"private.example", b"private-value", b"https://", b"src_"))
    assert value.stats() == {"entries": 1, "bytes": file.stat().st_size}
    assert value.stats()["bytes"] > len(JPEG), "Metadata is charged to the same capacity"


def test_new_instance_and_independent_process_can_restore_original_bytes(cache):
    value, now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    now[0] += 30
    recovered = disk.ArtworkDiskCache(root, clock=lambda: now[0])
    assert recovered.get(key(), check=CHECK).remaining == 86400 - 30
    code = """
import hashlib,json,sys
from pathlib import Path
from app.online.artwork_disk import ArtworkDiskCache
key=('src_'+'1'*32,'2'*64,hashlib.sha256(b'https://private.example/1?token=private-value').digest())
hit=ArtworkDiskCache(Path(sys.argv[1]),clock=lambda:1030).get(key,check=lambda:None)
print(json.dumps({'mime':hit.result[1],'sha256':hashlib.sha256(hit.result[0]).hexdigest(),'remaining':hit.remaining}))
"""
    result = subprocess.run([sys.executable, "-c", code, str(root)], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10, check=True)
    restored = json.loads(result.stdout)
    assert restored == {"mime": "image/jpeg", "sha256": hashlib.sha256(JPEG).hexdigest(), "remaining": 86370}


def test_reads_and_restarts_never_renew_the_absolute_lifetime(cache):
    value, now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    now[0] += 86399
    assert value.get(key(), check=CHECK).remaining == 1
    assert disk.ArtworkDiskCache(root, clock=lambda: now[0]).get(key(), check=CHECK).remaining == 1
    now[0] += 1
    assert value.get(key(), check=CHECK) is None
    assert value.stats() == {"bytes": 0, "entries": 0}
    assert not list(root.glob("*.art"))


def test_shorter_new_limits_and_backwards_clock_cannot_extend_existing_files(cache):
    value, now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    now[0] += 10
    assert disk.ArtworkDiskCache(root, ttl=20, clock=lambda: now[0]).get(key(), check=CHECK).remaining == 10
    now[0] -= 11
    assert value.get(key(), check=CHECK) is None


def test_url_source_and_complete_configuration_remain_distinct(cache):
    value, _now, _root = cache
    assert value.put(key(), RESULT, check=CHECK)
    assert value.get(key(2), check=CHECK) is None
    assert value.get(key(configuration="3" * 64), check=CHECK) is None
    other_source = ("src_" + "4" * 32, key()[1], key()[2])
    assert value.get(other_source, check=CHECK) is None
    assert value.get(("../outside", key()[1], key()[2]), check=CHECK) is None
    assert value.get(key(), check=CHECK).result == RESULT


def test_lru_survives_restart_and_budgets_include_headers(tmp_path):
    root = tmp_path / "cache"
    now = [1000.0]
    value = disk.ArtworkDiskCache(root, capacity=2, clock=lambda: now[0])
    assert value.put(key(1), RESULT, check=CHECK)
    now[0] += 1
    assert value.put(key(2), RESULT, check=CHECK)
    now[0] += 1
    assert value.get(key(1), check=CHECK)
    now[0] += 1
    recovered = disk.ArtworkDiskCache(root, capacity=2, clock=lambda: now[0])
    assert recovered.put(key(3), RESULT, check=CHECK)
    assert recovered.get(key(2), check=CHECK) is None
    assert recovered.get(key(1), check=CHECK) and recovered.get(key(3), check=CHECK)
    record_size = image_file(root).stat().st_size
    bounded = disk.ArtworkDiskCache(root, byte_budget=record_size + 1, clock=lambda: now[0])
    bounded.sweep(force=True)
    assert bounded.stats()["entries"] == 1
    assert sum(file.stat().st_size for file in root.iterdir()) <= record_size + 1


def test_single_byte_budget_never_creates_a_directory_or_partial_metadata(tmp_path):
    root = tmp_path / "absent"
    value = disk.ArtworkDiskCache(root, byte_budget=1)
    assert value.put(key(), RESULT, check=CHECK) is False
    assert not root.exists()
    assert value.stats() == {"bytes": 0, "entries": 0}


@pytest.mark.parametrize("result", [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")])
def test_only_bounded_validated_original_formats_are_retained(cache, result):
    value, _now, _root = cache
    assert value.put(key(), result, check=CHECK)
    assert value.get(key(), check=CHECK).result == result


@pytest.mark.parametrize("result", [(b"<svg/>", "image/svg+xml"), (JPEG, "image/png"),
    (b"", "image/jpeg"), (b"no image", None), (JPEG + b"x" * (512 * 1024), "image/jpeg")])
def test_invalid_or_oversized_images_do_not_leave_files(cache, result):
    value, _now, root = cache
    assert value.put(key(), result, check=CHECK) is False
    assert not root.exists()


@pytest.mark.parametrize("mutation", [lambda value: b"broken", lambda value: value[:-1],
    lambda value: value[:-1] + bytes([value[-1] ^ 1]), lambda value: value + b"trailing"])
def test_corrupt_container_length_and_body_digest_are_cache_misses(cache, mutation):
    value, _now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    file = image_file(root)
    file.write_bytes(mutation(file.read_bytes()))
    assert value.get(key(), check=CHECK) is None
    assert not file.exists()
    assert value.stats() == {"bytes": 0, "entries": 0}


def test_symlink_root_and_symlink_or_hardlink_entries_never_follow_outside_files(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(outside, target_is_directory=True)
    assert disk.ArtworkDiskCache(root_link).put(key(), RESULT, check=CHECK) is False
    assert list(outside.iterdir()) == []
    root = tmp_path / "cache"
    value = disk.ArtworkDiskCache(root)
    assert value.put(key(), RESULT, check=CHECK)
    file = image_file(root)
    original = outside / "preserved"
    original.write_bytes(file.read_bytes())
    original.chmod(0o600)
    file.unlink()
    file.symlink_to(original)
    assert value.get(key(), check=CHECK) is None
    assert original.read_bytes().endswith(JPEG) and not file.exists()
    os.link(original, file)
    assert value.get(key(), check=CHECK) is None
    assert original.read_bytes().endswith(JPEG) and not file.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO semantics need a POSIX filesystem")
def test_fifo_entries_and_lock_files_are_rejected_without_blocking(tmp_path):
    root = tmp_path / "cache"
    value = disk.ArtworkDiskCache(root)
    assert value.put(key(), RESULT, check=CHECK)
    file = image_file(root)
    file.unlink()
    os.mkfifo(file, mode=0o600)
    started = time.monotonic()
    assert value.get(key(), check=CHECK) is None
    assert time.monotonic() - started < 1
    (root / ".lock").unlink()
    os.mkfifo(root / ".lock", mode=0o600)
    started = time.monotonic()
    assert value.put(key(), RESULT, check=CHECK) is False
    assert time.monotonic() - started < 1


def test_foreign_files_and_existing_broadly_readable_roots_are_not_adopted_or_deleted(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    value = disk.ArtworkDiskCache(root)
    assert value.put(key(), RESULT, check=CHECK) is False
    root.chmod(0o700)
    foreign = root / "do-not-touch"
    foreign.write_bytes(b"foreign data")
    assert value.put(key(), RESULT, check=CHECK) is False
    value.clear()
    assert foreign.read_bytes() == b"foreign data"


def test_filesystem_read_write_and_unlink_failures_do_not_escape_or_break_the_budget(cache, monkeypatch):
    value, now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    real_read = disk.os.read
    with monkeypatch.context() as patch:
        patch.setattr(disk.os, "read", lambda *_args: (_ for _ in ()).throw(OSError("read failed")))
        assert value.get(key(), check=CHECK) is None
    assert disk.os.read is real_read
    with monkeypatch.context() as patch:
        patch.setattr(disk.os, "write", lambda *_args: (_ for _ in ()).throw(OSError("write failed")))
        assert value.put(key(), RESULT, check=CHECK) is False
    assert not list(root.glob("*.art")) and not list(root.glob(".*.tmp"))
    assert value.put(key(), RESULT, check=CHECK)
    bounded = disk.ArtworkDiskCache(root, capacity=1, clock=lambda: now[0])
    with monkeypatch.context() as patch:
        patch.setattr(disk.os, "unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("readonly")))
        assert bounded.put(key(2), RESULT, check=CHECK) is False
        assert bounded.stats()["entries"] == 1
    assert bounded.get(key(), check=CHECK).result == RESULT
    assert bounded.get(key(2), check=CHECK) is None


def test_permission_and_cancellation_callbacks_are_never_swallowed_as_disk_errors(cache, monkeypatch):
    value, _now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    read = disk.os.read
    observed = threading.Event()
    def checked_read(*args):
        result = read(*args)
        observed.set()
        return result
    def revoked():
        if observed.is_set():
            raise PermissionError("caller permission revoked")
    with monkeypatch.context() as patch:
        patch.setattr(disk.os, "read", checked_read)
        with pytest.raises(PermissionError, match="caller permission revoked"):
            value.get(key(), check=revoked)
    assert value.get(key(), check=CHECK).result == RESULT
    replace = disk.os.replace
    published = threading.Event()
    def checked_replace(*args, **kwargs):
        result = replace(*args, **kwargs)
        published.set()
        return result
    def cancelled():
        if published.is_set():
            raise RuntimeError("request cancelled")
    with monkeypatch.context() as patch:
        patch.setattr(disk.os, "replace", checked_replace)
        with pytest.raises(RuntimeError, match="request cancelled"):
            value.put(key(2), RESULT, check=cancelled)
    assert value.get(key(2), check=CHECK) is None
    assert not list(root.glob(".*.tmp"))


@pytest.mark.skipif(disk.fcntl is None, reason="Disk cache is disabled without POSIX advisory locks")
def test_maintenance_never_waits_for_an_active_thread_or_process_lock(cache):
    value, _now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    with value._lock:
        started = time.monotonic()
        value.sweep(force=True)
        assert value.stats()["entries"] == 1
        assert time.monotonic() - started < 1
    with (root / ".lock").open("rb") as held:
        disk.fcntl.flock(held, disk.fcntl.LOCK_EX | disk.fcntl.LOCK_NB)
        started = time.monotonic()
        value.sweep(force=True)
        assert value.stats()["entries"] == 1
        assert time.monotonic() - started < 1
        cancelled = threading.Event()
        entered = threading.Event()
        def check():
            entered.set()
            if cancelled.is_set():
                raise RuntimeError("cancelled while waiting for disk lock")
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(value.get, key(), check=check)
            assert entered.wait(1)
            cancelled.set()
            with pytest.raises(RuntimeError, match="cancelled while waiting"):
                future.result(timeout=1)


def test_maintenance_is_throttled_and_orphan_temporary_files_are_reaped(cache, monkeypatch):
    value, now, root = cache
    assert value.put(key(), RESULT, check=CHECK)
    calls = []
    scandir = disk.os.scandir
    def scan(*args):
        calls.append(True)
        return scandir(*args)
    monkeypatch.setattr(disk.os, "scandir", scan)
    value.sweep()
    assert not calls
    now[0] += 60
    value.sweep()
    assert len(calls) == 1
    temporary = root / ("." + "a" * 32 + ".tmp")
    temporary.write_bytes(b"interrupted private body")
    value.sweep(force=True)
    assert not temporary.exists()
    assert value.stats()["entries"] == 1


def test_default_location_tracks_the_live_data_directory_without_crossing_roots(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    monkeypatch.setattr(disk.settings, "data_dir", str(first))
    value = disk.ArtworkDiskCache()
    assert value.put(key(), RESULT, check=CHECK)
    monkeypatch.setattr(disk.settings, "data_dir", str(second))
    assert value.get(key(), check=CHECK) is None
    assert value.stats() == {"bytes": 0, "entries": 0}
    assert value.put(key(), (PNG, "image/png"), check=CHECK)
    assert value.get(key(), check=CHECK).result == (PNG, "image/png")
    monkeypatch.setattr(disk.settings, "data_dir", str(first))
    assert value.get(key(), check=CHECK).result == RESULT


def test_owned_mutations_invalidate_application_usage_and_clear_only_the_dedicated_cache(cache, monkeypatch):
    value, _now, root = cache
    calls = []
    monkeypatch.setattr(disk.data_usage, "invalidate", lambda: calls.append(True))
    assert value.put(key(), RESULT, check=CHECK)
    assert calls
    foreign = root.parent / "poster.jpg"
    foreign.write_bytes(JPEG)
    value.remove(key())
    assert value.stats() == {"bytes": 0, "entries": 0}
    assert value.put(key(), RESULT, check=CHECK)
    value.clear()
    assert value.stats() == {"bytes": 0, "entries": 0}
    assert foreign.read_bytes() == JPEG and (root / ".lock").exists()
