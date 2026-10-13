"""Bounded SSDP discovery and UPnP AVTransport/RenderingControl commands."""
from __future__ import annotations

import hashlib
import ipaddress
import select
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

SSDP_TARGET = ("239.255.255.250", 1900)
MAX_XML = 128 * 1024
MAX_DEVICES = 32
NS = "urn:schemas-upnp-org:device-1-0"


class CastError(Exception):
    """A bounded, user-facing receiver error (never a raw URL or response body)."""


def lan_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
        return ip.version == 4 and any(ip in net for net in (
            ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16")))
    except ValueError:
        return False


def device_url(value: str, peer: str) -> str:
    """Discovery may only fetch the responding LAN IP; no DNS or redirects."""
    try:
        url = urlsplit(value)
        if (not lan_ip(peer) or url.scheme != "http" or url.hostname != peer
                or url.username or url.password or url.fragment
                or not 1 <= (80 if url.port is None else url.port) <= 65535):
            raise ValueError
    except ValueError as exc:
        raise CastError("设备返回了无效的局域网地址") from exc
    return value


def parse_xml(data: bytes) -> ET.Element:
    # ElementTree does not load external entities; reject internal DTDs too.
    markup = data.replace(b"\x00", b"").upper()
    if len(data) > MAX_XML or b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
        raise CastError("设备响应格式无效")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise CastError("设备响应格式无效") from exc


def _http(method: str, url: str, **kwargs) -> bytes:
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            with client.stream(method, url, **kwargs) as response:
                data = bytearray()
                deadline = time.monotonic() + 8
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_XML or time.monotonic() > deadline:
                        raise CastError("设备响应过大")
                # SOAP faults are returned with HTTP 500 by many televisions.
                if response.status_code == 500 and data:
                    root = parse_xml(bytes(data))
                    code = next((e.text for e in root.iter() if e.tag.endswith("}errorCode")), "")
                    if code:
                        messages = {"701": "电视当前状态不允许此操作", "710": "电视不支持跳转进度",
                                    "711": "电视无法跳转到此位置", "714": "电视不支持此视频格式",
                                    "716": "电视无法访问视频，请检查服务器地址和网络"}
                        raise CastError(messages.get(code, f"电视拒绝操作（UPnP {code[:8]}）"))
                if response.status_code != 200:
                    raise CastError(f"电视请求失败（HTTP {response.status_code}）")
                return bytes(data)
    except httpx.HTTPError as exc:
        raise CastError("无法连接电视，请确认设备在线并已开启 DLNA 接收") from exc


@dataclass(frozen=True)
class Service:
    type: str
    url: str


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    address: str
    model: str
    transport: Service
    rendering: Service | None = None
    connection: Service | None = None

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "address": self.address,
                "model": self.model, "volume_control": self.rendering is not None}


def describe(location: str, peer: str) -> Device:
    location = device_url(location, peer)
    root = parse_xml(_http("GET", location))
    base = root.findtext(f"{{{NS}}}URLBase") or location
    device_url(base, peer)
    for node in root.iter(f"{{{NS}}}device"):
        dtype = node.findtext(f"{{{NS}}}deviceType") or ""
        if not dtype.startswith("urn:schemas-upnp-org:device:MediaRenderer:"):
            continue
        services = {}
        for item in node.findall(f"{{{NS}}}serviceList/{{{NS}}}service"):
            stype = item.findtext(f"{{{NS}}}serviceType") or ""
            parts = stype.split(":")
            if (len(parts) != 5 or parts[:3] != ["urn", "schemas-upnp-org", "service"]
                    or not parts[4].isdigit()):
                continue
            control = item.findtext(f"{{{NS}}}controlURL")
            if control and parts[3] in ("AVTransport", "RenderingControl", "ConnectionManager"):
                services[parts[3]] = Service(stype, device_url(urljoin(base, control), peer))
        if "AVTransport" not in services:
            continue
        udn = node.findtext(f"{{{NS}}}UDN") or location
        key = hashlib.sha256(f"{peer}|{udn}".encode()).hexdigest()[:24]
        return Device(key, (node.findtext(f"{{{NS}}}friendlyName") or "DLNA 电视")[:120],
                      peer, (node.findtext(f"{{{NS}}}modelName") or "")[:120],
                      services["AVTransport"], services.get("RenderingControl"),
                      services.get("ConnectionManager"))
    raise CastError("设备没有提供 DLNA 视频播放服务")


def describe_location(location: str) -> Device:
    """Resolve a user-supplied description URL without DNS or redirect access."""
    location = location.strip()
    try:
        peer = urlsplit(location).hostname or ""
    except ValueError:
        peer = ""
    return describe(location, peer)


def ssdp_locations(timeout: float = 3) -> list[tuple[str, str]]:
    packet = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
              'MAN: "ssdp:discover"\r\nMX: 2\r\n'
              "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n").encode()
    locations = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.bind(("0.0.0.0", 0))
            sock.setblocking(False)
            sock.sendto(packet, SSDP_TARGET)
            sock.sendto(packet, SSDP_TARGET)
            deadline = time.monotonic() + timeout
            packets = 0
            while time.monotonic() < deadline and packets < 256 and len(locations) < MAX_DEVICES:
                ready, _, _ = select.select([sock], [], [], max(0, deadline - time.monotonic()))
                if not ready:
                    break
                data, peer = sock.recvfrom(8192)
                packets += 1
                lines = data.decode("latin1").split("\r\n")
                if not lines[0].startswith("HTTP/1.1 200"):
                    continue
                headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
                headers = {key.lower().strip(): value.strip() for key, value in headers.items()}
                try:
                    locations.add((device_url(headers.get("location", ""), peer[0]), peer[0]))
                except CastError:
                    continue
    except OSError as exc:
        raise CastError("无法搜索局域网设备，请检查服务器网络及 UDP 1900 组播") from exc
    return sorted(locations)


