"""Parse and rewrite a bounded, unencrypted HLS VOD manifest without I/O.

Every external reference is handed to the session's resource registrar only
after the whole manifest is structurally valid. The registrar owns public
target checks, origin credentials, and the opaque same-origin resource path.
"""

from dataclasses import dataclass
import math
import re
from urllib.parse import unquote, urljoin, urlsplit


MANIFEST_BYTES = 256 * 1024
OUTPUT_BYTES = 1024 * 1024
REFERENCE_LIMIT = 4096
LINE_LIMIT = 8192
MAX_BYTE_POSITION = 2**63 - 1
_ATTRIBUTE_NAME = re.compile(r"[A-Z0-9-]+\Z")
_INTEGER = re.compile(r"[0-9]{1,20}\Z")
_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
_BYTE_RANGE = re.compile(r"([0-9]{1,20})(?:@([0-9]{1,20}))?\Z")
_BAD_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RESOURCE_PATH = re.compile(r"/(?!/)[A-Za-z0-9_./-]{1,511}\Z")
_NO_ARGUMENT = {"#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-DISCONTINUITY", "#EXT-X-ENDLIST",
                "#EXT-X-I-FRAMES-ONLY", "#EXT-X-GAP"}
_MEDIA_INTEGER = {"#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE", "#EXT-X-DISCONTINUITY-SEQUENCE"}
_STREAM_ATTRIBUTES = {"BANDWIDTH", "AVERAGE-BANDWIDTH", "CODECS", "RESOLUTION", "FRAME-RATE",
                      "HDCP-LEVEL", "AUDIO", "VIDEO", "SUBTITLES", "CLOSED-CAPTIONS", "VIDEO-RANGE"}
_MEDIA_ATTRIBUTES = {"TYPE", "URI", "GROUP-ID", "LANGUAGE", "ASSOC-LANGUAGE", "NAME", "DEFAULT",
                     "AUTOSELECT", "FORCED", "INSTREAM-ID", "CHARACTERISTICS", "CHANNELS"}
_QUOTED_ATTRIBUTES = {"URI", "CODECS", "AUDIO", "VIDEO", "SUBTITLES", "GROUP-ID", "LANGUAGE",
                      "ASSOC-LANGUAGE", "NAME", "INSTREAM-ID", "CHARACTERISTICS", "CHANNELS", "BYTERANGE"}


class ManifestError(ValueError):
    """Fixed failure category, never the original URI or manifest body."""


@dataclass(frozen=True)
class _Attribute:
    name: str
    value: str
    quoted: bool


@dataclass(frozen=True)
class _Reference:
    url: str
    kind: str
    byte_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class _AttributeLine:
    tag: str
    attributes: tuple[_Attribute, ...]
    reference: _Reference | None = None


def _attributes(value):
    """HLS attribute-list grammar; commas inside quotes are ordinary text.

    HLS quoted strings do not define backslash escapes. An embedded quote
    therefore closes the value and must be followed by a comma or end of line.
    """
    if not value:
        raise ManifestError("invalid_hls_attributes")
    result, seen, index = [], set(), 0
    while index < len(value):
        equal = value.find("=", index)
        if equal < 0:
            raise ManifestError("invalid_hls_attributes")
        name = value[index:equal]
        if not _ATTRIBUTE_NAME.fullmatch(name) or name in seen or len(result) >= 32:
            raise ManifestError("invalid_hls_attributes")
        index = equal + 1
        quoted = index < len(value) and value[index] == '"'
        if quoted:
            end = value.find('"', index + 1)
            if end < 0:
                raise ManifestError("invalid_hls_attributes")
            item = value[index + 1:end]
            index = end + 1
        else:
            end = value.find(",", index)
            end = len(value) if end < 0 else end
            item = value[index:end]
            index = end
            if not item or any(char.isspace() or char in {'"', "="} for char in item):
                raise ManifestError("invalid_hls_attributes")
        if index < len(value):
            if value[index] != "," or index + 1 == len(value):
                raise ManifestError("invalid_hls_attributes")
            index += 1
        seen.add(name)
        result.append(_Attribute(name, item, quoted))
    return tuple(result)


