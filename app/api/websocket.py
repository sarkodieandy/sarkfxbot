"""Authenticated WebSocket fan-out for prices, signals, positions, P&L, and health."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


def websocket_bearer_token(websocket: WebSocket, query_token: str = "") -> str | None:
    """Extract a bearer token from the header, with a query fallback for WS clients."""

    authorization = websocket.headers.get("authorization", "").strip()
    scheme, separator, credentials = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip()
    fallback = query_token.strip()
    return fallback or None


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, event: str, payload: Mapping[str, Any]) -> int:
        envelope = {
            "event": event,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": dict(payload),
        }
        async with self._lock:
            targets = tuple(self._connections)
        delivered = 0
        stale: list[WebSocket] = []
        for connection in targets:
            try:
                await connection.send_json(envelope)
                delivered += 1
            except (RuntimeError, OSError, WebSocketDisconnect):
                stale.append(connection)
        if stale:
            async with self._lock:
                for connection in stale:
                    self._connections.discard(connection)
        return delivered

    @property
    def connection_count(self) -> int:
        return len(self._connections)
