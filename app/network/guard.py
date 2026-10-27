"""Server-owned absolute I/O deadlines and detachable socket cancellation.

The current operation is scoped to one worker's complete response lifecycle.
Connection pools retain only dynamic socket adapters, never an old operation.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import math
import socket
import threading
import time


_current = ContextVar("network_operation", default=None)


class NetworkOperationError(RuntimeError):
    """A fixed boundary category without URL, headers or underlying errors."""


class _SocketRegistration:
    def __init__(self, sock):
        self.socket = sock
        self.active = True
        self.shutdown = None
        self.detach = None


class NetworkOperation:
    def __init__(self, deadline, cancelled=None, *, attach=None):
        if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or not math.isfinite(deadline) or deadline <= 0
                or cancelled is not None and not isinstance(cancelled, threading.Event)
                or attach is not None and not callable(attach)):
            raise NetworkOperationError("invalid_network_operation")
        self.deadline = float(deadline)
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self._attach = attach
        self._lock = threading.RLock()
        self._sockets = {}
        self._failure = None
        self._closed = False

    def __repr__(self):
        return f"NetworkOperation(closed={self._closed!r})"

    def remaining(self, timeout=None):
        remaining = self.deadline - time.monotonic()
        with self._lock:
            failure = self._failure
            if self._closed and failure is None:
                failure = "operation_cancelled"
        if failure is None:
            if remaining <= 0:
                failure = "operation_timeout"
            elif self.cancelled.is_set():
                failure = "operation_cancelled"
        if failure is not None:
            self.abort(failure)
            raise NetworkOperationError(failure)
        if timeout is not None:
            remaining = min(remaining, timeout)
        if remaining <= 0:
            raise NetworkOperationError("operation_timeout")
        return remaining

    def check(self):
        self.remaining()

    def bind_socket(self, sock):
        """Keep one shutdown callback per actual socket for this operation."""
        self.check()
        if sock is None or not callable(getattr(sock, "shutdown", None)):
            return
        identity = id(sock)
        registration = _SocketRegistration(sock)

        def shutdown():
            # A lease may already have copied this callback when response.close
            # releases the connection. Serialize invalidation with shutdown so
            # a queued old callback cannot affect its next pooled operation.
            with self._lock:
                if not registration.active:
                    return
                registration.active = False
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

        registration.shutdown = shutdown
        with self._lock:
            if identity in self._sockets:
                return
            self._sockets[identity] = registration

        detach = None
        if self._attach is not None:
            try:
                detach = self._attach(shutdown)
                if not callable(detach):
                    raise ValueError
            except Exception:
                shutdown()
                self._release_registration(registration)
                raise NetworkOperationError("operation_registration_failed") from None
        with self._lock:
            registration.detach = detach
            stale = (self._closed or self._failure is not None or self.cancelled.is_set()
                     or self._sockets.get(identity) is not registration)
        if stale:
            shutdown()
            self._release_registration(registration)
            self.check()
        self.check()

    def _release_registration(self, registration):
        with self._lock:
            identity = id(registration.socket)
            if self._sockets.get(identity) is registration:
                del self._sockets[identity]
            registration.active = False
            detach, registration.detach = registration.detach, None
        if detach is not None:
            try:
                detach()
            except Exception:
                pass

    def release_socket(self, sock):
        """Stop owning cancellation before a response returns its socket pool."""
        with self._lock:
            registration = self._sockets.get(id(sock))
        if registration is not None and registration.socket is sock:
            self._release_registration(registration)

    def abort(self, category="operation_cancelled"):
        if category not in {"operation_cancelled", "operation_timeout"}:
            category = "operation_cancelled"
        with self._lock:
            if self._failure is not None:
                return
            self._failure = category
            self.cancelled.set()
            callbacks = [registration.shutdown for registration in self._sockets.values()]
        for callback in callbacks:
            callback()

    def close(self):
        """Detach cancellation without closing healthy keep-alive sockets."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = tuple(self._sockets.values())
            self._sockets.clear()
            for registration in entries:
                registration.active = False
        for registration in entries:
            self._release_registration(registration)


@contextmanager
def operation_context(operation):
    if not isinstance(operation, NetworkOperation):
        raise NetworkOperationError("invalid_network_operation")
    operation.check()
    token = _current.set(operation)
    try:
        yield operation
    finally:
        _current.reset(token)


def current_operation():
    return _current.get()


def limit_timeout(timeout=None):
    operation = _current.get()
    return timeout if operation is None else operation.remaining(timeout)


def check_operation():
    operation = _current.get()
    if operation is not None:
        operation.check()


def bind_operation_socket(sock):
    operation = _current.get()
    if operation is not None:
        operation.bind_socket(sock)


class OperationSocket:
    """A transparent socket I/O adapter using the *current* operation.

    It is safe for an idle pooled stream to keep this adapter: each later
    operation installs its own deadline and cancellation registration.
    """

    def __init__(self, sock):
        self._sock = sock
        self._timeout = getattr(sock, "gettimeout", lambda: None)()

    def _prepare(self):
        bind_operation_socket(self._sock)
        self._sock.settimeout(limit_timeout(self._timeout))

    def settimeout(self, timeout):
        self._timeout = timeout
        self._prepare()

    def recv(self, *args):
        self._prepare()
        result = self._sock.recv(*args)
        check_operation()
        return result

    def send(self, *args):
        self._prepare()
        result = self._sock.send(*args)
        check_operation()
        return result

    def sendall(self, *args):
        self._prepare()
        self._sock.sendall(*args)
        check_operation()

    def __getattr__(self, name):
        return getattr(self._sock, name)


def wait_for_event(event, timeout):
    """Keep bounded DNS waits cancellable without starting another thread."""
    operation = _current.get()
    if operation is None:
        return event.wait(timeout)
    operation.check()
    deadline = min(operation.deadline, time.monotonic() + timeout)
    while True:
        operation.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return event.is_set()
        if event.wait(min(0.1, remaining)):
            operation.check()
            return True
