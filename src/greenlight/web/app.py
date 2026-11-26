import logging
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from greenlight.application.errors import NotFound
from greenlight.config import Settings, get_settings
from greenlight.domain.errors import NotPermitted
from greenlight.web import auth
from greenlight.web.container import Container, build_container
from greenlight.web.deps import render
from greenlight.web.routes import admin, cover, home, payments, requests
from greenlight.web.security import SecurityHeaders


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(title="Greenlight", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = container or build_container(settings)
    app.state.oauth = OAuth()
    if settings.sign_in == "entra":
        auth.register_entra(
            app.state.oauth,
            settings.entra_tenant_id,
            settings.entra_client_id,
            settings.entra_client_secret.get_secret_value(),
        )

    app.add_middleware(SecurityHeaders)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie="greenlight_session",
        max_age=12 * 60 * 60,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    @app.exception_handler(auth.SignInRequired)
    async def sign_in_required(request: Request, _):
        if request.method == "GET":
            request.session["next"] = request.url.path
        return RedirectResponse("/sign-in", status_code=303)

    @app.exception_handler(NotPermitted)
    async def not_permitted(request: Request, error: NotPermitted):
        return render(request, "error.html", status_code=403, message=str(error))

    @app.exception_handler(NotFound)
    async def not_found(request: Request, error: NotFound):
        return render(request, "error.html", status_code=404, message=str(error))

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")
    for module in (auth, home, requests, payments, cover, admin):
        app.include_router(module.router)
    return app
