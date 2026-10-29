"""Live updates pushed to the website and the Android app over WebSocket.

Single-process broadcaster. With several API workers, replace it by PostgreSQL
LISTEN/NOTIFY or Redis pub/sub (same publish()/subscribe() interface).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any


def _default(value: Any) -> str:
    if isinstance(value, (datetime, uuid.UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(type(value))


class Broadcaster:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._queues.discard(queue)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        """Thread-safe: may be called from sync request handlers."""
        message = json.dumps({"event": event, "data": data}, default=_default)
        if self._loop is None:
            return
        for queue in list(self._queues):
            self._loop.call_soon_threadsafe(self._put, queue, message)

    @staticmethod
    def _put(queue: asyncio.Queue[str], message: str) -> None:
        if queue.full():
            queue.get_nowait()  # drop the oldest message for slow clients
        queue.put_nowait(message)


broadcaster = Broadcaster()