def _checked_attributes(value, allowed, required=()):
    attributes = _attributes(value)
    mapping = {attribute.name: attribute for attribute in attributes}
    if not set(mapping) <= allowed or not set(required) <= set(mapping):
        raise ManifestError("unsupported_hls_attributes")
    for attribute in attributes:
        if attribute.name in _QUOTED_ATTRIBUTES and not attribute.quoted:
            raise ManifestError("invalid_hls_attributes")
        if attribute.name in {"BANDWIDTH", "AVERAGE-BANDWIDTH"}:
            _integer(attribute.value, positive=True)
        if attribute.name in {"DEFAULT", "AUTOSELECT", "FORCED", "PRECISE"} and attribute.value not in {"YES", "NO"}:
            raise ManifestError("invalid_hls_attributes")
    return attributes, mapping


def _integer(value, *, positive=False):
    if not _INTEGER.fullmatch(value):
        raise ManifestError("invalid_hls_number")
    number = int(value)
    if number > MAX_BYTE_POSITION or positive and number == 0:
        raise ManifestError("invalid_hls_number")
    return number


def _decimal(value, *, positive=False):
    if len(value) > 32 or not _DECIMAL.fullmatch(value):
        raise ManifestError("invalid_hls_number")
    number = float(value)
    if not math.isfinite(number) or positive and number <= 0:
        raise ManifestError("invalid_hls_number")
    return number


def _range(value):
    match = _BYTE_RANGE.fullmatch(value)
    if match is None:
        raise ManifestError("invalid_hls_byte_range")
    length = _integer(match[1], positive=True)
    offset = _integer(match[2]) if match[2] is not None else None
    return length, offset


def _resolved_range(value, url, previous):
    length, offset = value
    if offset is None:
        if previous is None or previous[0] != url:
            raise ManifestError("invalid_hls_implicit_byte_range")
        offset = previous[1][1] + 1
    end = offset + length - 1
    if end > MAX_BYTE_POSITION:
        raise ManifestError("invalid_hls_byte_range")
    return offset, end


def _absolute_uri(value, base):
    # Complete source-host/public-DNS checks are the registrar's responsibility.
    # Basic URI syntax is checked here before any registry side effect.
    if (not isinstance(value, str) or not isinstance(base, str) or not value or len(value) > LINE_LIMIT
            or re.search(r"[\s\\\x00-\x1f\x7f]", value)
            or "#" in value or "{$" in value):
        raise ManifestError("invalid_hls_uri")
    try:
        resolved = urljoin(base, value)
        parts = urlsplit(resolved)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port == 0 or any(char in parts.netloc for char in "@%")
                or len(resolved) > LINE_LIMIT
                or re.search(r"[\\\x00-\x1f\x7f]", unquote(parts.path + "?" + parts.query))):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ManifestError("invalid_hls_uri") from None
    return resolved


def _replace(attributes, name, value):
    return tuple(_Attribute(item.name, value, item.quoted) if item.name == name else item for item in attributes)


def _reference(value, base, kind, byte_range=None):
    return _Reference(_absolute_uri(value, base), kind, byte_range)


