"""
cogno_herald.ports — the storage seam for the OTP engine.

The parent hard-wired OTP state to Redis. Here it is a **port**: ``OTPStore`` is a
tiny KV-with-TTL Protocol the host implements (a Redis adapter is ~10 lines), and
``InMemoryOTPStore`` is the zero-dependency default for tests / single-process
hosts. Mirrors the homeo pattern (in-memory default + injectable store) so this
lib stays infra-agnostic with ``dependencies = []``.
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class OTPStore(Protocol):
    """A minimal KV store with per-key TTL. Keys/values are opaque strings."""

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    async def get(self, key: str) -> Optional[str]: ...

    async def delete(self, key: str) -> None: ...

    async def incr(self, key: str, ttl_seconds: int) -> int:
        """Atomically increment an integer counter at ``key`` (creating it at 1,
        stamping ``ttl_seconds`` on first creation) and return the new value."""
        ...

    async def exists(self, key: str) -> bool: ...


class InMemoryOTPStore:
    """Process-local ``OTPStore`` with lazy TTL expiry. Default for tests and
    single-worker hosts; swap for a Redis adapter in a multi-worker deployment."""

    def __init__(self) -> None:
        # key -> (value, expires_at_monotonic)
        self._kv: Dict[str, Tuple[str, float]] = {}

    def _live(self, key: str) -> Optional[str]:
        item = self._kv.get(key)
        if item is None:
            return None
        value, expires_at = item
        if time.monotonic() >= expires_at:
            self._kv.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._kv[key] = (value, time.monotonic() + ttl_seconds)

    async def get(self, key: str) -> Optional[str]:
        return self._live(key)

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)

    async def incr(self, key: str, ttl_seconds: int) -> int:
        current = self._live(key)
        if current is None:
            self._kv[key] = ("1", time.monotonic() + ttl_seconds)
            return 1
        nxt = int(current) + 1
        # preserve the existing expiry window
        self._kv[key] = (str(nxt), self._kv[key][1])
        return nxt

    async def exists(self, key: str) -> bool:
        return self._live(key) is not None
