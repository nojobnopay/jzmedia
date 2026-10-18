"""Publish only complete fMP4 samples matched to the Dolby Vision frame audit.

FFmpeg writes into an unservable staging directory. This module parses box
metadata (not decoded pixels), and atomically copies verified artifacts into
the public session directory before exposing them in playlists.
"""
from dataclasses import dataclass
from collections import OrderedDict
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import threading
import uuid

from ...playback.dovi_audit import AuditError


class PublishError(RuntimeError):
    pass


_MAX_METADATA = 2 * 1024 * 1024
_MAX_SEGMENT = 128 * 1024 * 1024
_MAX_PLAYLIST = 4 * 1024 * 1024
_MAX_MANIFEST = 32 * 1024 * 1024
_MAX_ENTRIES = 10000
_MAX_RENDITIONS = 9  # One video and the planner's maximum eight audio tracks.
_NAME = re.compile(r"^(?:video|audio\d+)_(?:init\.mp4|seg\d{5,}\.m4s)$")
_PLAYLIST = re.compile(r"^out_(video|audio\d+)\.m3u8$")
PUBLICATION_REVISION = "p5-sample-audit-1"
_COMPLETE_CACHE = OrderedDict()
_COMPLETE_CACHE_LOCK = threading.RLock()
_COMPLETE_CACHE_LIMIT = 128
_COMPLETE_CACHE_FILE_LIMIT = 200000


@dataclass(frozen=True)
class Track:
    id: int
    timescale: int
    handler: bytes
    default_duration: int = 0
    default_size: int = 0
    default_flags: int = 0
    time_offset: Fraction = Fraction(0)


@dataclass(frozen=True)
class Sample:
    pts: Fraction
    duration: Fraction
    dts: int
    size: int
    offset: int
    flags: int
    composition_offset: int


def _u32(data, offset=0):
    if offset < 0 or offset + 4 > len(data):
        raise PublishError("dovi_mp4_truncated_field")
    return struct.unpack_from(">I", data, offset)[0]


def _u64(data, offset=0):
    if offset < 0 or offset + 8 > len(data):
        raise PublishError("dovi_mp4_truncated_field")
    return struct.unpack_from(">Q", data, offset)[0]


def _boxes(fh, start, end):
    count = 0
    while start < end:
        count += 1
        if count > 20000 or end - start < 8:
            raise PublishError("dovi_mp4_box_bounds")
        fh.seek(start)
        header = fh.read(8)
        if len(header) != 8:
            raise PublishError("dovi_mp4_truncated_box")
        size, kind = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            extended = fh.read(8)
            if len(extended) != 8:
                raise PublishError("dovi_mp4_truncated_box")
            size = _u64(extended)
            header_size = 16
        # An open-ended mdat is still being written and cannot be published.
        if size == 0 or size < header_size or start + size > end:
            raise PublishError("dovi_mp4_box_bounds")
        yield kind, start, start + header_size, start + size
        start += size


def _payload(fh, box):
    _kind, _start, payload, end = box
    if end - payload > _MAX_METADATA:
        raise PublishError("dovi_mp4_metadata_limit")
    fh.seek(payload)
    data = fh.read(end - payload)
    if len(data) != end - payload:
        raise PublishError("dovi_mp4_truncated_box")
    return data


def _one(boxes, kind, *, optional=False):
    found = [box for box in boxes if box[0] == kind]
    if len(found) != 1:
        if optional and not found:
            return None
        raise PublishError("dovi_mp4_required_box")
    return found[0]


def _children(fh, box):
    return list(_boxes(fh, box[2], box[3]))


def _edit_offset(fh, edts, media_timescale, movie_timescale):
    data = _payload(fh, _one(_children(fh, edts), b"elst"))
    if len(data) < 8 or data[0] not in (0, 1):
        raise PublishError("dovi_mp4_edit_list")
    count, version = _u32(data, 4), data[0]
    entry_size = 20 if version else 12
    if not 1 <= count <= 2 or len(data) != 8 + count * entry_size:
        raise PublishError("dovi_mp4_edit_list")
    offset, pos, media_seen = Fraction(0), 8, False
    for _ in range(count):
        if version:
            duration = _u64(data, pos)
            media_time = struct.unpack_from(">q", data, pos + 8)[0]
        else:
            duration = _u32(data, pos)
            media_time = struct.unpack_from(">i", data, pos + 4)[0]
        if data[pos + entry_size - 4:pos + entry_size] != b"\0\x01\0\0" or media_seen:
            raise PublishError("dovi_mp4_edit_list")
        if media_time == -1:
            offset += Fraction(duration, movie_timescale)
        elif media_time >= 0:
            media_seen = True
            offset -= Fraction(media_time, media_timescale)
        else:
            raise PublishError("dovi_mp4_edit_list")
        pos += entry_size
    if not media_seen:
        raise PublishError("dovi_mp4_edit_list")
    return offset


def parse_init(path):
    """Read media timescales, trex defaults and linear presentation edit lists."""
    size = os.path.getsize(path)
    if size > _MAX_METADATA:
        raise PublishError("dovi_mp4_init_limit")
    tracks = {}
    with open(path, "rb") as fh:
        top = list(_boxes(fh, 0, size))
        moov = _children(fh, _one(top, b"moov"))
        defaults = {}
        mvex = _one(moov, b"mvex")
        for box in _children(fh, mvex):
            if box[0] == b"trex":
                data = _payload(fh, box)
                if len(data) != 24 or data[:4] != b"\0\0\0\0":
                    raise PublishError("dovi_mp4_trex")
                track_id, _description, duration, sample_size, flags = struct.unpack(">IIIII", data[4:])
                if track_id in defaults:
                    raise PublishError("dovi_mp4_duplicate_track")
                defaults[track_id] = duration, sample_size, flags
        for box in moov:
            if box[0] != b"trak":
                continue
            trak = _children(fh, box)
            tkhd = _payload(fh, _one(trak, b"tkhd"))
            if not tkhd or tkhd[0] not in (0, 1):
                raise PublishError("dovi_mp4_tkhd")
            track_id = _u32(tkhd, 20 if tkhd[0] else 12)
            mdia = _children(fh, _one(trak, b"mdia"))
            mdhd = _payload(fh, _one(mdia, b"mdhd"))
            if not mdhd or mdhd[0] not in (0, 1):
                raise PublishError("dovi_mp4_mdhd")
            timescale = _u32(mdhd, 20 if mdhd[0] else 12)
            handler = _payload(fh, _one(mdia, b"hdlr"))[8:12]
            if not track_id or not timescale or track_id in tracks or track_id not in defaults:
                raise PublishError("dovi_mp4_track")
            offset = Fraction(0)
            edts = _one(trak, b"edts", optional=True)
            if edts:
                mvhd = _payload(fh, _one(moov, b"mvhd"))
                if not mvhd or mvhd[0] not in (0, 1):
                    raise PublishError("dovi_mp4_mvhd")
                movie_timescale = _u32(mvhd, 20 if mvhd[0] else 12)
                if not movie_timescale:
                    raise PublishError("dovi_mp4_mvhd")
                offset = _edit_offset(fh, edts, timescale, movie_timescale)
            tracks[track_id] = Track(track_id, timescale, handler, *defaults[track_id], offset)
    if not tracks:
        raise PublishError("dovi_mp4_no_tracks")
    return tracks


