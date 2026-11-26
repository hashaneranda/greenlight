from decimal import Decimal, InvalidOperation
from urllib.parse import quote as quote_url
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.datastructures import FormData, UploadFile

from greenlight.application.use_cases import KeptQuote, QuoteDraft, Upload
from greenlight.domain.errors import DomainError, InvalidRequest
from greenlight.domain.money import Money
from greenlight.domain.spend_request import BillingPeriod, RequestStatus, SpendDetails
from greenlight.web.auth import CurrentUser
from greenlight.web.deps import container_of, flash, local_today, render
from greenlight.web.security import verify_csrf

router = APIRouter(prefix="/requests")


def _to_request(reference: str) -> RedirectResponse:
    return RedirectResponse(f"/requests/{reference}", status_code=303)


def _money(raw: str, currency: str) -> Money:
    try:
        amount = Decimal(raw.replace(",", "").strip())
    except InvalidOperation as error:
        raise InvalidRequest("Enter the amount as a number, for example 1250.50.") from error
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise InvalidRequest("Amounts can have at most two decimal places.")
    try:
        return Money(amount, currency)
    except ValueError as error:
        raise InvalidRequest("Choose a currency.") from error


def _inline_disposition(filename: str) -> str:
    printable = "".join(ch for ch in filename if ch.isprintable() and ch not in '"\\;')
    ascii_name = printable.encode("ascii", "ignore").decode() or "quote"
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote_url(printable)}"


def _optional_uuid(raw) -> UUID | None:
    return UUID(raw) if raw else None


def _details_from(form: FormData) -> SpendDetails:
    recurring = form.get("recurring") == "yes"
    try:
        term_months = int(form.get("term_months") or 0) if recurring else None
        billing_period = BillingPeriod(form.get("billing_period")) if recurring else None
        category_id = UUID(form.get("category_id", ""))
        tag_id = _optional_uuid(form.get("tag_id"))
    except ValueError as error:
        raise InvalidRequest("Check the category, project and billing fields.") from error

    return SpendDetails(
        title=form.get("title", ""),
        supplier=form.get("supplier", ""),
        justification=form.get("justification", ""),
        category_id=category_id,
        amount=_money(form.get("amount", ""), form.get("currency", "")),
        tag_id=tag_id,
        billing_period=billing_period,
        term_months=term_months,
        no_quote_reason=form.get("no_quote_reason", ""),
        is_confidential=form.get("confidential") == "yes",
    )


async def _uploads(form: FormData, field: str = "quotes") -> list[Upload]:
    uploads = []
    for item in form.getlist(field):
        if isinstance(item, UploadFile) and item.filename:
            uploads.append(Upload(item.filename, item.content_type or "", await item.read()))
    return uploads


def _form_page(
    request: Request, template: str, *, values: dict, error: str | None = None, **context
):
    categories, tags, currencies = container_of(request).queries.form_options()
    return render(
        request,
        template,
        status_code=400 if error else 200,
        categories=categories,
        tags=tags,
        currencies=currencies,
        values=values,
        error=error,
        **context,
    )


@router.get("")
def list_requests(
    request: Request,
    user: CurrentUser,
    status: str = "",
    tag: str = "",
    q: str = "",
):
    queries = container_of(request).queries
    chosen_status = RequestStatus(status) if status in RequestStatus else None
    try:
        tag_id = _optional_uuid(tag)
    except ValueError:
        tag_id = None
    requests = queries.visible_requests(user.id, status=chosen_status, tag_id=tag_id, search=q)
    _, tags, _ = queries.form_options()
    return render(
        request,
        "requests/list.html",
        requests=requests,
        lookups=queries.lookups(),
        tags=tags,
        filters={"status": status, "tag": tag, "q": q},
    )


def _values_from(draft: QuoteDraft) -> dict:
    reading = draft.reading
    return {
        "title": reading.title,
        "supplier": reading.supplier,
        "amount": f"{reading.amount:f}",
        "currency": reading.currency,
        "recurring": "yes" if reading.is_recurring else "",
        "billing_period": reading.billing_period or "",
        "term_months": reading.term_months or "",
        "category_id": str(draft.category_id or ""),
        "tag_id": str(draft.tag_id or ""),
        "kept_quote": draft.kept.sha256,
        "kept_quote_name": draft.kept.filename,
        "kept_quote_type": draft.kept.content_type,
    }


def _confidential_values(kept: KeptQuote) -> dict:
    return {
        "currency": "USD",
        "confidential": "yes",
        "kept_quote": kept.sha256,
        "kept_quote_name": kept.filename,
        "kept_quote_type": kept.content_type,
    }


async def _all_quotes(form: FormData, approvals) -> list[Upload]:
    uploads = await _uploads(form)
    if form.get("kept_quote"):
        kept = KeptQuote(
            form.get("kept_quote", ""),
            form.get("kept_quote_name", "quote"),
            form.get("kept_quote_type", ""),
        )
        uploads.append(approvals.kept_upload(kept))
    return uploads


@router.get("/new")
def new_request(request: Request, user: CurrentUser):
    return _form_page(request, "requests/form.html", values={"currency": "USD"})


