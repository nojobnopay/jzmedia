"""Dial only previously authorized numeric targets while preserving HTTP origins.

The HTTPX 0.28.1 adapter remains responsible for HTTP messages and exceptions;
only its connection pool's network backend changes. HTTPcore 1.0.9 therefore
keeps the logical hostname in pool keys, Host, certificate checks and TLS SNI.
An operator-configured proxy is trusted to honor numeric CONNECT destinations.
Its target-side routing and remote peer cannot independently be observed here.
"""

import base64
import ipaddress
import math
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.sync import SyncStream, TLSinTLSStream

from .models import validate_proxy_url
from .guard import (OperationSocket, bind_operation_socket, check_operation,
                    limit_timeout)
from .policy import NetworkSnapshot
from .targets import TargetPolicyError, resolve_operator_addresses


CONNECT_HEADER_LIMIT = 16 * 1024
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_STATUS_LINE = re.compile(rb"HTTP/1\.[01] ([0-9]{3}) [\x09\x20-\x7e\x80-\xff]*\Z")
_TLS_SOCKET_CLASS = ssl.SSLSocket


def _remaining(deadline, timeout=None):
    remaining = deadline - time.monotonic()
    if timeout is not None:
        remaining = min(remaining, timeout)
    if remaining <= 0:
        raise httpcore.ConnectTimeout("network_connect_timeout")
    return limit_timeout(remaining)


def _direct_tls(stream, ssl_context, hostname, deadline, timeout=None):
    """Register a real TLS socket before its blocking handshake starts."""
    if not isinstance(stream, SyncStream):
        return stream.start_tls(ssl_context=ssl_context, server_hostname=hostname,
                                timeout=_remaining(deadline, timeout))
    sock = stream.get_extra_info("socket")
    secured = None
    try:
        sock.settimeout(_remaining(deadline, timeout))
        secured = ssl_context.wrap_socket(sock, server_hostname=hostname, do_handshake_on_connect=False)
        bind_operation_socket(secured)
        secured.do_handshake()
        _remaining(deadline, timeout)
        return SyncStream(secured)
    except socket.timeout:
        if secured is not None:
            secured.close()
        raise httpcore.ConnectTimeout("network_connect_timeout") from None
    except OSError:
        if secured is not None:
            secured.close()
        raise httpcore.ConnectError("network_connect_failed") from None
    except BaseException:
        if secured is not None:
            secured.close()
        raise


def _io_stream(stream):
    if isinstance(stream, SyncStream):
        return SyncStream(OperationSocket(stream.get_extra_info("socket")))
    return stream


def _numeric_address(value):
    if not isinstance(value, str) or "%" in value:
        raise httpcore.ConnectError("network_target_pin_invalid")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        raise httpcore.ConnectError("network_target_pin_invalid") from None


class _DeadlineSocket:
    """Bound each TLS-in-TLS handshake I/O, then restore normal timeouts."""

    def __init__(self, sock, deadline):
        self._sock, self._deadline, self._timeout = sock, deadline, None

    def settimeout(self, timeout):
        self._timeout = timeout
        self._set_operation_timeout()

    def _set_operation_timeout(self):
        timeout = self._timeout if self._deadline is None else _remaining(self._deadline, self._timeout)
        self._sock.settimeout(timeout)

    def _check_deadline(self):
        if self._deadline is not None:
            _remaining(self._deadline)

    def recv(self, *args):
        self._set_operation_timeout()
        result = self._sock.recv(*args)
        self._check_deadline()
        return result

    def sendall(self, *args):
        self._set_operation_timeout()
        self._sock.sendall(*args)
        self._check_deadline()

    def disarm(self):
        self._deadline = None

    def __getattr__(self, name):
        return getattr(self._sock, name)