def discover() -> list[Device]:
    from concurrent.futures import ThreadPoolExecutor
    def one(location):
        try:
            return describe(*location)
        except CastError:
            return None
    locations = ssdp_locations()
    with ThreadPoolExecutor(max_workers=8) as pool:
        devices = {d.id: d for d in pool.map(one, locations) if d is not None}
    return sorted(devices.values(), key=lambda d: (d.name, d.id))


def action(service: Service, name: str, arguments: dict) -> dict:
    envelope = ET.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope",
                          {"{http://schemas.xmlsoap.org/soap/envelope/}encodingStyle":
                           "http://schemas.xmlsoap.org/soap/encoding/"})
    body = ET.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    command = ET.SubElement(body, f"{{{service.type}}}{name}")
    for key, value in arguments.items():
        ET.SubElement(command, key).text = str(value)
    root = parse_xml(_http("POST", service.url,
                          content=ET.tostring(envelope, encoding="utf-8", xml_declaration=True),
                          headers={"Content-Type": 'text/xml; charset="utf-8"',
                                   "SOAPAction": f'"{service.type}#{name}"'}))
    response = root.find(f".//{{{service.type}}}{name}Response")
    if response is None:
        raise CastError("电视返回了无效的控制响应")
    return {node.tag.split("}")[-1]: node.text or "" for node in response}


def time_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def seconds(value: str) -> float | None:
    try:
        parts = value.split(":")
        if len(parts) != 3:
            return None
        h, m, s = map(float, parts)
        if not (h >= 0 and 0 <= m < 60 and 0 <= s < 60):
            return None
        result = h * 3600 + m * 60 + s
        return result if result < 7 * 86400 else None
    except (ValueError, OverflowError):
        return None


def metadata(title: str, uri: str, mime: str, duration: float, size: int) -> str:
    ns = "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"
    root = ET.Element("DIDL-Lite", {"xmlns": ns, "xmlns:dc": "http://purl.org/dc/elements/1.1/",
                                    "xmlns:upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/"})
    item = ET.SubElement(root, "item", {"id": "0", "parentID": "-1", "restricted": "1"})
    ET.SubElement(item, "dc:title").text = title[:200]
    ET.SubElement(item, "upnp:class").text = "object.item.videoItem"
    ET.SubElement(item, "res", {"protocolInfo": f"http-get:*:{mime}:*",
                               "duration": time_text(duration), "size": str(size)}).text = uri
    return ET.tostring(root, encoding="unicode")
