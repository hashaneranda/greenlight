from fastapi import APIRouter, Request

from greenlight.web.auth import CurrentUser
from greenlight.web.deps import container_of, render

router = APIRouter()


@router.get("/")
def home(request: Request, user: CurrentUser):
    queries = container_of(request).queries
    suggestions = queries.rules(user.id).suggestions if user.sees_all_spend else []
    board = queries.dashboard(user.id)
    ledger = queries.payments_ledger(user.id) if user.can_record_payments else []
    return render(
        request,
        "home.html",
        suggestions=suggestions,
        waiting=queries.waiting_for(user.id),
        mine=queries.my_requests(user.id)[:6],
        to_pay=[r for r in ledger if r.outstanding_usd],
        board=board,
        show_picture=user.sees_all_spend or any(r.requester_id != user.id for r in board.recent),
        lookups=queries.lookups(),
    )


@router.get("/inbox")
def inbox(request: Request, user: CurrentUser):
    container = container_of(request)
    items = container.queries.inbox(user.id)
    container.approvals.mark_inbox_read(user.id)
    return render(request, "inbox.html", items=items)


@router.get("/insights")
def insights(request: Request, user: CurrentUser):
    queries = container_of(request).queries
    return render(
        request, "insights.html", board=queries.dashboard(user.id), lookups=queries.lookups()
    )


@router.get("/review")
def review(request: Request, user: CurrentUser):
    queries = container_of(request).queries
    waiting = queries.waiting_for(user.id)
    view = queries.request_view(user.id, waiting[0].reference) if waiting else None
    return render(request, "review.html", view=view, remaining=len(waiting))