class _ConnectDeadlineStream(httpcore.NetworkStream):
    """Extend the dial deadline through the target's subsequent TLS handshake."""

    def __init__(self, stream, deadline, host):
        self._stream, self._deadline, self._host = stream, deadline, host
        self._io = _io_stream(stream)

    def read(self, max_bytes, timeout=None):
        result = self._io.read(max_bytes, timeout=limit_timeout(timeout))
        check_operation()
        return result

    def write(self, buffer, timeout=None):
        self._io.write(buffer, timeout=limit_timeout(timeout))
        check_operation()

    def close(self):
        self._stream.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        # HTTPcore's optional sni_hostname extension cannot replace the
        # authorized logical hostname, even when using this adapter directly.
        try:
            if server_hostname != self._host:
                raise httpcore.ConnectError("network_tls_hostname_mismatch")
            sock = self._stream.get_extra_info("socket")
            deadline_socket = None
            if isinstance(self._stream, SyncStream) and isinstance(sock, _TLS_SOCKET_CLASS):
                # HTTPcore 1.0.9's TLS-in-TLS handshake otherwise resets a
                # socket's idle timeout on every read. Reuse its TLS state
                # machine with a socket that enforces the absolute deadline.
                deadline_socket = _DeadlineSocket(OperationSocket(sock), self._deadline)
                stream = TLSinTLSStream(deadline_socket, ssl_context, self._host,
                                        _remaining(self._deadline, timeout))
            else:
                stream = _direct_tls(self._stream, ssl_context, self._host, self._deadline, timeout)
            self._stream = stream
            self._io = _io_stream(stream)
            _remaining(self._deadline)
            if deadline_socket is not None:
                deadline_socket.disarm()
            return self
        except socket.timeout:
            self.close()
            raise httpcore.ConnectTimeout("network_connect_timeout") from None
        except OSError:
            self.close()
            raise httpcore.ConnectError("network_connect_failed") from None
        except BaseException:
            self.close()
            raise

    def get_extra_info(self, info):
        result = self._stream.get_extra_info(info)
        if info == "socket" and isinstance(result, _DeadlineSocket):
            result = result._sock
        if info == "socket" and isinstance(result, OperationSocket):
            result = result._sock
        return result


