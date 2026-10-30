"""Image cache budgets and request coalescing bookkeeping stay bounded."""

import pytest

from app.online.artwork_cache import ArtworkCache, ArtworkCacheBusy, MAX_IMAGE_BYTES


IMAGE = (b"\xff\xd8\xffdata", "image/jpeg")


def store(cache, key, value=IMAGE):
    result, flight, producer = cache.acquire(key)
    assert result is None and producer
    try:
        cache.complete(key, flight, result=value)
    finally:
        cache.release()


def lookup(cache, key):
    result, flight, _producer = cache.acquire(key)
    if flight is not None:
        cache.complete(key, flight)
        cache.release()
    return result


def test_cache_bounds_bytes_and_records_with_lru_eviction():
    cache = ArtworkCache(capacity=2, byte_budget=len(IMAGE[0]) * 2)
    store(cache, "first")
    store(cache, "second")
    assert lookup(cache, "first") == IMAGE
    store(cache, "third")
    assert lookup(cache, "first") == IMAGE
    assert lookup(cache, "second") is None
    assert lookup(cache, "third") == IMAGE
    assert cache.stats() == {"bytes": len(IMAGE[0]) * 2, "entries": 2,
                             "inflight": 0, "pending": 0, "waiters": 0}


def test_byte_budget_can_evict_before_the_entry_count_is_exhausted():
    cache = ArtworkCache(capacity=10, byte_budget=len(IMAGE[0]) * 2)
    for key in range(5):
        store(cache, key)
    assert cache.stats()["entries"] == 2
    assert cache.stats()["bytes"] == len(IMAGE[0]) * 2
    assert lookup(cache, 2) is None
    assert lookup(cache, 3) == lookup(cache, 4) == IMAGE


def test_cache_age_is_absolute_and_cache_hits_do_not_extend_it():
    now = [0.0]
    cache = ArtworkCache(ttl=2, clock=lambda: now[0])
    store(cache, "image")
    now[0] = 1
    assert lookup(cache, "image") == IMAGE
    now[0] = 2
    assert lookup(cache, "image") is None
    assert cache.stats()["bytes"] == cache.stats()["entries"] == 0


def test_images_larger_than_the_cache_budget_are_not_retained():
    cache = ArtworkCache(byte_budget=1)
    store(cache, "image")
    assert lookup(cache, "image") is None
    assert cache.stats()["bytes"] == 0


def test_promoting_a_disk_hit_cannot_extend_its_remaining_absolute_lifetime():
    now = [10.0]
    cache = ArtworkCache(clock=lambda: now[0])
    _result, flight, producer = cache.acquire("disk-hit")
    assert producer
    cache.complete("disk-hit", flight, result=IMAGE, ttl=2)
    cache.release()
    now[0] = 11.9
    assert lookup(cache, "disk-hit") == IMAGE
    now[0] = 12.0
    assert lookup(cache, "disk-hit") is None
    _result, flight, _producer = cache.acquire("expired")
    cache.complete("expired", flight, result=IMAGE, ttl=0)
    cache.release()
    assert lookup(cache, "expired") is None


@pytest.mark.parametrize("options", [
    {"capacity": 0}, {"capacity": 513}, {"capacity": True},
    {"byte_budget": 0}, {"byte_budget": 16 * 1024**2 + 1},
    {"ttl": 0}, {"ttl": 601}, {"ttl": True}, {"ttl": float("nan")},
    {"pending_limit": 0}, {"pending_limit": 65},
])
def test_invalid_or_raised_cache_limits_are_rejected(options):
    with pytest.raises(ValueError):
        ArtworkCache(**options)


@pytest.mark.parametrize("value", [
    (b"", "image/jpeg"), (b"x" * (MAX_IMAGE_BYTES + 1), "image/jpeg"),
    (b"jpeg", "image/svg+xml"), (bytearray(b"jpeg"), "image/jpeg"),
])
def test_cache_cannot_store_oversized_mutable_or_unapproved_bodies(value):
    cache = ArtworkCache()
    with pytest.raises(ValueError):
        store(cache, "invalid", value)
    assert cache.stats()["bytes"] == cache.stats()["entries"] == cache.stats()["pending"] == 0
    assert cache.stats()["inflight"] == 0


def test_pending_limit_applies_to_waiters_too_but_does_not_block_cache_hits():
    cache = ArtworkCache(pending_limit=2)
    store(cache, "cached")
    _value, flight, producer = cache.acquire("pending")
    assert producer
    _value, same, producer = cache.acquire("pending")
    assert not producer and same is flight
    try:
        assert cache.stats()["pending"] == 2
        assert cache.stats()["waiters"] == 1
        with pytest.raises(ArtworkCacheBusy):
            cache.acquire("another")
        assert lookup(cache, "cached") == IMAGE
    finally:
        cache.complete("pending", flight)
        cache.release()
        cache.release()
    assert cache.stats()["pending"] == cache.stats()["inflight"] == 0


def test_clearing_cached_bytes_does_not_create_a_duplicate_producer():
    cache = ArtworkCache()
    store(cache, "cached")
    _value, flight, producer = cache.acquire("pending")
    assert producer
    cache.clear()
    assert cache.stats()["bytes"] == cache.stats()["entries"] == 0
    _value, second, producer = cache.acquire("pending")
    assert second is flight and not producer
    cache.complete("pending", flight, result=IMAGE)
    cache.release()
    cache.release()
    assert lookup(cache, "pending") == IMAGE
    assert cache.stats()["pending"] == cache.stats()["inflight"] == 0
