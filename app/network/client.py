"""Bounded HTTP clients for explicit snapshots; no public fetch endpoint."""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from http.cookiejar import CookieJar, DefaultCookiePolicy
import hashlib
import json
import logging
import os
import ssl
import stat
import threading

import certifi
import httpx

from .policy import NetworkSnapshot
from .guard import NetworkOperation, NetworkOperationError, check_operation, current_operation, operation_context
from .headers import HeaderPolicy, HeaderPolicyError
from .targets import TargetPolicy, TargetPolicyError
from .transport import PinnedTransport
from .stages import observe_request, response_status, stage


_private_operation = ContextVar("network_private_operation", default=False)
_filter_lock = threading.Lock()
_filtered = False
_THIRD_PARTY_LOGGERS = ("httpx", "httpcore.connection", "httpcore.proxy",
                        "httpcore.http11", "httpcore.http2", "httpcore.socks")
_logger = logging.getLogger("jzmedia.network")
RESPONSE_BODY_LIMIT = 2 * 1024 * 1024
RESPONSE_CHUNK_BYTES = 64 * 1024
TRUST_DIRECTORY_ENTRY_LIMIT = 512


def _trust_material(snapshot):
    """Bounded metadata snapshot also covers OpenSSL's lazy capath loading."""
    def metadata(value):
        return tuple(getattr(value, name) for name in (
            "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns"))

    def entry(path):
        check_operation()
        if not os.access(path, os.R_OK):
            raise OSError
        result = metadata(os.lstat(path)), metadata(os.stat(path))
        check_operation()
        return result

    try:
        value = {}
        ca_file = snapshot.ca_file or (certifi.where() if not snapshot.ca_dir else "")
        if ca_file:
            record = entry(ca_file)
            if not stat.S_ISREG(os.stat(ca_file).st_mode):
                return None
            value["file"] = (ca_file, record)
        if snapshot.ca_dir:
            before = entry(snapshot.ca_dir)
            if not stat.S_ISDIR(os.stat(snapshot.ca_dir).st_mode):
                return None
            children = []
            with os.scandir(snapshot.ca_dir) as entries:
                for index, child in enumerate(entries):
                    if index >= TRUST_DIRECTORY_ENTRY_LIMIT:
                        return None
                    children.append((child.name, entry(child.path)))
            after = entry(snapshot.ca_dir)
            if before != after:
                return None
            value["directory"] = (snapshot.ca_dir, before, sorted(children))
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).digest()
    except (OSError, TypeError, ValueError):
        return None


def _tls_reuse_policy(context):
    """An internal comparison only; never publish certificates or trust data."""
    try:
        if (not isinstance(context, ssl.SSLContext) or context.verify_mode != ssl.CERT_REQUIRED
                or not context.check_hostname):
            return None
        certificates = sorted({hashlib.sha256(value).hexdigest()
                               for value in context.get_ca_certs(binary_form=True)})
        settings = {name: getattr(context, name, None) for name in (
            "protocol", "verify_mode", "check_hostname", "verify_flags", "options",
            "minimum_version", "maximum_version", "security_level", "post_handshake_auth",
            "hostname_checks_common_name")}
        value = {"certificates": certificates, "settings": settings,
                 "ciphers": context.get_ciphers()}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).digest()
    except Exception:
        # Unreadable or unusual trust state is ineligible for this optimization.
        return None


class _OperationLogFilter(logging.Filter):
    def filter(self, _record):
        return not _private_operation.get()


def _install_log_filter():
    global _filtered
    with _filter_lock:
        if not _filtered:
            rule = _OperationLogFilter()
            # These are the sync HTTPX/httpcore logger names in the pinned
            # runtime. Other service requests retain their existing logging.
            for name in _THIRD_PARTY_LOGGERS:
                logging.getLogger(name).addFilter(rule)
            _filtered = True


@contextmanager
def _private_logs():
    token = _private_operation.set(True)
    try:
        yield
    finally:
        _private_operation.reset(token)


class NetworkRequestError(RuntimeError):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def _category(error):
    if isinstance(error, httpx.TimeoutException):
        return "network_timeout"
    if isinstance(error, httpx.ProxyError):
        return "proxy_connection_failed"
    return "network_request_failed"


