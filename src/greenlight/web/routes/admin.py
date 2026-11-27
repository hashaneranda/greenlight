import secrets
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from greenlight.application.audit_chain import broken_links
from greenlight.domain.errors import DomainError
from greenlight.web.auth import CurrentUser
from greenlight.web.deps import container_of, flash, render
from greenlight.web.security import verify_csrf

router = APIRouter()


@router.get("/audit")
def audit_log(request: Request, user: CurrentUser):
    queries = container_of(request).queries
    entries = queries.audit_log(user.id)
    in_order = list(reversed(entries))
    tampered = broken_links(in_order)
    return render(
        request,
        "audit.html",
        entries=entries[:500],
        total=len(entries),
        tampered=tampered,
        first_broken_number=in_order.index(tampered[0]) + 1 if tampered else None,
        references={r.id: r.reference for r in queries.visible_requests(user.id)},
        lookups=queries.lookups(),
    )


@router.get("/rules")
def rules(request: Request, user: CurrentUser):
    return render(request, "rules.html", rules=container_of(request).queries.rules(user.id))


@router.post("/rules/coo-limit", dependencies=[Depends(verify_csrf)])
async def change_coo_limit(request: Request, user: CurrentUser):
    form = await request.form()
    try:
        amount = Decimal(form.get("amount_usd", "").replace(",", ""))
        container_of(request).approvals.change_coo_limit(
            user.id,
            UUID(form.get("category_id", "")),
            amount,
            suggested=form.get("suggested") == "yes",
        )
        flash(request, "Rule changed. It applies to requests raised from now on.")
    except (ValueError, ArithmeticError):
        flash(request, "Enter the limit as a number of US dollars.", "error")
    except DomainError as error:
        flash(request, str(error), "error")
    return RedirectResponse("/rules", status_code=303)


@router.post("/trips", dependencies=[Depends(verify_csrf)])
async def open_trip(request: Request, user: CurrentUser):
    form = await request.form()
    try:
        starts_on = date.fromisoformat(form["starts_on"]) if form.get("starts_on") else None
        ends_on = date.fromisoformat(form["ends_on"]) if form.get("ends_on") else None
        container_of(request).approvals.open_trip(user.id, form.get("name", ""), starts_on, ends_on)
        flash(request, "Trip added. Everyone travelling can now tag their spend to it.")
    except ValueError:
        flash(request, "Enter the trip dates as dates.", "error")
    except DomainError as error:
        flash(request, str(error), "error")
    return RedirectResponse("/requests/new", status_code=303)


def _require_cron_token(request: Request, authorization: Annotated[str, Header()] = "") -> None:
    expected = container_of(request).settings.cron_token.get_secret_value()
    supplied = authorization.removeprefix("Bearer ").strip()
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401)


@router.post("/jobs/reminders", dependencies=[Depends(_require_cron_token)])
def send_reminders(request: Request):
    container = container_of(request)
    hours = container.settings.reminder_after_hours
    reminded = container.approvals.send_reminders(timedelta(hours=hours))
    delivered = container.notifications.deliver_pending()
    return {"reminded": reminded, "delivered": delivered}


@router.post("/jobs/deliver", dependencies=[Depends(_require_cron_token)])
def deliver(request: Request):
    return {"delivered": container_of(request).notifications.deliver_pending()}


@router.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}
