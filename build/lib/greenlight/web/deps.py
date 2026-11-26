from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from greenlight.web.security import csrf_token

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
DISPLAY_TIMEZONE = ZoneInfo("Asia/Dubai")


def local_today() -> date:
    return datetime.now(DISPLAY_TIMEZONE).date()


def container_of(request: Request):
    return request.app.state.container


def render(request: Request, template: str, status_code: int = 200, **context):
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("flash", request.session.pop("flash", None))
    return TEMPLATES.TemplateResponse(
        request,
        template,
        {"csrf": csrf_token(request), "container": container_of(request), **context},
        status_code=status_code,
    )


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _usd(value: Decimal | None) -> str:
    if value is None:
        return "–"
    if value == 0:
        return "$0"
    return f"${value:,.0f}" if abs(value) >= 1000 else f"${value:,.2f}"


def _when(moment: datetime | None) -> str:
    if moment is None:
        return ""
    local = moment.astimezone(DISPLAY_TIMEZONE)
    days = (local_today() - local.date()).days
    if days == 0:
        return f"Today {local:%H:%M}"
    if days == 1:
        return f"Yesterday {local:%H:%M}"
    return f"{local:%d %b %Y, %H:%M}"


TEMPLATES.env.filters["usd"] = _usd
TEMPLATES.env.filters["when"] = _when
TEMPLATES.env.filters["day"] = lambda value: f"{value:%d %b %Y}" if value else ""
