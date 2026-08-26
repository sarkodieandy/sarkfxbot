"""Correlation, security-header, and bounded in-process rate-limit middleware."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from threading import RLock
from uuid import uuid4

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from app.config.logging import bind_correlation_id, reset_correlation_id


class SlidingWindowRateLimiter:
    """Per-process HTTP limiter; deployments can place a stricter gateway in front."""

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        self._limit = requests_per_minute
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def allowed(self, key: str, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        cutoff = current - 60
        with self._lock:
            bucket = self._requests[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(current)
            return True


def install_http_middleware(app: object, requests_per_minute: int) -> None:
    limiter = SlidingWindowRateLimiter(requests_per_minute)

    @app.middleware("http")  # type: ignore[attr-defined,untyped-decorator]
    async def safety_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("x-correlation-id", "").strip()
        correlation_id = supplied[:128] if supplied else str(uuid4())
        token = bind_correlation_id(correlation_id)
        client_key = request.client.host if request.client is not None else "unknown"
        try:
            if request.url.path not in {"/health", "/ready", "/metrics"} and not limiter.allowed(
                client_key
            ):
                response: Response = JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "rate limit exceeded"},
                )
            else:
                response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            reset_correlation_id(token)
