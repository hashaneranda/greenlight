from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from greenlight.domain.people import Employee
from greenlight.web.deps import container_of, render
from greenlight.web.security import verify_csrf

SESSION_USER_KEY = "employee_id"

router = APIRouter()


class SignInRequired(Exception):
    pass


def current_user(request: Request) -> Employee:
    employee_id = request.session.get(SESSION_USER_KEY)
    if not employee_id:
        raise SignInRequired
    person = container_of(request).queries.person(UUID(employee_id))
    if person is None or not person.is_active:
        request.session.clear()
        raise SignInRequired
    request.state.user = person
    return person


CurrentUser = Annotated[Employee, Depends(current_user)]


def is_local_path(url: str) -> bool:
    return url.startswith("/") and not url.startswith(("//", "/\\")) and not urlsplit(url).netloc


def _sign_in_as(request: Request, person: Employee) -> RedirectResponse:
    next_url = request.session.pop("next", "/")
    request.session.clear()
    request.session[SESSION_USER_KEY] = str(person.id)
    return RedirectResponse(next_url if is_local_path(next_url) else "/", status_code=303)


def _entra(request: Request):
    oauth: OAuth = request.app.state.oauth
    return oauth.entra


@router.get("/sign-in")
async def sign_in(request: Request):
    container = container_of(request)
    if container.settings.sign_in == "entra":
        redirect_uri = f"{container.settings.base_url}/auth/callback"
        return await _entra(request).authorize_redirect(request, redirect_uri)

    people = container.queries.people()
    lookups = container.queries.lookups()
    return render(request, "sign_in.html", people=people, lookups=lookups)


@router.post("/sign-in/demo", dependencies=[Depends(verify_csrf)])
async def demo_sign_in(request: Request, employee_id: Annotated[UUID, Form()]):
    container = container_of(request)
    if container.settings.sign_in != "demo":
        return RedirectResponse("/sign-in", status_code=303)
    person = container.queries.person(employee_id)
    if person is None or not person.is_active:
        return RedirectResponse("/sign-in", status_code=303)
    return _sign_in_as(request, person)


@router.get("/auth/callback")
async def entra_callback(request: Request):
    container = container_of(request)
    try:
        token = await _entra(request).authorize_access_token(request)
    except OAuthError:
        return render(
            request,
            "error.html",
            status_code=401,
            message="Microsoft sign-in did not complete. Please try again.",
        )
    claims = token.get("userinfo") or {}
    email = claims.get("email") or claims.get("preferred_username") or ""
    person = container.queries.person_by_email(email)
    if person is None or not person.is_active:
        return render(
            request,
            "error.html",
            status_code=403,
            message=f"{email} is not set up in Greenlight yet. Ask IT to add you.",
        )
    return _sign_in_as(request, person)


@router.post("/sign-out", dependencies=[Depends(verify_csrf)])
async def sign_out(request: Request):
    request.session.clear()
    return RedirectResponse("/sign-in", status_code=303)


def register_entra(oauth: OAuth, tenant_id: str, client_id: str, client_secret: str) -> None:
    oauth.register(
        "entra",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=(
            f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile"},
    )