class PinnedBackend(httpcore.NetworkBackend):
    """One immutable route and a per-operation registry of approved addresses.

    Only a proxy endpoint may use operator-trusted hostname resolution. For
    HTTP and HTTPS targets alike, proxying uses a numeric CONNECT tunnel, never
    a forward request or a CONNECT that delegates target DNS to the proxy.
    """

    def __init__(self, snapshot, targets, *, network_backend=None):
        if (not isinstance(snapshot, NetworkSnapshot)
                or snapshot.source_id != targets.source_id
                or snapshot.mode not in {"direct", "proxy"}
                or not isinstance(snapshot.ssl_context, ssl.SSLContext)
                or snapshot.ssl_context.verify_mode != ssl.CERT_REQUIRED
                or not snapshot.ssl_context.check_hostname
                or (snapshot.mode == "proxy") != bool(snapshot.proxy_url)
                or snapshot.mode == "direct" and snapshot.proxy_auth is not None):
            raise ValueError("invalid_pinned_transport_configuration")
        self._snapshot, self._targets = snapshot, targets
        self._backend = httpcore.SyncBackend() if network_backend is None else network_backend
        self._proxy = None
        if snapshot.mode == "proxy":
            try:
                endpoint = validate_proxy_url(snapshot.proxy_url)
                self._proxy = urlsplit(endpoint)
            except ValueError:
                raise ValueError("invalid_pinned_transport_configuration") from None

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = self._targets.pin_for(host, port)
        except ValueError:
            raise httpcore.ConnectError("network_target_not_pinned") from None
        if not addresses or type(port) is not int or not 1 <= port <= 65535 or local_address is not None:
            raise httpcore.ConnectError("network_target_pin_invalid")
        # Validate the entire set before the first dial, including injected
        # backends. No hostname or scoped IPv6 value can slip into a fallback.
        addresses = tuple(dict.fromkeys(_numeric_address(value) for value in addresses))
        budget = self._snapshot.connect_timeout_seconds
        if timeout is not None:
            budget = min(budget, timeout)
        budget = limit_timeout(budget)
        if not math.isfinite(budget) or budget <= 0:
            raise httpcore.ConnectTimeout("network_connect_timeout")
        deadline = time.monotonic() + budget
        dial_addresses, dial_port = addresses, port
        if self._proxy is not None:
            dial_port = self._proxy.port or (443 if self._proxy.scheme == "https" else 80)
            try:
                dial_addresses = resolve_operator_addresses(self._proxy.hostname, dial_port, _remaining(deadline))
            except TargetPolicyError as error:
                if str(error) == "target_dns_timeout":
                    raise httpcore.ConnectTimeout("network_connect_timeout") from None
                raise httpcore.ProxyError("network_proxy_dns_failed") from None
            _remaining(deadline)
            dial_addresses = tuple(_numeric_address(value) for value in dial_addresses)
        last_timeout = False
        for dial_host in dial_addresses:
            address = dial_host if self._proxy is None else addresses[0]
            try:
                stream = self._backend.connect_tcp(
                    host=dial_host, port=dial_port, timeout=_remaining(deadline),
                    local_address=None, socket_options=socket_options,
                )
            except httpcore.ConnectTimeout:
                last_timeout = True
                _remaining(deadline)
                continue
            except httpcore.ConnectError:
                _remaining(deadline)
                continue
            try:
                _remaining(deadline)
                self._check_peer(stream, dial_host, dial_port)
                bind_operation_socket(stream.get_extra_info("socket"))
                if self._proxy is not None:
                    if self._proxy.scheme == "https":
                        stream = _direct_tls(stream, self._snapshot.ssl_context,
                                             self._proxy.hostname, deadline)
                        bind_operation_socket(stream.get_extra_info("socket"))
                        _remaining(deadline)
                    self._connect_tunnel(stream, address, port, deadline)
                return _ConnectDeadlineStream(stream, deadline, host)
            except BaseException:
                stream.close()
                raise
        if last_timeout:
            raise httpcore.ConnectTimeout("network_connect_timeout") from None
        raise httpcore.ConnectError("network_connect_failed") from None

    @staticmethod
    def _check_peer(stream, address, port):
        peer = stream.get_extra_info("server_addr")
        try:
            matches = (isinstance(peer, tuple) and len(peer) >= 2
                       and _numeric_address(peer[0]) == address
                       and type(peer[1]) is int and peer[1] == port)
        except httpcore.ConnectError:
            matches = False
        if not matches:
            raise httpcore.ConnectError("network_connected_peer_mismatch")

    def _connect_tunnel(self, stream, address, port, deadline):
        authority = f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
        headers = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}"]
        if self._snapshot.proxy_auth is not None:
            username, password = self._snapshot.proxy_auth
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {credentials}")
        try:
            request = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
            self._write_connect(stream, request, deadline)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                if len(response) >= CONNECT_HEADER_LIMIT:
                    raise httpcore.ProxyError("network_proxy_headers_too_large")
                size = min(4096, CONNECT_HEADER_LIMIT - len(response))
                part = stream.read(size, timeout=_remaining(deadline))
                _remaining(deadline)
                if not part or len(part) > size:
                    raise httpcore.ProxyError("network_proxy_response_invalid")
                response.extend(part)
        except (httpcore.ReadTimeout, httpcore.WriteTimeout):
            raise httpcore.ConnectTimeout("network_connect_timeout") from None
        except (httpcore.ReadError, httpcore.WriteError):
            raise httpcore.ProxyError("network_proxy_connection_failed") from None
        # An HTTP origin has received no request (nor ClientHello) yet. Reject
        # unsolicited tunnel bytes instead of losing them before target TLS.
        if response.find(b"\r\n\r\n") + 4 != len(response):
            raise httpcore.ProxyError("network_proxy_response_invalid")
        lines = bytes(response[:-4]).split(b"\r\n")
        status = _STATUS_LINE.fullmatch(lines[0])
        if status is None:
            raise httpcore.ProxyError("network_proxy_response_invalid")
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if (not separator or not _HEADER_NAME.fullmatch(name)
                    or any(byte < 32 and byte != 9 or byte == 127 for byte in value)):
                raise httpcore.ProxyError("network_proxy_response_invalid")
            if name.lower() == b"transfer-encoding" or name.lower() == b"content-length" and value.strip() != b"0":
                raise httpcore.ProxyError("network_proxy_response_invalid")
        code = int(status.group(1))
        if code == 407:
            raise httpcore.ProxyError("network_proxy_authentication_failed")
        if not 200 <= code <= 299:
            raise httpcore.ProxyError("network_proxy_connect_rejected")

    @staticmethod
    def _write_connect(stream, request, deadline):
        sock = stream.get_extra_info("socket")
        if sock is None:
            # Injectable test backends need only implement NetworkStream.
            stream.write(request, timeout=_remaining(deadline))
            _remaining(deadline)
            return
        # The installed SyncBackend returns SyncStream over a socket or (for
        # HTTPS proxies) SSLSocket. Each partial send receives the remaining
        # budget, without a byte-per-TLS-record penalty on a weak NAS.
        try:
            while request:
                sock.settimeout(_remaining(deadline))
                sent = sock.send(request)
                _remaining(deadline)
                if type(sent) is not int or not 0 < sent <= len(request):
                    raise httpcore.ProxyError("network_proxy_connection_failed")
                request = request[sent:]
        except socket.timeout:
            raise httpcore.ConnectTimeout("network_connect_timeout") from None
        except OSError:
            raise httpcore.ProxyError("network_proxy_connection_failed") from None

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("network_unix_socket_not_permitted")


