from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import FormData, UploadFile

from greenlight.application.administration import PersonForm
from greenlight.application.errors import NotFound
from greenlight.application.people_import import COLUMNS, ImportPreview
from greenlight.application.queries import RATE_STALE_AFTER
from greenlight.domain.errors import DomainError, InvalidRequest, NotPermitted
from greenlight.domain.people import Employee, Role
from greenlight.web.auth import CurrentUser
from greenlight.web.deps import container_of, flash, render
from greenlight.web.security import verify_csrf

router = APIRouter(prefix="/manage")

ROLES = {
    Role.EXECUTIVE_APPROVER: ("COO approver", "Approves anything at or over the COO limit."),
    Role.IT_APPROVER: ("IT approver", "Approves anything in an IT category."),
    Role.FINANCE: ("Finance", "Sees all spend, records payments, and keeps categories and rates."),
    Role.ADMIN: ("Admin", "Adds people, changes their details and deactivates leavers."),
}


def _people_manager(user: CurrentUser) -> Employee:
    if not user.can_manage_people:
        raise NotPermitted("Only an admin can manage people.")
    return user


def _reference_data_manager(user: CurrentUser) -> Employee:
    if not user.can_manage_reference_data:
        raise NotPermitted("Only finance can change categories and exchange rates.")
    return user


PeopleManager = Annotated[Employee, Depends(_people_manager)]
ReferenceDataManager = Annotated[Employee, Depends(_reference_data_manager)]


def _optional_uuid(raw: str) -> UUID | None:
    return UUID(raw) if raw else None


def _person_form(form: FormData) -> PersonForm:
    try:
        return PersonForm(
            email=form.get("email", ""),
            name=form.get("name", ""),
            job_title=form.get("job_title", ""),
            department_id=_optional_uuid(form.get("department_id", "")),
            site_id=_optional_uuid(form.get("site_id", "")),
            manager_id=_optional_uuid(form.get("manager_id", "")),
            roles=frozenset(Role(role) for role in form.getlist("roles")),
        )
    except ValueError as error:
        raise InvalidRequest("Choose the department, site and manager from the lists.") from error


def _person(request: Request, person_id: UUID) -> Employee:
    person = container_of(request).queries.person(person_id)
    if person is None:
        raise NotFound("That person is not in Greenlight.")
    return person


def _person_page(
    request: Request,
    *,
    person: Employee | None,
    values: dict | FormData,
    roles: set[str],
    error: str | None = None,
):
    queries = container_of(request).queries
    lookups = queries.lookups()
    others = [p for p in queries.people() if not person or p.id != person.id]
    waiting = queries.approvals_waiting_on(person.id) if person else []
    requesters = {r.requester_id for r in waiting}
    return render(
        request,
        "manage/person_form.html",
        status_code=400 if error else 200,
        person=person,
        values=values,
        chosen_roles=roles,
        error=error,
        roles=ROLES,
        lookups=lookups,
        departments=sorted(lookups.departments.values(), key=lambda d: d.name),
        sites=sorted(lookups.sites.values(), key=lambda s: s.name),
        managers=others,
        waiting=waiting,
        can_take_over=[p for p in others if p.id not in requesters],
    )


@router.get("/people")
def people(request: Request, user: PeopleManager, q: str = ""):
    queries = container_of(request).queries
    return render(
        request,
        "manage/people.html",
        people=queries.directory(q),
        lookups=queries.lookups(),
        roles=ROLES,
        q=q,
    )


@router.get("/people/new")
def new_person(request: Request, user: PeopleManager):
    return _person_page(request, person=None, values={}, roles=set())


@router.post("/people/new", dependencies=[Depends(verify_csrf)])
async def add_person(request: Request, user: PeopleManager):
    form = await request.form()
    try:
        container_of(request).admin.add_person(user.id, _person_form(form))
    except DomainError as error:
        roles = set(form.getlist("roles"))
        return _person_page(request, person=None, values=form, roles=roles, error=str(error))
    flash(request, f"{form.get('name', '').strip()} is added and can sign in now.")
    return RedirectResponse("/manage/people", status_code=303)


@router.get("/people/import")
def import_page(request: Request, user: PeopleManager):
    return render(request, "manage/import.html", preview=None)


@router.get("/people/import/template.csv")
def import_template(user: PeopleManager):
    return Response(
        ",".join(COLUMNS) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="people.csv"'},
    )


async def _csv_text(form: FormData) -> str:
    upload = form.get("file")
    if isinstance(upload, UploadFile) and upload.filename:
        try:
            return (await upload.read()).decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise InvalidRequest("Save the file as CSV (UTF-8) and try again.") from error
    csv_text = form.get("csv_text", "")
    if not csv_text.strip():
        raise InvalidRequest("Paste the CSV or choose the file.")
    return csv_text


def _import_page(request: Request, csv_text: str, preview: ImportPreview):
    return render(
        request,
        "manage/import.html",
        status_code=400 if preview.errors else 200,
        csv_text=csv_text,
        preview=preview,
        change_count=len(preview.adds) + len(preview.changes) + len(preview.deactivations),
    )


@router.post("/people/import", dependencies=[Depends(verify_csrf)])
async def preview_import(request: Request, user: PeopleManager):
    form = await request.form()
    try:
        csv_text = await _csv_text(form)
    except DomainError as error:
        flash(request, str(error), "error")
        return RedirectResponse("/manage/people/import", status_code=303)
    return _import_page(
        request, csv_text, container_of(request).admin.preview_import(user.id, csv_text)
    )


@router.post("/people/import/apply", dependencies=[Depends(verify_csrf)])
async def apply_import(request: Request, user: PeopleManager):
    csv_text = (await request.form()).get("csv_text", "")
    preview = container_of(request).admin.apply_import(user.id, csv_text)
    if preview.errors:
        return _import_page(request, csv_text, preview)
    flash(
        request,
        f"Imported: {len(preview.adds)} added, {len(preview.changes)} changed, "
        f"{len(preview.deactivations)} deactivated.",
    )
    return RedirectResponse("/manage/people", status_code=303)


@router.get("/people/{person_id}")
def person_page(person_id: UUID, request: Request, user: PeopleManager):
    person = _person(request, person_id)
    values = {
        "email": person.email,
        "name": person.name,
        "job_title": person.job_title,
        "department_id": str(person.department_id or ""),
        "site_id": str(person.site_id or ""),
        "manager_id": str(person.manager_id or ""),
    }
    return _person_page(request, person=person, values=values, roles=set(person.roles))


@router.post("/people/{person_id}", dependencies=[Depends(verify_csrf)])
async def update_person(person_id: UUID, request: Request, user: PeopleManager):
    person = _person(request, person_id)
    form = await request.form()
    try:
        container_of(request).admin.update_person(user.id, person_id, _person_form(form))
    except DomainError as error:
        roles = set(form.getlist("roles"))
        return _person_page(request, person=person, values=form, roles=roles, error=str(error))
    flash(request, f"{person.first_name}'s details are saved.")
    return RedirectResponse("/manage/people", status_code=303)


@router.post("/people/{person_id}/deactivate", dependencies=[Depends(verify_csrf)])
async def deactivate_person(
    person_id: UUID, request: Request, background: BackgroundTasks, user: PeopleManager
):
    container = container_of(request)
    person = _person(request, person_id)
    form = await request.form()
    try:
        try:
            hand_over_to_id = _optional_uuid(form.get("hand_over_to_id", ""))
        except ValueError as error:
            raise InvalidRequest("Choose who takes over from the list.") from error
        container.admin.deactivate_person(user.id, person_id, hand_over_to_id)
    except DomainError as error:
        flash(request, str(error), "error")
        return RedirectResponse(f"/manage/people/{person_id}#leave", status_code=303)
    flash(request, f"{person.first_name} is deactivated and can no longer sign in.")
    background.add_task(container.notifications.deliver_pending)
    return RedirectResponse("/manage/people", status_code=303)


@router.get("/categories")
def categories(request: Request, user: ReferenceDataManager):
    lookups = container_of(request).queries.lookups()
    listed = sorted(lookups.categories.values(), key=lambda c: (not c.is_active, c.name))
    return render(request, "manage/categories.html", categories=listed)


@router.post("/categories", dependencies=[Depends(verify_csrf)])
async def add_category(request: Request, user: ReferenceDataManager):
    form = await request.form()
    try:
        container_of(request).admin.add_category(
            user.id, form.get("name", ""), requires_it_review=form.get("it_review") == "yes"
        )
        flash(request, "Category added. People can choose it for new requests.")
    except DomainError as error:
        flash(request, str(error), "error")
    return RedirectResponse("/manage/categories", status_code=303)


@router.post("/categories/{category_id}", dependencies=[Depends(verify_csrf)])
async def update_category(category_id: UUID, request: Request, user: ReferenceDataManager):
    form = await request.form()
    is_active = form.get("is_active") == "yes"
    try:
        container_of(request).admin.update_category(
            user.id,
            category_id,
            name=form.get("name", ""),
            requires_it_review=form.get("it_review") == "yes",
            is_active=is_active,
        )
        flash(
            request,
            "Saved. People can choose it for new requests."
            if is_active
            else "Retired. It stays on old requests but can't be chosen for new ones.",
        )
    except DomainError as error:
        flash(request, str(error), "error")
    return RedirectResponse("/manage/categories", status_code=303)


def _rate(raw: str) -> Decimal:
    try:
        rate = Decimal(raw.replace(",", "").strip())
    except InvalidOperation as error:
        raise InvalidRequest("Enter the rate as a number, like 0.2723.") from error
    if not rate.is_finite():
        raise InvalidRequest("Enter the rate as a number, like 0.2723.")
    return rate


@router.get("/rates")
def rates(request: Request, user: ReferenceDataManager):
    queries = container_of(request).queries
    return render(
        request,
        "manage/rates.html",
        rates=sorted(queries.rates_view(), key=lambda view: view.rate.currency),
        lookups=queries.lookups(),
        stale_after_days=RATE_STALE_AFTER.days,
    )


@router.post("/rates", dependencies=[Depends(verify_csrf)])
async def set_rate(request: Request, user: ReferenceDataManager):
    form = await request.form()
    currency = form.get("currency", "")
    try:
        container_of(request).admin.set_rate(user.id, currency, _rate(form.get("usd_per_unit", "")))
        flash(request, f"{currency.strip().upper()} saved. New requests use it from now on.")
    except DomainError as error:
        flash(request, str(error), "error")
    return RedirectResponse("/manage/rates", status_code=303)
