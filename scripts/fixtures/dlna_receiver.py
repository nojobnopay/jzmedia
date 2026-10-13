"""Synthetic UPnP renderer for isolated tests; never announces itself on the LAN."""
from __future__ import annotations

import json
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen


class DlnaReceiver:
    def __init__(self, fetch_media=False):
        self.uri = ""
        self.position = 0
        self.state = "STOPPED"
        self.calls = []
        self.volume = 50
        self.reject = set()
        self.sink = "http-get:*:video/mp4:*,http-get:*:video/x-matroska:*"
        self.fetch_media = fetch_media
        self.fetched = b""
        self.fetch_errors = []
        self.report_uri = True
        self.metadata = ""
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/debug":
                    data = json.dumps({"calls": receiver.calls, "uri": receiver.uri,
                                       "fetched": len(receiver.fetched), "errors": receiver.fetch_errors}).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = f'''<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
                  <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
                  <friendlyName>合成 DLNA 电视 · 长名称兼容性测试</friendlyName>
                  <modelName>隔离模拟接收端</modelName><UDN>uuid:jzmedia-casting-test</UDN>
                  <serviceList>{"".join(f"<service><serviceType>urn:schemas-upnp-org:service:{name}:1</serviceType><controlURL>/control/{name}</controlURL></service>" for name in ("AVTransport", "RenderingControl", "ConnectionManager"))}</serviceList>
                </device></root>'''.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                root = ET.fromstring(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                command = list(root.find("{http://schemas.xmlsoap.org/soap/envelope/}Body"))[0]
                namespace, name = command.tag[1:].split("}")
                args = {item.tag: item.text or "" for item in command}
                receiver.calls.append({"name": name, "args": args})
                if name in receiver.reject:
                    data = b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>710</errorCode></UPnPError></detail></s:Fault></s:Body></s:Envelope>'
                    self.send_response(500)
                else:
                    values = receiver.command(name, args)
                    response = ET.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
                    body = ET.SubElement(response, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
                    action = ET.SubElement(body, f"{{{namespace}}}{name}Response")
                    for key, value in values.items():
                        ET.SubElement(action, key).text = str(value)
                    data = ET.tostring(response, encoding="utf-8")
                    self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def location(self):
        return f"http://127.0.0.1:{self.server.server_port}/description.xml"

    def command(self, name, args):
        from app.casting.dlna import seconds, time_text
        if name == "GetProtocolInfo":
            return {"Source": "", "Sink": self.sink}
        if name == "SetAVTransportURI":
            self.uri = args["CurrentURI"]
            self.metadata = args["CurrentURIMetaData"]
            self.position, self.state = 0, "STOPPED"
        elif name == "Play":
            self.state = "PLAYING"
            if self.fetch_media:
                threading.Thread(target=self._fetch, daemon=True).start()
        elif name == "Pause":
            self.state = "PAUSED_PLAYBACK"
        elif name == "Stop":
            self.state = "STOPPED"
        elif name == "Seek":
            self.position = seconds(args["Target"])
        elif name == "SetVolume":
            self.volume = int(args["DesiredVolume"])
        elif name == "GetTransportInfo":
            return {"CurrentTransportState": self.state, "CurrentTransportStatus": "OK", "CurrentSpeed": 1}
        elif name == "GetPositionInfo":
            return {"TrackURI": self.uri if self.report_uri else "", "RelTime": time_text(self.position),
                    "TrackDuration": "00:10:00"}
        return {}

    def _fetch(self):
        try:
            with urlopen(Request(self.uri, headers={"Range": "bytes=0-63"}), timeout=5) as response:
                self.fetched = response.read()
        except Exception as exc:
            self.fetch_errors.append(str(exc))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
