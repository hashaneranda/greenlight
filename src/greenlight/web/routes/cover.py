from datetime import date
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse

from greenlight.domain.errors import DomainError, InvalidRequest
from greenlight.web.auth import CurrentUser
from greenlight.web.deps import container_of, flash, render
from greenlight.web.security import verify_csrf

router = APIRouter(prefix="/cover")


@router.get("")
def cover_page(request: Request, user: CurrentUser):
    queries = container_of(request).queries
    arranged, providing = queries.covers_for(user.id)
    colleagues = [p for p in queries.people() if p.id != user.id]
    return render(
        request,
        "cover.html",
        arranged=arranged,
        providing=providing,
        colleagues=colleagues,
        lookups=queries.lookups(),
        today=date.today(),
    )


@router.post("", dependencies=[Depends(verify_csrf)])
async def arrange_cover(request: Request, background: BackgroundTasks, user: CurrentUser):
    container = container_of(request)
    form = await request.form()
    try:
        try:
            covering_id = UUID(form.get("covering_id", ""))
            starts_on = date.fromisoformat(form.get("starts_on", ""))
            ends_on = date.fromisoformat(form.get("ends_on", ""))
        except ValueError as error:
            raise InvalidRequest("Choose a colleague and both dates.") from error
        container.approvals.arrange_cover(
            user.id,
            absent_id=user.id,
            covering_id=covering_id,
            starts_on=starts_on,
            ends_on=ends_on,
            note=form.get("note", ""),
        )
        flash(request, "Cover is set. They have been told.")
        background.add_task(container.notifications.deliver_pending)
    except DomainError as error:
        flash(request, str(error), "error")
    return RedirectResponse("/cover", status_code=303)


@router.post("/{cover_id}/cancel", dependencies=[Depends(verify_csrf)])
def cancel_cover(cover_id: UUID, request: Request, user: CurrentUser):
    try:
        container_of(request).approvals.cancel_cover(user.id, cover_id)
        flash(request, "Cover cancelled.")
    except (DomainError, LookupError) as error:
        flash(request, str(error), "error")
    return RedirectResponse("/cover", status_code=303)