@router.post("/new/read-quote", dependencies=[Depends(verify_csrf)])
async def read_quote(request: Request, user: CurrentUser):
    container = container_of(request)
    form = await request.form()
    uploads = await _uploads(form, "quote")
    try:
        if not uploads:
            raise InvalidRequest("Choose the quote to read.")
        if form.get("confidential") == "yes":
            kept = container.approvals.keep_quote(user.id, uploads[0])
            return _form_page(
                request, "requests/form.html", values=_confidential_values(kept), kept_private=True
            )
        if not request.app.state.ai_reads.allow(str(user.id)):
            raise InvalidRequest("You have read a lot of documents this hour. Try again later.")
        draft = container.approvals.read_quote(user.id, uploads[0])
    except DomainError as error:
        return _form_page(
            request, "requests/form.html", values={"currency": "USD"}, error=str(error)
        )
    return _form_page(request, "requests/form.html", values=_values_from(draft), draft=draft)


@router.get("/advice")
def advice(
    request: Request, user: CurrentUser, supplier: str = "", category_id: str = "", quote: str = ""
):
    try:
        category = _optional_uuid(category_id)
    except ValueError:
        category = None
    found = container_of(request).queries.advice(
        user.id, supplier[:200], category, has_quote=quote == "yes"
    )
    filing = found.filing
    return JSONResponse(
        {
            "category_id": str(filing.category_id) if filing else "",
            "tag_id": str(filing.tag_id or "") if filing else "",
            "learned_from": filing.learned_from if filing else "",
            "lessons": found.lessons,
        }
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def submit_request(request: Request, background: BackgroundTasks, user: CurrentUser):
    container = container_of(request)
    form = await request.form()
    try:
        quotes = await _all_quotes(form, container.approvals)
        reference = container.approvals.submit(user.id, _details_from(form), quotes)
    except DomainError as error:
        return _form_page(request, "requests/form.html", values=dict(form), error=str(error))
    background.add_task(container.notifications.deliver_pending)
    flash(request, f"{reference} is with your approver now.")
    return _to_request(reference)


@router.get("/{reference}")
def show_request(reference: str, request: Request, user: CurrentUser):
    view = container_of(request).queries.request_view(user.id, reference)
    return render(request, "requests/detail.html", view=view, today=local_today())


@router.post("/{reference}/approve", dependencies=[Depends(verify_csrf)])
async def approve(
    reference: str,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser,
):
    container = container_of(request)
    form = await request.form()
    try:
        container.approvals.approve(user.id, reference, comment=form.get("comment", ""))
        flash(request, f"Approved {reference}.")
    except DomainError as error:
        flash(request, str(error), "error")
    background.add_task(container.notifications.deliver_pending)
    return RedirectResponse("/", status_code=303)


@router.post("/{reference}/reject", dependencies=[Depends(verify_csrf)])
async def reject(
    reference: str,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser,
):
    container = container_of(request)
    form = await request.form()
    try:
        container.approvals.reject(user.id, reference, form.get("reason", ""))
        flash(request, f"{reference} was sent back with your reason.")
    except DomainError as error:
        flash(request, str(error), "error")
        return _to_request(reference)
    background.add_task(container.notifications.deliver_pending)
    return RedirectResponse("/", status_code=303)


@router.post("/{reference}/withdraw", dependencies=[Depends(verify_csrf)])
async def withdraw(reference: str, request: Request, user: CurrentUser):
    form = await request.form()
    try:
        container_of(request).approvals.withdraw(user.id, reference, form.get("reason", ""))
        flash(request, f"{reference} was withdrawn.")
    except DomainError as error:
        flash(request, str(error), "error")
    return _to_request(reference)


@router.get("/{reference}/edit")
def edit_request(reference: str, request: Request, user: CurrentUser):
    view = container_of(request).queries.request_view(user.id, reference)
    if not view.can_revise:
        flash(request, "This request can no longer be changed.", "error")
        return _to_request(reference)
    spend = view.request
    values = {
        "title": spend.title,
        "supplier": spend.supplier,
        "justification": spend.justification,
        "category_id": str(spend.category_id),
        "tag_id": str(spend.tag_id or ""),
        "amount": f"{spend.amount:.2f}",
        "currency": spend.currency,
        "recurring": "yes" if spend.is_recurring else "",
        "billing_period": spend.billing_period or "",
        "term_months": spend.term_months or "",
        "no_quote_reason": spend.no_quote_reason,
        "confidential": "yes" if spend.is_confidential else "",
    }
    return _form_page(request, "requests/form.html", values=values, editing=view)


@router.post("/{reference}/edit", dependencies=[Depends(verify_csrf)])
async def save_changes(
    reference: str,
    request: Request,
    background: BackgroundTasks,
    user: CurrentUser,
):
    container = container_of(request)
    form = await request.form()
    try:
        needs_reapproval = container.approvals.revise(
            user.id,
            reference,
            _details_from(form),
            await _uploads(form),
            form.get("change_reason", ""),
        )
    except DomainError as error:
        view = container.queries.request_view(user.id, reference)
        return _form_page(
            request, "requests/form.html", values=dict(form), error=str(error), editing=view
        )
    background.add_task(container.notifications.deliver_pending)
    flash(
        request,
        "Saved. The change needs approving again."
        if needs_reapproval
        else "Saved. The approval still stands and finance has been told.",
    )
    return _to_request(reference)


@router.get("/{reference}/quotes/{quote_id}")
def download_quote(reference: str, quote_id: UUID, request: Request, user: CurrentUser):
    container = container_of(request)
    view = container.queries.request_view(user.id, reference)
    quote = next((q for q in view.request.quotes if q.id == quote_id), None)
    if quote is None:
        return Response(status_code=404)
    return Response(
        container.approvals.quote_content(quote),
        media_type=quote.content_type,
        headers={
            "Content-Disposition": _inline_disposition(quote.filename),
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )
