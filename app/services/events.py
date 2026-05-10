from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator


class EventLog:
    def __init__(self, limit: int = 500) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=limit)
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def append(self, level: str, subsystem: str, message: str, **fields: Any) -> dict[str, Any]:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "subsystem": subsystem,
            "message": message,
            **fields,
        }
        self._events.appendleft(event)
        for queue in self._subscribers:
            queue.put_nowait(event)
        return event

    def list(self) -> list[dict[str, Any]]:
        return list(self._events)

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[dict[str, Any]], None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            yield queue
        finally:
            self._subscribers.remove(queue)