def _request_options(values):
    if set(values) - {"headers", "params"}:
        raise NetworkRequestError("unsupported_request_options")
    try:
        headers = httpx.Headers(values.get("headers"))
    except (TypeError, ValueError, UnicodeError):
        raise NetworkRequestError("invalid_request_headers") from None
    if any(name in headers for name in ("proxy-authorization", "proxy-connection")):
        raise NetworkRequestError("reserved_proxy_headers")
    return values


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, *_args, **_kwargs):
        return False

    def return_ok(self, *_args, **_kwargs):
        return False


class _OperationResponseStream(httpx.SyncByteStream):
    """Invalidate socket cancellation before HTTPcore makes it reusable."""

    def __init__(self, stream, operation, sock):
        self._stream, self._operation, self._socket = stream, operation, sock
        self._closed = False

    def __iter__(self):
        yield from self._stream

    def close(self):
        if not self._closed:
            self._closed = True
            self._operation.release_socket(self._socket)
            self._stream.close()


class _ImageResponseStream(httpx.SyncByteStream):
    """HTTPX's consumed flag starts at iteration; retain actual EOF as well."""

    def __init__(self, stream):
        self._stream = stream
        self.complete = False

    def __iter__(self):
        yield from self._stream
        self.complete = True

    def close(self):
        self._stream.close()