class PinnedTransport(httpx.HTTPTransport):
    """HTTPX adapter with a pin-enforcing pool and unchanged logical origins."""

    def __init__(self, snapshot, targets, *, network_backend=None):
        backend = PinnedBackend(snapshot, targets, network_backend=network_backend)
        limits = httpx.Limits(max_connections=snapshot.max_connections,
                              max_keepalive_connections=snapshot.max_connections)
        super().__init__(verify=snapshot.ssl_context, trust_env=False, limits=limits, retries=0)
        self._pool.close()
        # HTTPX 0.28.1's installed handler owns the request/response conversion.
        # Keep this compatibility dependency small and covered with fake I/O.
        self._pool = httpcore.ConnectionPool(
            ssl_context=snapshot.ssl_context, max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry, network_backend=backend,
            http1=True, http2=False, retries=0,
        )
        self._targets = targets

    def handle_request(self, request):
        # A reused connection does not invoke connect_tcp. Check registration
        # on every request as well as at the actual dial boundary.
        if request.url.raw_scheme not in {b"http", b"https"}:
            raise httpx.UnsupportedProtocol("network_target_scheme_invalid", request=request)
        if "sni_hostname" in request.extensions:
            raise httpx.LocalProtocolError("network_tls_hostname_override_forbidden", request=request)
        if any(name in request.headers for name in ("proxy-authorization", "proxy-connection")):
            raise httpx.LocalProtocolError("network_proxy_headers_reserved", request=request)
        port = request.url.port or (443 if request.url.raw_scheme == b"https" else 80)
        host = request.url.raw_host.decode("ascii")
        authority = f"[{host}]" if ":" in host else host
        if port != (443 if request.url.raw_scheme == b"https" else 80):
            authority += f":{port}"
        if request.headers.get_list("host") != [authority]:
            raise httpx.LocalProtocolError("network_http_host_mismatch", request=request)
        try:
            self._targets.pin_for(host, port)
        except ValueError:
            raise httpx.ConnectError("network_target_not_pinned", request=request) from None
        return super().handle_request(request)
