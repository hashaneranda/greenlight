import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

CSRF_SESSION_KEY = "csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_FILES_PER_FORM = 5
MAX_FIELDS_PER_FORM = 50
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' data:",
        "style-src 'self'",
        "style-src-attr 'unsafe-inline'",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "form-action 'self' https://login.microsoftonline.com",
        "base-uri 'none'",
    ]
)


def csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if token is None:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def verify_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    form = await request.form(max_files=MAX_FILES_PER_FORM, max_fields=MAX_FIELDS_PER_FORM)
    submitted = form.get("csrf") or request.headers.get("x-csrf-token", "")
    expected = request.session.get(CSRF_SESSION_KEY, "")
    if not expected or not secrets.compare_digest(str(submitted), expected):
        raise HTTPException(403, "Your session expired. Go back, refresh and try again.")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class BodySizeLimit:
    """Rejects request bodies over the limit before they are buffered or parsed."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await _too_large(send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(413, "That upload is too large.")
            return message

        await self.app(scope, limited_receive, send)


async def _too_large(send) -> None:
    body = b"That upload is too large."
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class HourlyLimit:
    """Per-person limit for costly actions. Kept in memory, so each instance counts on its own."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        calls = self._calls[key]
        while calls and now - calls[0] > 3600:
            calls.popleft()
        if len(calls) >= self.per_hour:
            return False
        calls.append(now)
        return True