class NetworkClient:
    """One independent connection pool and fixed route, with no implicit retry.

    Targets and upstream header grants are server-only dependencies. The normal
    online entry point additionally checks the source and feature enable state.
    """

    def __init__(self, snapshot: NetworkSnapshot, *, targets: TargetPolicy,
                 header_policy: HeaderPolicy | None = None):
        _install_log_filter()
        if (not isinstance(snapshot, NetworkSnapshot) or snapshot.mode not in {"direct", "proxy"}
                or not isinstance(snapshot.ssl_context, ssl.SSLContext)
                or snapshot.ssl_context.verify_mode != ssl.CERT_REQUIRED or not snapshot.ssl_context.check_hostname
                or (snapshot.mode == "proxy") != bool(snapshot.proxy_url)
                or snapshot.mode == "direct" and snapshot.proxy_auth is not None
                or not isinstance(targets, TargetPolicy) or targets.source_id != snapshot.source_id):
            raise NetworkRequestError("network_client_configuration_failed")
        self.snapshot = snapshot
        self.targets = targets
        self.header_policy = header_policy or HeaderPolicy(snapshot.source_id)
        if not isinstance(self.header_policy, HeaderPolicy) or self.header_policy.source_id != snapshot.source_id:
            raise NetworkRequestError("network_client_configuration_failed")
        self._reuse_lock = threading.Lock()
        self._reuse_origin = None
        self._reuse_eligible = True
        self._reuse_completed = False
        self._reuse_closed = False
        self._image_response = None
        self._image_stream_open = False
        self._reuse_trust_material = _trust_material(snapshot) if snapshot.purpose in {"api", "image"} else None
        try:
            self._client = httpx.Client(
                trust_env=False, follow_redirects=False,
                transport=PinnedTransport(snapshot, targets),
                cookies=CookieJar(policy=_NoCookies()),
                headers={"Accept-Encoding": "identity"},
                timeout=httpx.Timeout(connect=snapshot.connect_timeout_seconds,
                                      read=snapshot.read_timeout_seconds,
                                      write=snapshot.read_timeout_seconds,
                                      pool=snapshot.connect_timeout_seconds),
                limits=httpx.Limits(max_connections=snapshot.max_connections,
                                   max_keepalive_connections=snapshot.max_connections),
            )
        except (ValueError, TypeError, httpx.HTTPError):
            raise NetworkRequestError("network_client_configuration_failed") from None

    def __repr__(self):
        return f"NetworkClient(mode={self.snapshot.mode!r}, policy={self.snapshot.fingerprint!r})"

    def _reuse_target(self, target):
        with self._reuse_lock:
            if self._reuse_origin is None:
                self._reuse_origin = target.origin
            elif self._reuse_origin != target.origin:
                # Sticky even for a redirect that later returns to its origin.
                self._reuse_eligible = False

    def reusable_for(self, base_url):
        """Only an explicitly completed, single-origin bounded reader is reusable."""
        if (self.snapshot.purpose not in {"api", "image"} or self._reuse_trust_material is None
                or type(self.header_policy) is not HeaderPolicy
                or self.header_policy._grants):
            return False
        try:
            root = self.targets._canonical_url(base_url)[4]
            with self._reuse_lock:
                eligible = (self._reuse_eligible and self._reuse_completed and not self._reuse_closed
                            and self._reuse_origin == root)
            return eligible and self.targets.has_only_root_pin(base_url)
        except (TypeError, ValueError):
            return False

    def can_reuse(self, fresh, base_url):
        """Compare a fresh factory's route, trust and DNS without moving pins.

        Callers retain their admission and operation, then close the unused
        candidate. They must separately bind the current health observation.
        Neither a previous request nor its socket cancellation is retained.
        """
        if (not isinstance(fresh, NetworkClient) or fresh is self
                or self.snapshot != fresh.snapshot or not self.reusable_for(base_url)
                or fresh._reuse_trust_material is None
                or self._reuse_trust_material != fresh._reuse_trust_material
                or type(fresh.header_policy) is not HeaderPolicy or fresh.header_policy._grants):
            return False
        with fresh._reuse_lock:
            if fresh._reuse_closed or fresh._reuse_origin is not None or fresh._reuse_completed:
                return False
        check_operation()
        try:
            same_targets = self.targets.matches_fresh_root(fresh.targets, base_url)
        except TargetPolicyError as error:
            raise NetworkRequestError(str(error)) from None
        check_operation()
        if not same_targets:
            return False
        previous_trust = _tls_reuse_policy(self.snapshot.ssl_context)
        current_trust = _tls_reuse_policy(fresh.snapshot.ssl_context)
        check_operation()
        return previous_trust is not None and previous_trust == current_trust

    def accept_image_response(self, response):
        """Mark a validated image only after its own stream has fully closed.

        The image caller separately enforces its smaller byte/MIME limits and
        final access check. Generic stream and media users cannot qualify.
        """
        check_operation()
        with self._reuse_lock:
            owned = self._image_response
            accepted = (self.snapshot.purpose == "image" and not self._reuse_closed
                        and owned is not None and owned[0] is response and owned[1].complete
                        and not self._image_stream_open
                        and isinstance(response, httpx.Response) and response.status_code == 200
                        and response.is_closed and response.is_stream_consumed)
            self._image_response = None
            self._reuse_completed = accepted
            return accepted

    def _failed(self, error):
        category = _category(error)
        _logger.warning("network request failed: %s policy=%s purpose=%s",
                        category, self.snapshot.fingerprint, self.snapshot.purpose)
        return NetworkRequestError(category)

    def request(self, method, url, **kwargs):
        """Read a bounded small response; media consumers must use stream()."""
        with self._reuse_lock:
            self._reuse_completed = False
        with self.stream(method, url, **kwargs) as response:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise NetworkRequestError("encoded_response_not_supported")
            length = response.headers.get("content-length")
            has_body = response.request.method != "HEAD" and response.status_code not in {204, 304}
            if has_body and length is not None and length.isdigit() and int(length) > RESPONSE_BODY_LIMIT:
                raise NetworkRequestError("response_body_limit")
            chunks, total = [], 0
            with stage("body"):
                for chunk in response.iter_raw(chunk_size=RESPONSE_CHUNK_BYTES):
                    total += len(chunk)
                    if total > RESPONSE_BODY_LIMIT:
                        raise NetworkRequestError("response_body_limit")
                    chunks.append(chunk)
            result = httpx.Response(response.status_code, headers=response.headers,
                                    content=b"".join(chunks), request=response.request,
                                    extensions=response.extensions)
        with self._reuse_lock:
            self._reuse_completed = self.snapshot.purpose == "api" and result.status_code == 200
        return result

    @contextmanager
    def guarded_stream(self, method, url, operation: NetworkOperation, **kwargs):
        """Use one worker for open, iteration and close under a server budget."""
        try:
            with operation_context(operation):
                try:
                    with self.stream(method, url, **kwargs) as response:
                        operation.check()
                        yield response
                        operation.check()
                except NetworkRequestError:
                    # Socket shutdown can surface as an ordinary transport
                    # error; preserve the actual server-owned abort category.
                    operation.check()
                    raise
        except NetworkOperationError as error:
            raise NetworkRequestError(str(error)) from None
        finally:
            if isinstance(operation, NetworkOperation):
                operation.close()

    @contextmanager
    def stream(self, method, url, **kwargs):
        with self._reuse_lock:
            self._reuse_completed = False
            self._image_response = None
            self._image_stream_open = self.snapshot.purpose == "image"
        options = _request_options(kwargs)
        if not isinstance(method, str) or method.upper() not in {"GET", "HEAD"}:
            raise NetworkRequestError("unsupported_request_method")
        method = method.upper()
        supplied_headers = options.get("headers")
        with _private_logs(), observe_request(self.snapshot):
            try:
                target = self.targets.resolve(url)
                if options.get("params") is not None:
                    # Query parameters belong to the first request only; never
                    # re-append them to a redirect's Location.
                    try:
                        initial_url = str(httpx.URL(target.url).copy_merge_params(options["params"]))
                    except (TypeError, ValueError, UnicodeError):
                        raise NetworkRequestError("invalid_request_params") from None
                    target = self.targets.resolve(initial_url)
                seen = set()
                with ExitStack() as stack:
                    for hop in range(6):
                        self._reuse_target(target)
                        if target.url in seen:
                            raise NetworkRequestError("redirect_cycle")
                        seen.add(target.url)
                        headers = self.header_policy.headers_for(target, supplied_headers)
                        operation = current_operation()
                        stream_options = {}
                        if operation is not None:
                            # Pool waits precede socket I/O. They need the
                            # same server deadline, including short remaining
                            # TTLs, rather than only the snapshot's timeout.
                            stream_options["timeout"] = httpx.Timeout(
                                connect=operation.remaining(self.snapshot.connect_timeout_seconds),
                                read=operation.remaining(self.snapshot.read_timeout_seconds),
                                write=operation.remaining(self.snapshot.read_timeout_seconds),
                                pool=operation.remaining(self.snapshot.connect_timeout_seconds),
                            )
                        try:
                            with stage("response_headers"):
                                response = stack.enter_context(self._client.stream(method, target.url, headers=headers,
                                                                                   **stream_options))
                            response_status(response.status_code)
                        except (TypeError, ValueError) as error:
                            raise self._failed(error) from None
                        if operation is not None:
                            network_stream = response.extensions.get("network_stream")
                            if callable(getattr(network_stream, "get_extra_info", None)):
                                sock = network_stream.get_extra_info("socket")
                                response.stream = _OperationResponseStream(response.stream, operation, sock)
                        if response.status_code not in {301, 302, 303, 307, 308}:
                            if self.snapshot.purpose == "image":
                                image_stream = _ImageResponseStream(response.stream)
                                response.stream = image_stream
                                with self._reuse_lock:
                                    self._image_response = response, image_stream
                            yield response
                            return
                        locations = response.headers.get_list("location")
                        response.close()
                        if hop >= 5:
                            raise NetworkRequestError("redirect_limit")
                        if len(locations) != 1 or not locations[0]:
                            raise NetworkRequestError("invalid_redirect_location")
                        previous = target
                        target = self.targets.resolve(locations[0], base_url=previous.url)
                        if previous.scheme == "https" and target.scheme != "https":
                            raise NetworkRequestError("redirect_downgrade")
            except (TargetPolicyError, HeaderPolicyError) as error:
                raise NetworkRequestError(str(error)) from None
            except (httpx.HTTPError, httpx.InvalidURL) as error:
                raise self._failed(error) from None
            finally:
                # API request() and the explicit image completion hook mark
                # success only after this context and its response have closed.
                with self._reuse_lock:
                    self._reuse_completed = False
                    self._image_stream_open = False

    def close(self):
        with self._reuse_lock:
            self._reuse_closed = True
            self._image_response = None
        with _private_logs():
            try:
                self._client.close()
            except httpx.HTTPError as error:
                raise self._failed(error) from None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def create_client(snapshot: NetworkSnapshot, *, targets: TargetPolicy,
                  header_policy: HeaderPolicy | None = None) -> NetworkClient:
    return NetworkClient(snapshot, targets=targets, header_policy=header_policy)


def create_online_client(source_id: str, purpose: str, *,
                         header_policy: HeaderPolicy | None = None) -> NetworkClient:
    """Build an enabled source's guarded reader from one consistent DB view."""
    from ..store.online_network import load_online_network_state
    from .policy import NetworkPolicyError, resolve_policy_state

    state = load_online_network_state(source_id)
    source = state["source_config"]
    if not state["online_enabled"] or not source["enabled"]:
        raise NetworkPolicyError("online_source_disabled")
    snapshot = resolve_policy_state(state, purpose)
    targets = TargetPolicy(source_id, source["allowed_hosts"],
                           dns_timeout_seconds=snapshot.connect_timeout_seconds)
    client = create_client(snapshot, targets=targets, header_policy=header_policy)
    from ..online.health import bind_client
    bind_client(client, state)
    return client
