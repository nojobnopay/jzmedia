"""Fixture-only target policy and recorded session registry, shared by all scenarios."""

import threading

from app.network.targets import TargetPolicy, TargetPolicyError
from app.online.sessions import SessionRegistry


class FixtureTargetPolicy(TargetPolicy):
    """A fixture-only seam to one exact loopback origin; no runtime option."""

    def __init__(self, source_id, port):
        def addresses(host, requested_port):
            if host != "fixture.invalid" or requested_port != port:
                raise TargetPolicyError("fixture_target_not_allowed")
            return ("127.0.0.1",)

        super().__init__(source_id, ["fixture.invalid"], resolver=addresses)
        self._fixture_port = port

    def _validate_address(self, address):
        if address != "127.0.0.1":
            raise TargetPolicyError("fixture_target_not_allowed")
        return address

    def resolve(self, url, base_url=None):
        target = super().resolve(url, base_url=base_url)
        if target.scheme != "http" or target.port != self._fixture_port:
            raise TargetPolicyError("fixture_target_not_allowed")
        return target


class FixtureRegistry(SessionRegistry):
    def __init__(self):
        super().__init__()
        self.created = []
        self._record_lock = threading.Lock()

    def create(self, *args, **kwargs):
        session = super().create(*args, **kwargs)
        with self._record_lock:
            self.created.append(session.id)
            del self.created[:-2000]
        return session

    def recorded_ids(self):
        with self._record_lock:
            return tuple(self.created)