def _plan(body, base_url):
    if not isinstance(body, bytes) or not body or len(body) > MANIFEST_BYTES:
        raise ManifestError("hls_manifest_size_limit")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise ManifestError("invalid_hls_encoding") from None
    if _BAD_CONTROL.search(text) or text.count("\n") >= LINE_LIMIT:
        raise ManifestError("invalid_hls_lines")
    lines = text.split("\n")
    if lines[0].removesuffix("\r") != "#EXTM3U":
        raise ManifestError("invalid_hls_header")
    base = _absolute_uri(base_url, base_url)
    result = ["#EXTM3U"]
    mode, pending_variant, pending_segment, pending_range = None, False, False, None
    previous_segment, previous_map = None, None
    segments = variants = references = 0
    target_duration = ended = False
    singleton = set()

    def classify(wanted):
        nonlocal mode
        if mode is not None and mode != wanted:
            raise ManifestError("mixed_hls_manifest")
        mode = wanted

    for raw in lines[1:]:
        line = raw.removesuffix("\r")
        if "\r" in line or len(line) > LINE_LIMIT or line.strip() != line:
            raise ManifestError("invalid_hls_lines")
        if not line:
            continue
        if not line.startswith("#"):
            if ended:
                raise ManifestError("invalid_hls_structure")
            url = _absolute_uri(line, base)
            if pending_variant:
                variants += 1
                pending_variant = False
                result.append(_Reference(url, "manifest"))
            else:
                classify("media")
                if not pending_segment:
                    raise ManifestError("invalid_hls_structure")
                byte_range = None
                if pending_range is not None:
                    position, value = pending_range
                    byte_range = _resolved_range(value, url, previous_segment)
                    result[position] = f"#EXT-X-BYTERANGE:{value[0]}@{byte_range[0]}"
                    pending_range = None
                result.append(_Reference(url, "segment", byte_range))
                previous_segment = (url, byte_range) if byte_range is not None else None
                pending_segment = False
                segments += 1
            references += 1
        else:
            tag, separator, value = line.partition(":")
            if tag == "#EXTM3U":
                raise ManifestError("invalid_hls_header")
            if not tag.startswith("#EXT"):
                continue  # Comments do not affect playback or expose original URLs.
            if tag in _NO_ARGUMENT:
                if separator:
                    raise ManifestError("invalid_hls_structure")
                if tag != "#EXT-X-INDEPENDENT-SEGMENTS":
                    classify("media")
                if tag == "#EXT-X-ENDLIST":
                    if ended or pending_segment or pending_range is not None:
                        raise ManifestError("invalid_hls_structure")
                    ended = True
                if tag in {"#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-I-FRAMES-ONLY"}:
                    if tag in singleton:
                        raise ManifestError("invalid_hls_structure")
                    singleton.add(tag)
                result.append(line)
            elif tag in _MEDIA_INTEGER or tag == "#EXT-X-VERSION":
                if not separator or tag in singleton:
                    raise ManifestError("invalid_hls_structure")
                singleton.add(tag)
                _integer(value, positive=tag in {"#EXT-X-TARGETDURATION", "#EXT-X-VERSION"})
                if tag != "#EXT-X-VERSION":
                    classify("media")
                target_duration = target_duration or tag == "#EXT-X-TARGETDURATION"
                result.append(line)
            elif tag == "#EXTINF":
                classify("media")
                duration, comma, _title = value.partition(",")
                if not separator or not comma or pending_segment or ended:
                    raise ManifestError("invalid_hls_structure")
                _decimal(duration, positive=True)
                pending_segment = True
                result.append(f"#EXTINF:{duration},")
            elif tag == "#EXT-X-BYTERANGE":
                classify("media")
                if not separator or pending_range is not None or ended:
                    raise ManifestError("invalid_hls_structure")
                pending_range = (len(result), _range(value))
                result.append(line)
            elif tag == "#EXT-X-PLAYLIST-TYPE":
                classify("media")
                if not separator or value != "VOD" or tag in singleton:
                    raise ManifestError("unsupported_hls_playlist")
                singleton.add(tag)
                result.append(line)
            elif tag == "#EXT-X-PROGRAM-DATE-TIME":
                classify("media")
                # This tag is metadata; keep only its restricted timestamp syntax.
                if not separator or not re.fullmatch(r"[0-9TZ:+.\-]{10,64}", value):
                    raise ManifestError("invalid_hls_timestamp")
                result.append(line)
            elif tag == "#EXT-X-KEY":
                classify("media")
                attributes, mapping = _checked_attributes(value, {"METHOD"}, {"METHOD"})
                if not separator or mapping["METHOD"].value != "NONE":
                    raise ManifestError("unsupported_hls_encryption")
                result.append(_AttributeLine(tag, attributes))
            elif tag == "#EXT-X-MAP":
                classify("media")
                if ended:
                    raise ManifestError("invalid_hls_structure")
                attributes, mapping = _checked_attributes(value, {"URI", "BYTERANGE"}, {"URI"})
                url = _absolute_uri(mapping["URI"].value, base)
                byte_range = None
                if "BYTERANGE" in mapping:
                    parsed = _range(mapping["BYTERANGE"].value)
                    byte_range = _resolved_range(parsed, url, previous_map)
                    attributes = _replace(attributes, "BYTERANGE", f"{parsed[0]}@{byte_range[0]}")
                previous_map = (url, byte_range) if byte_range is not None else None
                result.append(_AttributeLine(tag, attributes, _Reference(url, "map", byte_range)))
                references += 1
            elif tag in {"#EXT-X-STREAM-INF", "#EXT-X-I-FRAME-STREAM-INF"}:
                classify("master")
                if pending_variant:
                    raise ManifestError("invalid_hls_structure")
                iframe = tag == "#EXT-X-I-FRAME-STREAM-INF"
                allowed = _STREAM_ATTRIBUTES | ({"URI"} if iframe else set())
                attributes, mapping = _checked_attributes(value, allowed, {"BANDWIDTH", "URI"} if iframe else {"BANDWIDTH"})
                reference = _reference(mapping["URI"].value, base, "manifest") if iframe else None
                result.append(_AttributeLine(tag, attributes, reference))
                if iframe:
                    variants += 1
                    references += 1
                else:
                    pending_variant = True
            elif tag == "#EXT-X-MEDIA":
                classify("master")
                if pending_variant:
                    raise ManifestError("invalid_hls_structure")
                attributes, mapping = _checked_attributes(value, _MEDIA_ATTRIBUTES, {"TYPE", "GROUP-ID", "NAME"})
                media_type = mapping["TYPE"].value
                if media_type not in {"AUDIO", "VIDEO", "SUBTITLES", "CLOSED-CAPTIONS"}:
                    raise ManifestError("unsupported_hls_rendition")
                if media_type == "SUBTITLES" and "URI" not in mapping or media_type == "CLOSED-CAPTIONS" and "URI" in mapping:
                    raise ManifestError("invalid_hls_attributes")
                reference = _reference(mapping["URI"].value, base, "manifest") if "URI" in mapping else None
                result.append(_AttributeLine(tag, attributes, reference))
                references += int(reference is not None)
            elif tag == "#EXT-X-START":
                if tag in singleton:
                    raise ManifestError("invalid_hls_structure")
                singleton.add(tag)
                attributes, mapping = _checked_attributes(value, {"TIME-OFFSET", "PRECISE"}, {"TIME-OFFSET"})
                _decimal(mapping["TIME-OFFSET"].value)
                result.append(_AttributeLine(tag, attributes))
            else:
                # Includes encryption session tags, LL-HLS, variable expansion,
                # steering, data and unknown URI-bearing extensions.
                raise ManifestError("unsupported_hls_tag")
        if references > REFERENCE_LIMIT:
            raise ManifestError("hls_reference_limit")
    if pending_variant or pending_segment or pending_range is not None:
        raise ManifestError("invalid_hls_structure")
    if mode == "media" and (not ended or not target_duration or not segments):
        raise ManifestError("unsupported_hls_playlist")
    if mode == "master" and not variants or mode is None:
        raise ManifestError("invalid_hls_structure")
    return result


