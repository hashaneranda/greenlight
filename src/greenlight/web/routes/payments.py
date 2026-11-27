from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse

from greenlight.domain.errors import DomainError, InvalidRequest
from greenlight.web.auth import CurrentUser
from greenlight.web.deps import container_of, flash, local_today, render
from greenlight.web.routes.requests import _money, _uploads
from greenlight.web.security import verify_csrf

router = APIRouter()


@router.get("/payments")
def ledger(request: Request, user: CurrentUser):
    approved = container_of(request).queries.payments_ledger(user.id)
    return render(
        request,
        "payments/ledger.html",
        to_pay=[r for r in approved if r.outstanding_usd],
        paid_in_full=[r for r in approved if not r.outstanding_usd],
        today=local_today(),
    )


@router.post("/payments/read-invoice", dependencies=[Depends(verify_csrf)])
async def read_invoice(request: Request, user: CurrentUser):
    container = container_of(request)
    uploads = await _uploads(await request.form(), "invoice")
    try:
        if not uploads:
            raise InvalidRequest("Choose an invoice to read.")
        if not request.app.state.ai_reads.allow(str(user.id)):
            raise InvalidRequest("You have read a lot of invoices this hour. Try again later.")
        check = container.approvals.read_invoice(user.id, uploads[0])
    except DomainError as error:
        flash(request, str(error), "error")
        return RedirectResponse("/payments#read", status_code=303)
    return render(
        request,
        "payments/matches.html",
        check=check,
        invoice=check.invoice,
        filename=uploads[0].filename,
        lookups=container.queries.lookups(),
        today=local_today(),
    )


@router.post("/requests/{reference}/payments", dependencies=[Depends(verify_csrf)])
async def record_payment(
    reference: str, request: Request, background: BackgroundTasks, user: CurrentUser
):
    form = await request.form()
    background.add_task(container_of(request).notifications.deliver_pending)
    try:
        paid_on = date.fromisoformat(form.get("paid_on") or local_today().isoformat())
        container_of(request).approvals.record_payment(
            user.id,
            reference,
            invoice_reference=form.get("invoice_reference", ""),
            amount=_money(form.get("amount", ""), form.get("currency", "")),
            paid_on=paid_on,
            confirm_over_approval=form.get("confirm_over_approval") == "yes",
        )
        flash(request, f"Payment recorded against {reference}.")
    except ValueError:
        flash(request, "Enter the payment date as a date.", "error")
    except DomainError as error:
        flash(request, str(error), "error")
    return RedirectResponse(f"/requests/{reference}", status_code=303)
