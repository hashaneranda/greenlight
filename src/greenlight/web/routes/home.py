from fastapi import APIRouter, Request

from greenlight.web.auth import CurrentUser
from greenlight.web.deps import container_of, render

router = APIRouter()


@router.get("/")
def home(request: Request, user: CurrentUser):
    queries = container_of(request).queries
    return render(
        request,
        "home.html",
        waiting=queries.waiting_for(user.id),
        mine=queries.my_requests(user.id)[:6],
        lookups=queries.lookups(),
    )


@router.get("/insights")
def insights(request: Request, user: CurrentUser):
    queries = container_of(request).queries
    return render(
        request, "insights.html", board=queries.dashboard(user.id), lookups=queries.lookups()
    )
