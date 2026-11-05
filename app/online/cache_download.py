"""Bounded persistent VOD downloads through the application's guarded client."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

from ..network.client import create_online_client
from ..network.guard import NetworkOperation, operation_context
from ..network.policy import resolve_policy_state
from ..store import online_cache as ledger
from ..store.online_network import load_online_network_state, online_network_fingerprint
from . import hls, providers
from .cache_files import CacheError
from . import cache_files as files


CHUNK = 64 * 1024
MP4_WINDOW = 8 * 1024**2
MAX_RESOURCE = 32 * 1024**2
_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)\Z")
_STRONG_ETAG = re.compile(r'"[\x21\x23-\x7e\x80-\xff]*"\Z')


def minimum_free():
    try:
        value = float(os.getenv("ONLINE_CACHE_MIN_FREE_GB", "1"))
        if not 0 <= value <= 1024 * 1024:
            raise ValueError
        return int(value * 1024**3)
    except (ValueError, OverflowError):
        return 1024**3


_disk_lock = threading.Lock()


def write_block(stream, data):
    with _disk_lock:
        if shutil.disk_usage(files.root()).free - len(data) < minimum_free():
            raise CacheError("online_cache_space_low")
        try:
            stream.write(data)
        except OSError as error:
            if error.errno in {errno.ENOSPC, errno.EDQUOT}:
                raise CacheError("online_cache_space_low") from None
            raise CacheError("online_cache_storage_unavailable", 503) from None


class Download:
    """One task, at most four resource readers, with independently cancellable I/O."""

    def __init__(self, task):
        self.task = task
        self.id = task["id"]
        self.cancelled = threading.Event()
        self.resource_cancelled = threading.Event()
        self.done = threading.Event()
        self.lock = threading.RLock()
        self.operations = set()
        self.package = dict(task.get("package") or {})
        self.records = dict(self.package.get("records") or {})
        self.current = {}
        self.lengths = {}
        self.resource_names = set()
        self.started = time.monotonic()
        self.transferred = 0
        self.last_publish = 0
        self.last_policy = 0
        self.policy = None
        self.client = None
        self.subtitle_client = None
        self.header_policy = None
        self.storage_identity = ledger.root_identity() or task.get("storage_identity")

    def abort(self):
        self.cancelled.set()
        self.abort_resources()

    def abort_resources(self):
        self.resource_cancelled.set()
        with self.lock:
            operations = tuple(self.operations)
        for operation in operations:
            operation.abort()

    def check(self, *, force=False):
        if self.cancelled.is_set() or self.resource_cancelled.is_set():
            raise CacheError("online_cache_cancelled")
        files.verify_identity(self.storage_identity)
        now = time.monotonic()
        with self.lock:
            if self.policy is None or not force and now - self.last_policy < 1:
                return
            self.last_policy = now
        state = load_online_network_state(self.task["identity"]["source_id"])
        if not state["online_enabled"] or not state["source_config"]["enabled"]:
            raise CacheError("online_source_disabled", 503)
        if online_network_fingerprint(state) != self.policy:
            raise CacheError("online_source_changed")

    @contextmanager
    def operation(self, seconds=120):
        self.check(force=True)
        operation = NetworkOperation(time.monotonic() + seconds)
        with self.lock:
            self.operations.add(operation)
        try:
            self.check()
            with operation_context(operation):
                yield operation
        finally:
            operation.close()
            with self.lock:
                self.operations.discard(operation)

    def publish(self, *, force=False):
        with self.lock:
            now = time.monotonic()
            if not force and now - self.last_publish < .5:
                return
            self.last_publish = now
            downloaded = sum(self.current.get(name, self.records.get(name, {}).get("size", 0))
                             for name in self.resource_names)
            known = all(name in self.lengths or name in self.records for name in self.resource_names)
            total = (sum(self.lengths.get(name, self.records.get(name, {}).get("size", 0))
                         for name in self.resource_names) if known else None)
            self.package["records"] = dict(self.records)
            ledger.update(self.id, package=dict(self.package), downloaded_bytes=downloaded, total_bytes=total,
                          total_resources=len(self.resource_names),
                          completed_resources=sum(name in self.records for name in self.resource_names),
                          speed_bytes_per_second=self.transferred / max(.1, now - self.started))

    def progress(self, name, amount, *, length=None, transferred=0):
        with self.lock:
            self.current[name] = amount
            self.transferred += transferred
            if length is not None:
                self.lengths[name] = length
        self.publish()

    def record(self, name, *, upstream=None):
        self.check()
        record = files.signature(self.id, name)
        if upstream is not None:
            record["upstream"] = upstream
        with self.lock:
            self.records[name] = record
            self.current.pop(name, None)
        self.publish(force=True)

    def complete(self, name):
        record = self.records.get(name)
        if record is None:
            return False
        try:
            actual = files.signature(self.id, name)
        except CacheError:
            return False
        if actual["size"] == record["size"] and actual["sha256"] == record["sha256"]:
            self.records[name] = {**record, **actual}
            return True
        return False

    def read_manifest(self, url, purpose="media"):
        for attempt in range(3):
            try:
                return self._read_manifest(url, purpose)
            except CacheError:
                raise
            except Exception:
                if attempt == 2:
                    raise CacheError("online_cache_network_failed", 502) from None
                if self.cancelled.wait(.5 * (2**attempt)):
                    self.check()

    def _read_manifest(self, url, purpose):
        with self.operation() as operation:
            with self.reader(purpose).guarded_stream("GET", url, operation=operation) as response:
                if response.status_code in {401, 403}:
                    raise CacheError("online_cache_url_expired", 502)
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise CacheError("online_cache_manifest_failed", 502)
                body = bytearray()
                for block in response.iter_raw():
                    self.check()
                    body.extend(block)
                    if len(body) > hls.MANIFEST_BYTES:
                        raise CacheError("hls_manifest_size_limit", 422)
                length = response.headers.get("content-length")
                if length is not None and (not length.isdigit() or int(length) != len(body)):
                    raise CacheError("online_cache_length_mismatch", 502)
                return bytes(body), str(response.url)

    def build_hls(self, entry_url):
        inventory, pending, manifests = {}, [], {}
        signature = hashlib.sha256()
        durations = []

        current_purpose = "media"

        def register(url, *, kind, byte_range=None, purpose=None):
            purpose = purpose or current_purpose
            key = (url, kind, byte_range, purpose)
            if key in inventory:
                return inventory[key]["name"]
            # The entry manifest also occupies one ordinary playback resource.
            if len(inventory) >= hls.REFERENCE_LIMIT - 1:
                raise CacheError("hls_reference_limit", 422)
            suffix = urlsplit(url).path.rsplit(".", 1)[-1].lower()
            if kind == "manifest":
                suffix = "m3u8"
            elif kind == "map":
                suffix = "mp4"
            elif suffix not in {"ts", "m4s", "mp4", "vtt", "aac", "mp3", "ac3", "ec3"}:
                suffix = "ts"
            resource = {"name": f"resource_{len(inventory):05d}.{suffix}", "url": url,
                        "kind": kind, "byte_range": byte_range, "purpose": purpose}
            inventory[key] = resource
            if kind == "manifest":
                pending.append((resource, 1))
            return resource["name"]

        main = {"name": "entry.m3u8", "url": entry_url, "kind": "manifest"}
        pending.append((main, 0))
        seen = set()
        while pending:
            resource, depth = pending.pop(0)
            if resource["url"] in seen:
                raise CacheError("online_cache_manifest_cycle", 422)
            if depth > 8:
                raise CacheError("online_cache_manifest_depth", 422)
            seen.add(resource["url"])
            current_purpose = resource.get("purpose", "media")
            body, base = self.read_manifest(resource["url"], current_purpose)
            signature.update(resource["url"].encode())
            signature.update(body)
            previous = len(pending)
            result = hls.offline_manifest(body, base, register)
            pending[previous:] = [(child, depth + 1) for child, _old_depth in pending[previous:]]
            manifests[resource["name"]] = result["body"]
            if result["duration_seconds"] is not None:
                durations.append(result["duration_seconds"])
        resources = [value for value in inventory.values() if value["kind"] != "manifest"]
        if not resources:
            raise CacheError("online_cache_empty", 422)
        return manifests, resources, signature.hexdigest(), max(durations, default=None)

    @staticmethod
    def resource_checkpoint(resource, response, *, requested_range=None):
        """Strong origin identity, distinct from the local fragment's checksum."""
        etag = response.headers.get("etag", "")
        length = response.headers.get("content-length", "")
        if (not _STRONG_ETAG.fullmatch(etag) or not length.isdigit() or int(length) <= 0
                or response.headers.get("content-encoding", "identity") != "identity"):
            return None
        total = int(length)
        if response.status_code == 206:
            match = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
            if (match is None or tuple(map(int, match.groups()[:2])) != requested_range
                    or int(match[2]) - int(match[1]) + 1 != total or int(match[3]) <= int(match[2])):
                return None
            total = int(match[3])
        elif response.status_code != 200:
            return None
        byte_range = resource.get("byte_range")
        return {"url_hash": hashlib.sha256(resource["url"].encode()).hexdigest(),
                "response_url_hash": hashlib.sha256(str(response.url).encode()).hexdigest(),
                "etag": etag, "total": total, "byte_range": list(byte_range) if byte_range else None}

    def _resource_checkpoint(self, resource):
        # HEAD can prove an unchanged full entity without transferring media.
        # Some origins do not implement HEAD or omit its entity headers; a
        # one-byte GET supplies the same proof without fetching a whole file.
        for method, headers in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
            try:
                with self.operation() as operation:
                    with self.reader(resource.get("purpose", "media")).guarded_stream(
                            method, resource["url"], operation=operation, headers=headers) as response:
                        if response.status_code in {401, 403}:
                            raise CacheError("online_cache_url_expired", 502)
                        checkpoint = self.resource_checkpoint(
                            resource, response, requested_range=(0, 0) if method == "GET" else None)
                        if checkpoint is not None:
                            return checkpoint
            except CacheError:
                raise
            except Exception:
                self.check()
                if method == "GET":
                    raise
        return None

    def reusable_hls(self, resources):
        """Decide for the whole saved package before starting any new readers."""
        candidates = [resource for resource in resources if resource["name"] in self.records]
        for resource in candidates:
            upstream = self.records[resource["name"]].get("upstream")
            byte_range = resource.get("byte_range")
            if (not isinstance(upstream, dict) or not _STRONG_ETAG.fullmatch(upstream.get("etag", ""))
                    or upstream.get("url_hash") != hashlib.sha256(resource["url"].encode()).hexdigest()
                    or upstream.get("byte_range") != (list(byte_range) if byte_range else None)):
                return False
        reusable = True
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="online-cache-verify") as executor:
            futures = {executor.submit(self._resource_checkpoint, resource): self.records[resource["name"]]["upstream"]
                       for resource in candidates}
            try:
                for future in as_completed(futures):
                    if future.result() != futures[future]:
                        reusable = False
            except BaseException:
                self.abort_resources()
                raise
        return reusable

    def download_resource(self, resource):
        name = resource["name"]
        if self.complete(name):
            return
        last_error = None
        for attempt in range(3):
            self.check()
            try:
                self._download_resource(resource)
                return
            except CacheError as error:
                if error.category in {"online_cache_space_low", "online_cache_cancelled", "online_source_changed",
                                      "online_source_disabled", "online_cache_path_unsafe", "online_cache_url_expired"}:
                    raise
                last_error = error
            except Exception:
                last_error = CacheError("online_cache_network_failed", 502)
            if self.cancelled.wait(.5 * (2**attempt)):
                self.check()
        raise last_error

    def _download_resource(self, resource):
        name, byte_range = resource["name"], resource.get("byte_range")
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else {}
        with self.operation() as operation:
            with self.reader(resource.get("purpose", "media")).guarded_stream(
                    "GET", resource["url"], operation=operation, headers=headers) as response:
                if response.status_code in {401, 403}:
                    raise CacheError("online_cache_url_expired", 502)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise CacheError("online_cache_encoded_response", 502)
                length = response.headers.get("content-length", "")
                if not length.isdigit() or not 0 < int(length) <= MAX_RESOURCE:
                    raise CacheError("online_cache_resource_size", 422)
                length = int(length)
                if byte_range:
                    match = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
                    if (response.status_code != 206 or match is None
                            or tuple(map(int, match.groups()[:2])) != byte_range
                            or length != byte_range[1] - byte_range[0] + 1
                            or int(match[3]) <= byte_range[1]):
                        raise CacheError("online_cache_range_mismatch", 502)
                elif response.status_code != 200:
                    raise CacheError("online_cache_upstream_status", 502)
                upstream = self.resource_checkpoint(resource, response, requested_range=byte_range)
                amount = 0
                self.progress(name, amount, length=length)
                with files.open_file(self.id, name + ".part", "wb") as stream:
                    for block in response.iter_raw(chunk_size=CHUNK):
                        self.check()
                        amount += len(block)
                        if amount > length:
                            raise CacheError("online_cache_length_mismatch", 502)
                        write_block(stream, block)
                        self.progress(name, amount, transferred=len(block))
                    if amount != length:
                        raise CacheError("online_cache_length_mismatch", 502)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.check()
                os.replace(files.path(self.id, name + ".part"), files.path(self.id, name, missing=True))
                self.record(name, upstream=upstream)

    @staticmethod
    def validator(headers):
        etag = headers.get("etag", "")
        return etag if etag and not etag.startswith("W/") else headers.get("last-modified", "")

    def download_mp4(self, url):
        name = "entry.mp4"
        self.resource_names = {name}
        with self.operation() as operation:
            with self.client.guarded_stream("GET", url, operation=operation,
                                            headers={"Range": "bytes=0-0"}) as response:
                if response.status_code in {401, 403}:
                    raise CacheError("online_cache_url_expired", 502)
                match = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
                ranged = response.status_code == 206 and match is not None and match.group(1, 2) == ("0", "0")
                if ranged:
                    total = int(match[3])
                elif response.status_code == 200:
                    raw = response.headers.get("content-length", "")
                    if not raw.isdigit():
                        raise CacheError("online_cache_resource_size", 422)
                    total = int(raw)
                else:
                    raise CacheError("online_cache_upstream_status", 502)
                validator = self.validator(response.headers)
        if total <= 0:
            raise CacheError("online_cache_empty", 422)
        ranged = ranged and bool(validator)
        checkpoint = {"url_hash": hashlib.sha256(url.encode()).hexdigest(), "validator": validator, "total": total}
        if self.complete(name):
            if ranged and self.package.get("mp4_checkpoint") == checkpoint:
                return
            files.path(self.id, name).unlink()
            with self.lock:
                self.records.pop(name, None)
        part = files.path(self.id, name + ".part", missing=True)
        offset = part.stat().st_size if part.exists() else 0
        if not ranged or self.package.get("mp4_checkpoint") != checkpoint or offset > total:
            offset = 0
            with files.open_file(self.id, name + ".part", "wb"):
                pass
        self.package.update(entry_name=name, transport="mp4", mp4_checkpoint=checkpoint)
        self.progress(name, offset, length=total)
        self.publish(force=True)
        while offset < total:
            self.check()
            end = min(total - 1, offset + MP4_WINDOW - 1) if ranged else total - 1
            headers = {"Range": f"bytes={offset}-{end}", "If-Range": validator} if ranged else {}
            with self.operation(seconds=180) as operation:
                with self.client.guarded_stream("GET", url, operation=operation, headers=headers) as response:
                    if response.status_code in {401, 403}:
                        raise CacheError("online_cache_url_expired", 502)
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise CacheError("online_cache_encoded_response", 502)
                    if ranged:
                        match = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", ""))
                        if (response.status_code != 206 or match is None
                                or tuple(map(int, match.groups())) != (offset, end, total)
                                or self.validator(response.headers) != validator):
                            raise CacheError("online_cache_source_changed")
                    elif response.status_code != 200:
                        raise CacheError("online_cache_upstream_status", 502)
                    raw = response.headers.get("content-length", "")
                    if not raw.isdigit() or int(raw) != end - offset + 1:
                        raise CacheError("online_cache_length_mismatch", 502)
                    with files.open_file(self.id, name + ".part", "ab" if offset else "wb") as stream:
                        for block in response.iter_raw(chunk_size=CHUNK):
                            self.check()
                            if offset + len(block) > end + 1:
                                raise CacheError("online_cache_length_mismatch", 502)
                            write_block(stream, block)
                            offset += len(block)
                            self.progress(name, offset, transferred=len(block))
                        stream.flush()
                        os.fsync(stream.fileno())
                    if offset != end + 1:
                        raise CacheError("online_cache_length_mismatch", 502)
        self.check()
        os.replace(part, files.path(self.id, name, missing=True))
        self.record(name)

    def probe(self, expected):
        from .. import media
        executable = shutil.which("ffprobe") or media._static_bins_if_present().get("ffprobe")
        if not executable:
            raise CacheError("online_cache_probe_unavailable", 503)
        entry = files.path(self.id, self.package["entry_name"])
        command = [executable, "-v", "error", "-max_alloc", "67108864", "-protocol_whitelist", "file"]
        if self.package["transport"] == "hls":
            command += ["-allowed_extensions", "ALL"]
        command += ["-count_packets", "-show_entries", "format=duration:stream=codec_type,codec_name,nb_read_packets",
                    "-of", "json", str(entry)]
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(command, stdout=output, stderr=errors)
            except OSError:
                raise CacheError("online_cache_probe_unavailable", 503) from None
            deadline = time.monotonic() + 1800
            try:
                while process.poll() is None:
                    self.check()
                    if (time.monotonic() > deadline or os.fstat(output.fileno()).st_size > 2 * 1024**2
                            or os.fstat(errors.fileno()).st_size > 2 * 1024**2):
                        raise CacheError("online_cache_probe_failed", 422)
                    self.cancelled.wait(.1)
                output.seek(0)
                errors.seek(0)
                if process.returncode or errors.read(2 * 1024**2):
                    raise CacheError("online_cache_probe_failed", 422)
                try:
                    result = json.load(output)
                    streams = result["streams"]
                    if not any(stream.get("codec_type") == "video" and int(stream.get("nb_read_packets", 0)) > 0
                               for stream in streams):
                        raise ValueError
                    duration = float(result.get("format", {}).get("duration") or expected or 0)
                    if not 0 < duration < 60 * 60 * 24 * 7:
                        raise ValueError
                except (ValueError, TypeError, KeyError):
                    raise CacheError("online_cache_probe_failed", 422) from None
                if expected and abs(duration - expected) > max(3, expected * .02):
                    raise CacheError("online_cache_duration_mismatch", 422)
                return duration
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def reader(self, purpose):
        if purpose != "subtitle":
            return self.client
        with self.lock:
            if self.subtitle_client is None:
                self.subtitle_client = create_online_client(self.task["identity"]["source_id"], "subtitle",
                                                            header_policy=self.header_policy)
            return self.subtitle_client

    def run(self):
        for attempt in range(2):
            try:
                self._run_attempt()
                return
            except CacheError as error:
                if error.category != "online_cache_url_expired" or attempt or self.cancelled.is_set():
                    raise
                # Re-resolve signed addresses once. The package fingerprint or
                # MP4 entity checkpoint decides whether old bytes are reusable.
                self.resource_cancelled.clear()
                self.client = self.subtitle_client = None
                self.policy = None

    def _mp4_with_retry(self, url):
        for attempt in range(3):
            try:
                self.download_mp4(url)
                return
            except CacheError as error:
                if error.category in {"online_cache_space_low", "online_cache_cancelled", "online_source_changed",
                                      "online_source_disabled", "online_cache_path_unsafe", "online_cache_url_expired"}:
                    raise
                problem = error
            except Exception:
                problem = CacheError("online_cache_network_failed", 502)
            if attempt == 2:
                raise problem
            if self.cancelled.wait(.5 * (2**attempt)):
                self.check()

    def _run_attempt(self):
        files.verify_identity(self.storage_identity)
        files.directory(self.id, create=True)
        with self.operation(seconds=30):
            resolved = providers.resolve(self.task["identity"])
        self.check()
        self.client = create_online_client(self.task["identity"]["source_id"], "media", header_policy=resolved.header_policy)
        self.header_policy = resolved.header_policy
        try:
            state = load_online_network_state(self.task["identity"]["source_id"])
            self.policy = online_network_fingerprint(state)
            from . import catalog
            if resolved.source_fingerprint:
                catalog.assert_source_version(self.task["identity"]["source_id"], resolved.source_fingerprint)
            if (resolved.media_policy_fingerprint is not None
                    and self.client.snapshot.fingerprint != resolved.media_policy_fingerprint):
                raise CacheError("online_source_changed")
            if (getattr(self.client, "snapshot", None) is not None
                    and self.client.snapshot.fingerprint != resolve_policy_state(state, "media").fingerprint):
                raise CacheError("online_source_changed")
            duration = resolved.duration_seconds
            if resolved.transport == "mp4":
                self._mp4_with_retry(resolved.url)
            else:
                manifests, resources, fingerprint, duration = self.build_hls(resolved.url)
                if self.package.get("fingerprint") != fingerprint or not self.reusable_hls(resources):
                    self.records.clear()
                    self.current.clear()
                    self.lengths.clear()
                    for old in files.directory(self.id).iterdir():
                        if old.name.startswith(("resource_", "entry.")):
                            files.path(self.id, old.name).unlink()
                self.package.update(entry_name="entry.m3u8", transport="hls", fingerprint=fingerprint)
                self.resource_names = {resource["name"] for resource in resources}
                self.publish(force=True)
                with ThreadPoolExecutor(max_workers=4, thread_name_prefix="online-cache-resource") as executor:
                    futures = [executor.submit(self.download_resource, resource) for resource in resources]
                    try:
                        for future in as_completed(futures):
                            future.result()
                    except BaseException:
                        self.abort_resources()
                        raise
                for name, body in manifests.items():
                    self.check()
                    files.atomic_bytes(self.id, name, body)
                    self.record(name)
            self.check(force=True)
            self.package["files"] = sorted(self.records)
            duration = self.probe(duration)
            self.check()
            self.publish(force=True)
            current = ledger.get(self.id)
            if current is not None and current["state"] == "downloading":
                ledger.update(self.id, expected_state="downloading", state="ready", package=dict(self.package),
                              duration_seconds=duration, transport=self.package["transport"],
                              error_code="", speed_bytes_per_second=0)
        finally:
            self.client.close()
            if self.subtitle_client is not None:
                self.subtitle_client.close()
