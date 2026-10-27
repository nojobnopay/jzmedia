"""Bounded HTTP clients for explicit snapshots; no public fetch endpoint."""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from http.cookiejar import CookieJar, DefaultCookiePolicy
import logging
import ssl
import threading

import httpx

from .policy import NetworkSnapshot
from .guard import NetworkOperation, NetworkOperationError, current_operation, operation_context
from .headers import HeaderPolicy, HeaderPolicyError
from .targets import TargetPolicy, TargetPolicyError
from .transport import PinnedTransport


_private_operation = ContextVar("network_private_operation", default=False)
_filter_lock = threading.Lock()
_filtered = False
_THIRD_PARTY_LOGGERS = ("httpx", "httpcore.connection", "httpcore.proxy",
                        "httpcore.http11", "httpcore.http2", "httpcore.socks")
_logger = logging.getLogger("jzmedia.network")
RESPONSE_BODY_LIMIT = 2 * 1024 * 1024
RESPONSE_CHUNK_BYTES = 64 * 1024


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

    def _failed(self, error):
        category = _category(error)
        _logger.warning("network request failed: %s policy=%s purpose=%s",
                        category, self.snapshot.fingerprint, self.snapshot.purpose)
        return NetworkRequestError(category)

    def request(self, method, url, **kwargs):
        """Read a bounded small response; media consumers must use stream()."""
        with self.stream(method, url, **kwargs) as response:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise NetworkRequestError("encoded_response_not_supported")
            length = response.headers.get("content-length")
            has_body = response.request.method != "HEAD" and response.status_code not in {204, 304}
            if has_body and length is not None and length.isdigit() and int(length) > RESPONSE_BODY_LIMIT:
                raise NetworkRequestError("response_body_limit")
            chunks, total = [], 0
            for chunk in response.iter_raw(chunk_size=RESPONSE_CHUNK_BYTES):
                total += len(chunk)
                if total > RESPONSE_BODY_LIMIT:
                    raise NetworkRequestError("response_body_limit")
                chunks.append(chunk)
            return httpx.Response(response.status_code, headers=response.headers,
                                  content=b"".join(chunks), request=response.request,
                                  extensions=response.extensions)

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
        options = _request_options(kwargs)
        if not isinstance(method, str) or method.upper() not in {"GET", "HEAD"}:
            raise NetworkRequestError("unsupported_request_method")
        method = method.upper()
        supplied_headers = options.get("headers")
        with _private_logs():
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
                            response = stack.enter_context(self._client.stream(method, target.url, headers=headers,
                                                                               **stream_options))
                        except (TypeError, ValueError) as error:
                            raise self._failed(error) from None
                        if operation is not None:
                            network_stream = response.extensions.get("network_stream")
                            if callable(getattr(network_stream, "get_extra_info", None)):
                                sock = network_stream.get_extra_info("socket")
                                response.stream = _OperationResponseStream(response.stream, operation, sock)
                        if response.status_code not in {301, 302, 303, 307, 308}:
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

    def close(self):
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
