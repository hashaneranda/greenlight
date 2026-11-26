import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

CSRF_SESSION_KEY = "csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
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
    form = await request.form()
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
