"""WebSocket fan-out inside one API worker (M10).

One Redis subscription per worker feeds `Hub.publish`, which is synchronous: it only
appends to each subscriber's bounded queue and never waits for a socket. A subscriber
whose queue is full is emptied and marked for a resnapshot; its writer then sends a
fresh snapshot before any later update. Too many overflows within a window close it.
A slow client therefore costs its own messages, never anyone else's, and never the
matcher's time (the matcher only publishes to Redis).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any


class _Marker:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


RESNAPSHOT = _Marker("RESNAPSHOT")  # send a fresh snapshot, then continue with later updates
CLOSED = _Marker("CLOSED")  # close the connection (slow consumer)


class Subscriber:
    def __init__(self, maxsize: int, *, max_overflows: int = 3, window_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic, initial_snapshot: bool = False) -> None:
        self.maxsize = maxsize
        self.max_overflows = max_overflows
        self.window_s = window_s
        self._clock = clock
        self._queue: deque[Any] = deque()
        self._needs_snapshot = initial_snapshot
        self._overflows: deque[float] = deque()
        self._event: asyncio.Event | None = None
        self._closed_seen = False
        self.closed = False
        self.close_reason: str | None = None
        self.dropped = 0
        self.on_close: Callable[[], None] | None = None

    def _wake(self) -> None:
        if self._event is not None:
            self._event.set()

    def offer(self, msg: Any) -> None:
        """Never blocks. Overflow: the queue is emptied and a resnapshot requested."""
        if self.closed:
            return
        if len(self._queue) >= self.maxsize:
            self.dropped += len(self._queue)
            self._queue.clear()
            self._needs_snapshot = True
            now = self._clock()
            self._overflows.append(now)
            while self._overflows and now - self._overflows[0] > self.window_s:
                self._overflows.popleft()
            if len(self._overflows) > self.max_overflows:
                self.close("slow consumer")
                return
        self._queue.append(msg)
        self._wake()

    def close(self, reason: str) -> None:
        if not self.closed:
            self.closed = True
            self.close_reason = reason
            self._queue.clear()
            self._wake()
            if self.on_close:
                self.on_close()

    def pending(self) -> bool:
        """Whether get() would return without waiting (CLOSED counts once)."""
        if self.closed:
            return not self._closed_seen
        return self._needs_snapshot or bool(self._queue)

    async def get(self) -> Any:
        """The next message, RESNAPSHOT, or CLOSED (forever, once closed)."""
        while True:
            if self.closed:
                self._closed_seen = True
                return CLOSED
            if self._needs_snapshot:
                self._needs_snapshot = False
                return RESNAPSHOT
            if self._queue:
                return self._queue.popleft()
            if self._event is None:
                self._event = asyncio.Event()
            self._event.clear()
            await self._event.wait()


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, list[Subscriber]] = {}
        self.on_first: Callable[[str], None] | None = None  # e.g. subscribe in Redis
        self.on_last: Callable[[str], None] | None = None

    def subscribe(self, channel: str, sub: Subscriber) -> None:
        subs = self._subs.setdefault(channel, [])
        subs.append(sub)
        if len(subs) == 1 and self.on_first:
            self.on_first(channel)

    def unsubscribe(self, channel: str, sub: Subscriber) -> None:
        subs = self._subs.get(channel, [])
        if sub in subs:
            subs.remove(sub)
        if not subs and channel in self._subs:
            del self._subs[channel]
            if self.on_last:
                self.on_last(channel)

    def channels(self) -> list[str]:
        return list(self._subs)

    def publish(self, channel: str, msg: Any) -> int:
        """Offers msg to every open subscriber of the channel; returns how many took it.
        Closed subscribers are removed."""
        subs = self._subs.get(channel)
        if not subs:
            return 0
        n = 0
        for sub in list(subs):
            sub.offer(msg)
            if sub.closed:
                self.unsubscribe(channel, sub)
            else:
                n += 1
        return n

    def resnapshot_all(self) -> None:
        """After a lost Redis subscription: every client may have missed updates."""
        for subs in self._subs.values():
            for sub in subs:
                sub._needs_snapshot = True
                sub._queue.clear()
                sub._wake()