def rewrite_manifest(body: bytes, base_url: str, register) -> bytes:
    """Rewrite validated VOD references via ``register(url, kind=, byte_range=)``.

    ``byte_range`` is an inclusive ``(start, end)`` pair. The callback must return
    an opaque same-origin absolute path. It must enforce source target policy;
    this parser never opens sockets. On callback failure the gateway must roll
    back/revoke the session's partially registered references.
    """
    plan = _plan(body, base_url)
    if not callable(register):
        raise ManifestError("invalid_hls_registrar")
    lines, total = [], 0

    def path(reference):
        result = register(reference.url, kind=reference.kind, byte_range=reference.byte_range)
        if (not isinstance(result, str) or not _RESOURCE_PATH.fullmatch(result)
                or any(part in {".", ".."} for part in result.split("/"))):
            raise ManifestError("invalid_hls_resource_path")
        return result

    for node in plan:
        if isinstance(node, str):
            line = node
        elif isinstance(node, _Reference):
            line = path(node)
        else:
            attributes = node.attributes
            if node.reference is not None:
                attributes = _replace(attributes, "URI", path(node.reference))
            line = node.tag + ":" + ",".join(
                f'{item.name}="{item.value}"' if item.quoted else f"{item.name}={item.value}"
                for item in attributes)
        total += len(line.encode("utf-8")) + 1
        if total > OUTPUT_BYTES:
            raise ManifestError("hls_output_size_limit")
        lines.append(line)
    return ("\n".join(lines) + "\n").encode("utf-8")
