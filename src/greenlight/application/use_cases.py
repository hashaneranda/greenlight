import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from greenlight.application import event_handlers
from greenlight.application.directory import PeopleDirectory
from greenlight.application.ports import Clock, FileStorage, InvoiceReader, UnitOfWork
from greenlight.domain.catalog import Tag, TagKind
from greenlight.domain.cover import Cover
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.invoice_matching import Invoice, InvoiceMatch, rank_matches
from greenlight.domain.money import Money
from greenlight.domain.people import Employee, Role
from greenlight.domain.spend_request import Quote, SpendDetails, SpendRequest

ALLOWED_QUOTE_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
}


@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str
    content: bytes


class NotFound(LookupError):
    pass


class SpendApprovals:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        storage: FileStorage,
        clock: Clock,
        invoice_reader: InvoiceReader | None = None,
        max_upload_bytes: int = 10 * 1024 * 1024,
    ):
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._clock = clock
        self._invoice_reader = invoice_reader
        self._max_upload_bytes = max_upload_bytes

    def submit(self, requester_id: UUID, details: SpendDetails, uploads: list[Upload]) -> str:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            requester = _person(uow, requester_id)
            self._check_catalog(uow, details)
            quotes = self._store_quotes(uploads, requester, now)
            request = SpendRequest.submit(
                reference=uow.requests.next_reference(),
                requester=requester,
                details=details,
                usd_rate=_rate(uow, details.amount.currency),
                quotes=quotes,
                now=now,
            )
            self._start_route(uow, request, requester, now)
            uow.requests.add(request)
            event_handlers.handle(uow, request, now)
            uow.commit()
            return request.reference

    def approve(self, actor_id: UUID, reference: str, comment: str = "") -> None:
        self._decide(
            actor_id,
            reference,
            lambda request, actor, now, cover: request.approve(
                actor, now, comment=comment, cover=cover
            ),
        )

    def reject(self, actor_id: UUID, reference: str, reason: str) -> None:
        self._decide(
            actor_id,
            reference,
            lambda request, actor, now, cover: request.reject(
                actor, now, reason=reason, cover=cover
            ),
        )

    def withdraw(self, actor_id: UUID, reference: str, reason: str = "") -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            request = _request(uow, reference)
            request.withdraw(_person(uow, actor_id), now, reason)
            event_handlers.handle(uow, request, now)
            uow.commit()

    def revise(
        self,
        actor_id: UUID,
        reference: str,
        details: SpendDetails,
        uploads: list[Upload],
        reason: str,
    ) -> bool:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            actor = _person(uow, actor_id)
            request = _request(uow, reference)
            self._check_catalog(uow, details)
            quotes = self._store_quotes(uploads, actor, now)
            needs_reapproval = request.revise(
                actor, details, _rate(uow, details.amount.currency), quotes, reason
            )
            if needs_reapproval:
                self._start_route(uow, request, _person(uow, request.requester_id), now)
            event_handlers.handle(uow, request, now)
            uow.commit()
            return needs_reapproval

    def record_payment(
        self,
        actor_id: UUID,
        reference: str,
        *,
        invoice_reference: str,
        amount: Money,
        paid_on: date,
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            request = _request(uow, reference)
            request.record_payment(
                _person(uow, actor_id),
                invoice_reference=invoice_reference,
                amount=amount,
                usd_rate=_rate(uow, amount.currency),
                paid_on=paid_on,
                now=now,
            )
            event_handlers.handle(uow, request, now)
            uow.commit()

    def read_invoice(self, actor_id: UUID, upload: Upload) -> tuple[Invoice, list[InvoiceMatch]]:
        if self._invoice_reader is None:
            raise InvalidRequest("Invoice reading is not switched on.")
        self._check_upload(upload)
        with self._unit_of_work() as uow:
            actor = _person(uow, actor_id)
            if not actor.has_role(Role.FINANCE):
                raise NotPermitted("Only finance can match invoices.")
            invoice = self._invoice_reader.read(upload.content, upload.content_type)
            rate = uow.catalog.usd_rate(invoice.currency)
            if rate is None:
                raise InvalidRequest(f"Greenlight has no exchange rate for {invoice.currency}.")
            matches = rank_matches(invoice, uow.requests.all(), rate)
            event_handlers.audit(
                uow,
                "invoice_read",
                self._clock.now(),
                actor_id=actor.id,
                supplier=invoice.supplier,
                invoice_number=invoice.invoice_number,
                total=str(Money(invoice.total, invoice.currency)),
                suggested=[match.request.reference for match in matches],
            )
            uow.commit()
            return invoice, matches

    def arrange_cover(
        self,
        actor_id: UUID,
        *,
        absent_id: UUID,
        covering_id: UUID,
        starts_on: date,
        ends_on: date,
        note: str = "",
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            absent = _person(uow, absent_id)
            covering = _person(uow, covering_id)
            cover = Cover.arrange(
                arranged_by=_person(uow, actor_id),
                absent=absent,
                covering=covering,
                starts_on=starts_on,
                ends_on=ends_on,
                today=now.date(),
                existing=uow.covers.for_absent(absent_id),
                note=note,
            )
            uow.covers.add(cover)
            event_handlers.audit(
                uow,
                "cover_arranged",
                now,
                actor_id=actor_id,
                absent=absent.email,
                covering=covering.email,
                starts_on=starts_on,
                ends_on=ends_on,
                note=cover.note,
            )
            event_handlers.notify(
                uow,
                covering.id,
                f"You are covering approvals for {absent.name}",
                f"From {starts_on:%d %b} to {ends_on:%d %b %Y} you can approve what is waiting "
                f"for {absent.name}. Each decision is recorded as made on their behalf.",
                None,
                now,
            )
            uow.commit()

    def cancel_cover(self, actor_id: UUID, cover_id: UUID) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            cover = uow.covers.get(cover_id)
            if cover is None:
                raise NotFound("That cover no longer exists.")
            cover.cancel(_person(uow, actor_id), now)
            event_handlers.audit(uow, "cover_cancelled", now, actor_id=actor_id, cover_id=cover.id)
            uow.commit()

    def open_trip(
        self, actor_id: UUID, name: str, starts_on: date | None = None, ends_on: date | None = None
    ) -> UUID:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            _person(uow, actor_id)
            name = name.strip()
            if not name:
                raise InvalidRequest("Give the trip a name.")
            if any(
                t.kind == TagKind.TRIP and t.name.lower() == name.lower()
                for t in uow.catalog.tags()
            ):
                raise InvalidRequest("A trip with that name already exists. Pick it from the list.")
            tag = Tag(TagKind.TRIP, name, starts_on=starts_on, ends_on=ends_on)
            uow.catalog.add_tag(tag)
            event_handlers.audit(uow, "trip_opened", now, actor_id=actor_id, name=name)
            uow.commit()
            return tag.id

    def send_reminders(self, older_than_hours: int) -> int:
        now = self._clock.now()
        reminded = 0
        with self._unit_of_work() as uow:
            for request in uow.requests.all():
                step = request.pending_step
                if step is None:
                    continue
                last_nudge = step.reminded_at or step.activated_at
                if last_nudge and (now - last_nudge).total_seconds() < older_than_hours * 3600:
                    continue
                step.reminded_at = now
                event_handlers.notify(
                    uow,
                    step.approver_id,
                    f"Reminder: {request.reference} is waiting for you",
                    f"{request.title} for {request.supplier} is still waiting for your "
                    f"approval as {step.stage_label}.",
                    request.id,
                    now,
                )
                event_handlers.audit(
                    uow,
                    "reminder_sent",
                    now,
                    actor_id=None,
                    request_id=request.id,
                    to=str(step.approver_id),
                )
                reminded += 1
            uow.commit()
        return reminded

    def quote_content(self, quote: Quote) -> bytes:
        return self._storage.load(quote.storage_key)

    def _decide(self, actor_id: UUID, reference: str, decision) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            actor = _person(uow, actor_id)
            request = _request(uow, reference)
            step = request.pending_step
            cover = None
            if step is not None:
                cover = next(
                    (
                        c
                        for c in uow.covers.for_absent(step.approver_id)
                        if c.covering_id == actor.id and c.is_active_on(now.date())
                    ),
                    None,
                )
            decision(request, actor, now, cover)
            event_handlers.handle(uow, request, now)
            uow.commit()

    @staticmethod
    def _start_route(
        uow: UnitOfWork, request: SpendRequest, requester: Employee, now: datetime
    ) -> None:
        category = uow.catalog.category(request.category_id)
        route = uow.catalog.approval_policy().plan(
            requester, category, request.total_usd, PeopleDirectory(uow.people)
        )
        request.start_approval(route, now)

    @staticmethod
    def _check_catalog(uow: UnitOfWork, details: SpendDetails) -> None:
        category = uow.catalog.category(details.category_id)
        if category is None or not category.is_active:
            raise InvalidRequest("Choose a category.")
        if details.tag_id is not None:
            tag = uow.catalog.tag(details.tag_id)
            if tag is None or not tag.is_active:
                raise InvalidRequest("That project or trip is closed.")

    def _check_upload(self, upload: Upload) -> None:
        if upload.content_type not in ALLOWED_QUOTE_TYPES:
            raise InvalidRequest(f"{upload.filename}: upload a PDF or a photo.")
        if len(upload.content) > self._max_upload_bytes:
            limit_mb = self._max_upload_bytes // (1024 * 1024)
            raise InvalidRequest(f"{upload.filename} is larger than {limit_mb} MB.")
        if not upload.content:
            raise InvalidRequest(f"{upload.filename} is empty.")

    def _store_quotes(
        self, uploads: list[Upload], uploader: Employee, now: datetime
    ) -> list[Quote]:
        quotes = []
        for upload in uploads:
            self._check_upload(upload)
            digest = hashlib.sha256(upload.content).hexdigest()
            key = f"quotes/{digest}"
            self._storage.save(key, upload.content, upload.content_type)
            quotes.append(
                Quote(
                    filename=upload.filename[:255],
                    content_type=upload.content_type,
                    size_bytes=len(upload.content),
                    sha256=digest,
                    storage_key=key,
                    uploaded_by_id=uploader.id,
                    uploaded_at=now,
                )
            )
        return quotes


def _person(uow: UnitOfWork, employee_id: UUID) -> Employee:
    person = uow.people.get(employee_id)
    if person is None or not person.is_active:
        raise NotPermitted("Your account is not active in Greenlight.")
    return person


def _request(uow: UnitOfWork, reference: str) -> SpendRequest:
    request = uow.requests.by_reference(reference)
    if request is None:
        raise NotFound(f"No request {reference}.")
    return request


def _rate(uow: UnitOfWork, currency: str) -> Decimal:
    rate = uow.catalog.usd_rate(currency)
    if rate is None:
        raise InvalidRequest(f"Greenlight has no exchange rate for {currency}.")
    return rate