def parse_fragment(path, tracks):
    """Parse tfhd/tfdt/trun, checking every referenced sample lies in mdat.

    The supported producer writes independent rendition files with a single
    track. Explicit defaults, multiple runs/moofs, and signed version-1
    composition offsets are handled; ambiguous implicit inter-track bases fail.
    """
    size = os.path.getsize(path)
    if size > _MAX_SEGMENT:
        raise PublishError("dovi_mp4_segment_limit")
    result = {track_id: [] for track_id in tracks}
    occupied = []
    with open(path, "rb") as fh:
        top = list(_boxes(fh, 0, size))
        mdats = [(box[2], box[3]) for box in top if box[0] == b"mdat"]
        moofs = [box for box in top if box[0] == b"moof"]
        if not mdats or not moofs:
            raise PublishError("dovi_mp4_incomplete_fragment")
        for moof in moofs:
            children = _children(fh, moof)
            mfhd = _payload(fh, _one(children, b"mfhd"))
            if len(mfhd) != 8 or mfhd[:4] != b"\0\0\0\0":
                raise PublishError("dovi_mp4_mfhd")
            trafs = [box for box in children if box[0] == b"traf"]
            if len(trafs) != 1:
                raise PublishError("dovi_mp4_interleaved_tracks")
            for traf in trafs:
                children = _children(fh, traf)
                data = _payload(fh, _one(children, b"tfhd"))
                if len(data) < 8 or data[0] != 0:
                    raise PublishError("dovi_mp4_tfhd")
                flags, track_id = _u32(data) & 0xFFFFFF, _u32(data, 4)
                if flags & ~0x03003B or flags & 0x010000 or track_id not in tracks:
                    raise PublishError("dovi_mp4_tfhd_flags")
                track = tracks[track_id]
                pos, base = 8, moof[1]
                if flags & 1:
                    base, pos = _u64(data, pos), pos + 8
                if flags & 2:
                    _u32(data, pos)
                    pos += 4
                duration, sample_size, sample_flags = (
                    track.default_duration, track.default_size, track.default_flags)
                for bit, name in ((8, "duration"), (16, "size"), (32, "flags")):
                    if flags & bit:
                        value, pos = _u32(data, pos), pos + 4
                        if name == "duration":
                            duration = value
                        elif name == "size":
                            sample_size = value
                        else:
                            sample_flags = value
                if pos != len(data):
                    raise PublishError("dovi_mp4_tfhd_size")
                tfdt = _payload(fh, _one(children, b"tfdt"))
                if not tfdt or tfdt[0] not in (0, 1) or len(tfdt) != (12 if tfdt[0] else 8):
                    raise PublishError("dovi_mp4_tfdt")
                dts = _u64(tfdt, 4) if tfdt[0] else _u32(tfdt, 4)
                next_data = None
                runs = [box for box in children if box[0] == b"trun"]
                if not runs:
                    raise PublishError("dovi_mp4_no_samples")
                for run in runs:
                    data = _payload(fh, run)
                    if len(data) < 8 or data[0] not in (0, 1):
                        raise PublishError("dovi_mp4_trun")
                    version, flags, count = data[0], _u32(data) & 0xFFFFFF, _u32(data, 4)
                    if flags & ~0x000F05 or not 0 < count <= 20000:
                        raise PublishError("dovi_mp4_trun_flags")
                    pos, first_flags = 8, None
                    if flags & 1:
                        data_offset = struct.unpack(">i", data[pos:pos + 4])[0] if len(data) >= pos + 4 else None
                        if data_offset is None:
                            raise PublishError("dovi_mp4_truncated_field")
                        next_data, pos = base + data_offset, pos + 4
                    elif next_data is None:
                        next_data = base
                    if flags & 4:
                        first_flags, pos = _u32(data, pos), pos + 4
                    if flags & 4 and flags & 0x400:
                        raise PublishError("dovi_mp4_conflicting_flags")
                    for index in range(count):
                        sample_duration, length, current_flags, cto = duration, sample_size, sample_flags, 0
                        for bit in (0x100, 0x200, 0x400, 0x800):
                            if flags & bit:
                                value, pos = _u32(data, pos), pos + 4
                                if bit == 0x100:
                                    sample_duration = value
                                elif bit == 0x200:
                                    length = value
                                elif bit == 0x400:
                                    current_flags = value
                                else:
                                    cto = value - 2 ** 32 if version == 1 and value >= 2 ** 31 else value
                        if index == 0 and first_flags is not None:
                            current_flags = first_flags
                        if not sample_duration or not length or not any(
                                start <= next_data and next_data + length <= end for start, end in mdats):
                            raise PublishError("dovi_mp4_sample_bounds")
                        output = result[track_id]
                        if output and dts != output[-1].dts + int(output[-1].duration * track.timescale):
                            raise PublishError("dovi_mp4_decode_gap")
                        sample = Sample(Fraction(dts + cto, track.timescale) + track.time_offset,
                                        Fraction(sample_duration, track.timescale),
                                        dts, length, next_data, current_flags, cto)
                        if output and sample.pts <= output[-1].pts:
                            raise PublishError("dovi_mp4_display_order")
                        output.append(sample)
                        occupied.append((next_data, next_data + length))
                        if len(output) > 20000:
                            raise PublishError("dovi_mp4_sample_limit")
                        dts += sample_duration
                        next_data += length
                    if pos != len(data):
                        raise PublishError("dovi_mp4_trun_size")
    if not any(result.values()):
        raise PublishError("dovi_mp4_no_samples")
    # The payload must consist precisely of the declared, non-overlapping
    # samples. A second run cannot reuse bytes from an earlier sample.
    cursor = 0
    occupied.sort()
    for start, end in mdats:
        position = start
        while cursor < len(occupied) and occupied[cursor][0] < end:
            left, right = occupied[cursor]
            if left != position or right > end:
                raise PublishError("dovi_mp4_payload_coverage")
            position, cursor = right, cursor + 1
        if position != end:
            raise PublishError("dovi_mp4_payload_coverage")
    if cursor != len(occupied):
        raise PublishError("dovi_mp4_payload_coverage")
    return result


def _box(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _audio_prefix(source, destination, track, samples):
    """Write a complete prefix fragment when the last AAC packet crosses EOF.

    This only copies already encoded AAC bytes. The final packet that would
    exceed the verified video boundary is omitted; no video frame is removed.
    """
    with open(source, "rb") as src:
        source_moofs = [candidate for candidate in _boxes(src, 0, os.fstat(src.fileno()).st_size)
                        if candidate[0] == b"moof"]
        if not source_moofs:
            raise PublishError("dovi_mp4_incomplete_fragment")
        mfhd = _payload(src, _one(_children(src, source_moofs[0]), b"mfhd"))
        if len(mfhd) != 8 or mfhd[:4] != b"\0\0\0\0":
            raise PublishError("dovi_mp4_mfhd")
        sequence = _u32(mfhd, 4)
    entries = b"".join(struct.pack(">IIIi", int(sample.duration * track.timescale),
                                    sample.size, sample.flags, sample.composition_offset)
                       for sample in samples)
    tfhd = _box(b"tfhd", struct.pack(">II", 0x020000, track.id))
    tfdt = _box(b"tfdt", struct.pack(">IQ", 0x01000000, samples[0].dts))

    def moof(offset):
        trun = _box(b"trun", struct.pack(">IIi", 0x01000F01, len(samples), offset) + entries)
        return _box(b"moof", _box(b"mfhd", struct.pack(">II", 0, sequence))
                    + _box(b"traf", tfhd + tfdt + trun))

    header = moof(0)
    header = moof(len(header) + 8)
    with open(source, "rb") as src, open(destination, "wb") as dst:
        dst.write(header)
        dst.write(struct.pack(">I4s", sum(sample.size for sample in samples) + 8, b"mdat"))
        for sample in samples:
            src.seek(sample.offset)
            remaining = sample.size
            while remaining:
                block = src.read(min(remaining, 64 * 1024))
                if not block:
                    raise PublishError("dovi_mp4_source_changed")
                dst.write(block)
                remaining -= len(block)


def _safe_file(directory, name):
    if not _NAME.fullmatch(name):
        raise PublishError("dovi_playlist_unsafe_uri")
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise PublishError("dovi_publication_file_missing")
    return path


def _digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_bytes(path, data):
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class Playlist:
    name: str
    rendition: str
    header: list
    init: str
    entries: list
    endlist: bool


def _read_playlist(path):
    if path.is_symlink() or path.stat().st_size > _MAX_PLAYLIST:
        raise PublishError("dovi_playlist_limit")
    text = path.read_text(encoding="utf-8")
    match = _PLAYLIST.fullmatch(path.name)
    if not match or not text.endswith("\n"):
        raise PublishError("dovi_playlist_incomplete")
    rendition = match[1]
    header, entries, init, duration, endlist = [], [], None, None, False
    seen = set()
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            if duration is not None:
                raise PublishError("dovi_playlist_duration")
            try:
                duration = Fraction(line[8:].split(",", 1)[0])
            except (ValueError, ZeroDivisionError):
                raise PublishError("dovi_playlist_duration") from None
            if duration <= 0:
                raise PublishError("dovi_playlist_duration")
        elif line.startswith("#EXT-X-MAP:"):
            mapping = re.fullmatch(r'#EXT-X-MAP:URI="([^"]+)"', line)
            if not mapping or init is not None or mapping[1] != rendition + "_init.mp4":
                raise PublishError("dovi_playlist_init")
            init = mapping[1]
            header.append(line)
        elif line == "#EXT-X-ENDLIST":
            endlist = True
        elif line and not line.startswith("#"):
            if (endlist or duration is None or not _NAME.fullmatch(line)
                    or not line.startswith(rendition + "_seg") or line in seen):
                raise PublishError("dovi_playlist_unsafe_uri")
            entries.append((line, duration))
            seen.add(line)
            duration = None
            if len(entries) > _MAX_ENTRIES:
                raise PublishError("dovi_playlist_limit")
        elif line.startswith(("#EXT-X-BYTERANGE:", "#EXT-X-KEY:", "#EXT-X-DISCONTINUITY",
                              "#EXT-X-PART:", "#EXT-X-PRELOAD-HINT:")):
            raise PublishError("dovi_playlist_unsupported")
        elif line and not entries and duration is None:
            header.append(line)
    if not header or header[0] != "#EXTM3U" or not init or duration is not None:
        raise PublishError("dovi_playlist_incomplete")
    return Playlist(path.name, rendition, header, init, entries, endlist)


class Publisher:
    """One producer's private-to-public gate; poll on one worker thread only."""

    def __init__(self, staging_dir, public_dir, audit, *, expected_renditions=None):
        self.staging = Path(staging_dir).resolve()
        self.public = Path(public_dir).resolve()
        if self.staging == self.public or self.staging in self.public.parents:
            raise ValueError("private staging must not contain the public directory")
        self.public.mkdir(parents=True, exist_ok=True)
        self.audit = audit
        self.expected_renditions = (frozenset(expected_renditions)
                                   if expected_renditions is not None else None)
        if self.expected_renditions is not None and (
                "video" not in self.expected_renditions
                or len(self.expected_renditions) > _MAX_RENDITIONS
                or any(not re.fullmatch(r"video|audio\d+", name)
                       for name in self.expected_renditions)):
            raise ValueError("invalid expected renditions")
        self.error = None
        self.complete = False
        self.video_end = Fraction(0)
        self._tracks = {}
        self._published = {}
        self._entries = {}
        self._playlists = {}
        self._last_video_dts_end = None
        self._last_audio_end = {}
        self._source_playlists = {}

    def _playlist(self, path):
        info = path.stat()
        signature = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        cached = self._source_playlists.get(path.name)
        if cached and cached[0] == signature:
            return cached[1]
        playlist = _read_playlist(path)
        self._source_playlists[path.name] = (signature, playlist)
        return playlist

    def _copy(self, name, source=None):
        source = source or _safe_file(self.staging, name)
        destination = self.public / name
        temp = destination.with_name("." + name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with open(source, "rb") as src, open(temp, "wb") as dst:
                shutil.copyfileobj(src, dst, length=64 * 1024)
            size, digest = temp.stat().st_size, _digest(temp)
            os.replace(temp, destination)
            self._published[name] = {"size": size, "sha256": digest}
        finally:
            temp.unlink(missing_ok=True)

    def _track(self, playlist):
        if playlist.rendition not in self._tracks:
            path = _safe_file(self.staging, playlist.init)
            tracks = parse_init(path)
            if len(tracks) != 1:
                raise PublishError("dovi_mp4_rendition_tracks")
            track = next(iter(tracks.values()))
            if track.handler != (b"vide" if playlist.rendition == "video" else b"soun"):
                raise PublishError("dovi_mp4_rendition_handler")
            self._tracks[playlist.rendition] = tracks
        return self._tracks[playlist.rendition]

    def _validate_audio_tail(self, playlist, index, tracks, first_samples, first_stat):
        """Validate the complete, bounded AAC suffix across an HLS boundary.

        A fractional video endpoint can cross one normal AAC packet, followed
        by a short final encoder packet in the next HLS file. Both packets are
        checked before either file's prefix can be exposed. This never adjusts
        the video endpoint or permits a longer source-audio tail.
        """
        if len(playlist.entries) - index > 2:
            raise PublishError("dovi_publication_audio_tail")
        track = next(iter(tracks.values()))
        packet_limit = min(Fraction(1024, track.timescale), Fraction(1, 8))
        tail, previous = [], None
        previous_end = self._last_audio_end.get(playlist.rendition)
        for offset, (name, _duration) in enumerate(playlist.entries[index:]):
            path = _safe_file(self.staging, name)
            before = first_stat if offset == 0 else path.stat()
            samples = first_samples if offset == 0 else parse_fragment(path, tracks)[track.id]
            if not samples:
                raise PublishError("dovi_mp4_no_samples")
            for sample in samples:
                if previous is not None:
                    if (sample.pts != previous.pts + previous.duration
                            or sample.dts != previous.dts + int(previous.duration * track.timescale)):
                        raise PublishError("dovi_mp4_audio_gap")
                elif previous_end is not None and sample.pts != previous_end:
                    raise PublishError("dovi_mp4_audio_gap")
                if sample.pts + sample.duration > self.video_end:
                    tail.append(sample)
                    if len(tail) > 2 or sample.duration > packet_limit:
                        raise PublishError("dovi_publication_audio_tail")
                elif tail:
                    raise PublishError("dovi_mp4_audio_gap")
                previous = sample
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise PublishError("dovi_mp4_source_changed")
        if (not tail or tail[0].pts > self.video_end
                or tail[-1].pts + tail[-1].duration - self.video_end > packet_limit):
            raise PublishError("dovi_publication_audio_tail")

    def _publish_rendition(self, playlist, *, final):
        tracks = self._track(playlist)
        track = next(iter(tracks.values()))
        published = self._entries.setdefault(playlist.rendition, [])
        if [entry[0] for entry in published] != [entry[0] for entry in playlist.entries[:len(published)]]:
            raise PublishError("dovi_playlist_history_changed")
        omitted_audio_tail = False
        for index, (name, _listed_duration) in enumerate(
                playlist.entries[len(published):], len(published)):
            path = _safe_file(self.staging, name)
            before = path.stat()
            samples_by_track = parse_fragment(path, tracks)
            samples = samples_by_track[track.id]
            if not samples:
                raise PublishError("dovi_mp4_no_samples")
            end = samples[-1].pts + samples[-1].duration
            trimmed = None
            if playlist.rendition == "video":
                if (self._last_video_dts_end is not None
                        and samples[0].dts != self._last_video_dts_end):
                    raise PublishError("dovi_mp4_video_gap")
                if not self.audit.match_samples([(sample.pts, sample.duration) for sample in samples],
                        tolerance=min(Fraction(1, 2 * track.timescale),
                                      min(sample.duration for sample in samples) / 8)):
                    break
            else:
                if end > self.video_end:
                    if not final:
                        break
                    retained = [sample for sample in samples if sample.pts + sample.duration <= self.video_end]
                    self._validate_audio_tail(playlist, index, tracks, samples, before)
                    omitted_audio_tail = True
                    samples = retained
                    if not samples:
                        if not published:
                            raise PublishError("dovi_publication_audio_empty")
                        break
                    end = samples[-1].pts + samples[-1].duration
                    trimmed = self.public / (".audio-prefix-" + uuid.uuid4().hex + ".tmp")
                    _audio_prefix(path, trimmed, track, samples)
                previous = self._last_audio_end.get(playlist.rendition)
                if previous is not None and samples[0].pts != previous:
                    raise PublishError("dovi_mp4_audio_gap")
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns):
                raise PublishError("dovi_mp4_source_changed")
            if playlist.init not in self._published:
                self._copy(playlist.init)
            try:
                self._copy(name, trimmed)
            finally:
                if trimmed is not None:
                    trimmed.unlink(missing_ok=True)
            if playlist.rendition == "video":
                self.video_end = end
                self._last_video_dts_end = samples[-1].dts + int(samples[-1].duration * track.timescale)
            else:
                self._last_audio_end[playlist.rendition] = end
            published.append((name, end - samples[0].pts))
            # The public atomically copied fragment is now the authoritative
            # copy. Keep only init/playlist state in private storage, rather
            # than retaining a second full movie until the producer exits.
            path.unlink()
            if omitted_audio_tail:
                break
        return len(published) == len(playlist.entries) or omitted_audio_tail

    def _write_playlist(self, playlist, endlist):
        entries = self._entries.get(playlist.rendition, [])
        if not entries:
            return
        lines = list(playlist.header)
        for name, duration in entries:
            lines += [f"#EXTINF:{float(duration):.9f},", name]
        if endlist:
            lines.append("#EXT-X-ENDLIST")
        _atomic_bytes(self.public / playlist.name, ("\n".join(lines) + "\n").encode())
        path = self.public / playlist.name
        self._playlists[playlist.name] = {"size": path.stat().st_size, "sha256": _digest(path)}

    def poll(self, *, final=False):
        """Publish available verified output. A repeated successful final is safe."""
        if self.complete:
            return True
        if self.error:
            raise PublishError(self.error)
        try:
            self.audit.check()
            before_count = len(self._published)
            paths = sorted(self.staging.glob("out_*.m3u8"), key=lambda path: path.name != "out_video.m3u8")
            playlists = [self._playlist(path) for path in paths if _PLAYLIST.fullmatch(path.name)]
            if not playlists or playlists[0].rendition != "video":
                if final:
                    raise PublishError("dovi_publication_no_video")
                return False
            actual_renditions = {playlist.rendition for playlist in playlists}
            if len(actual_renditions) > _MAX_RENDITIONS:
                raise PublishError("dovi_publication_rendition_limit")
            if (final and self.expected_renditions is not None
                    and actual_renditions != self.expected_renditions):
                raise PublishError("dovi_publication_missing_rendition")
            all_published = True
            for playlist in playlists:
                all_published &= self._publish_rendition(playlist, final=final)
            if final:
                self.audit.assert_complete()
                if not all(playlist.endlist for playlist in playlists):
                    raise PublishError("dovi_publication_missing_endlist")
                if not all_published or any(not self._entries.get(playlist.rendition) for playlist in playlists):
                    raise PublishError("dovi_publication_incomplete_rendition")
                # At most two checked AAC packets can straddle the endpoint;
                # their total overshoot is bounded to one AAC packet duration.
                self.complete = bool(self._entries.get("video"))
            if not final and before_count == len(self._published):
                return all_published
            for playlist in playlists:
                self._write_playlist(playlist, self.complete)
            state = {"revision": PUBLICATION_REVISION, "complete": self.complete,
                     "video_end": str(self.video_end), "files": self._published,
                     "playlists": self._playlists,
                     "audit": self.audit.diagnostics()}
            _atomic_bytes(self.public / "dovi-publication.json",
                          json.dumps(state, separators=(",", ":"), sort_keys=True).encode())
            return self.complete if final else all_published
        except (AuditError, PublishError, OSError, UnicodeError, ValueError, struct.error) as exc:
            self.complete = False
            self.error = str(exc) if isinstance(exc, (AuditError, PublishError)) else "dovi_publication_io"
            raise PublishError(self.error) from exc

    def diagnostics(self):
        return {"error": self.error, "complete": self.complete, "video_end": float(self.video_end),
                "published_video_segments": len(self._entries.get("video", [])),
                "published_files": len(self._published)}


def _fingerprint(directory, names):
    result = []
    for name in names:
        info = (directory / name).lstat()
        if not stat.S_ISREG(info.st_mode):
            raise PublishError("dovi_publication_file_missing")
        result.append((name, info.st_dev, info.st_ino, info.st_size,
                       info.st_mtime_ns, info.st_ctime_ns))
    return tuple(result)


def publication_complete(public_dir):
    """Validate cache reuse; memoize hashes until any file fingerprint changes.

    At most 128 completed artifacts are retained. Every call stats the manifest,
    playlists and referenced files; mutation/replacement invalidates the SHA
    result even when the new file has the same length or restored mtime.
    """
    directory = Path(public_dir).resolve()
    key = str(directory)
    try:
        actual_playlists = tuple(sorted(path.name for path in directory.glob("out_*.m3u8")))
        with _COMPLETE_CACHE_LOCK:
            cached = _COMPLETE_CACHE.get(key)
        if cached:
            names, fingerprint, playlist_names = cached
            if actual_playlists == playlist_names and _fingerprint(directory, names) == fingerprint:
                with _COMPLETE_CACHE_LOCK:
                    if key in _COMPLETE_CACHE:
                        _COMPLETE_CACHE.move_to_end(key)
                return True
        manifest = directory / "dovi-publication.json"
        if manifest.is_symlink() or manifest.stat().st_size > _MAX_MANIFEST:
            return False
        state = json.loads(manifest.read_text(encoding="utf-8"))
        audit = state["audit"]
        if (state.get("revision") != PUBLICATION_REVISION or state.get("complete") is not True
                or audit.get("error") or audit.get("eof") is not True or audit.get("returncode") != 0
                or audit.get("pending_frames") != 0 or audit.get("audited_frames", 0) <= 0
                or audit.get("matched_frames") != audit.get("audited_frames")):
            return False
        files = state["files"]
        playlists_meta = state["playlists"]
        if (not isinstance(files, dict) or not 0 < len(files) <= (_MAX_ENTRIES + 1) * _MAX_RENDITIONS
                or any(not _NAME.fullmatch(name) for name in files)):
            return False
        if (not isinstance(playlists_meta, dict) or not 0 < len(playlists_meta) <= 100
                or any(not _PLAYLIST.fullmatch(name) for name in playlists_meta)
                or tuple(sorted(playlists_meta)) != actual_playlists):
            return False
        names = tuple(sorted(["dovi-publication.json", *files, *playlists_meta]))
        before = _fingerprint(directory, names)
        for name, record in files.items():
            path = _safe_file(directory, name)
            if path.stat().st_size != record["size"] or _digest(path) != record["sha256"]:
                return False
        for name, record in playlists_meta.items():
            path = directory / name
            if path.stat().st_size != record["size"] or _digest(path) != record["sha256"]:
                return False
        playlists = [_read_playlist(path) for path in directory.glob("out_*.m3u8")]
        valid = (any(playlist.rendition == "video" and playlist.entries for playlist in playlists)
                 and all(playlist.endlist and playlist.init in files
                         and all(name in files for name, _ in playlist.entries) for playlist in playlists))
        after = _fingerprint(directory, names)
        if not valid or before != after:
            return False
        with _COMPLETE_CACHE_LOCK:
            _COMPLETE_CACHE[key] = (names, after, actual_playlists)
            _COMPLETE_CACHE.move_to_end(key)
            while (len(_COMPLETE_CACHE) > _COMPLETE_CACHE_LIMIT
                   or sum(len(item[0]) for item in _COMPLETE_CACHE.values()) > _COMPLETE_CACHE_FILE_LIMIT):
                _COMPLETE_CACHE.popitem(last=False)
        return True
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError, KeyError, PublishError):
        return False
