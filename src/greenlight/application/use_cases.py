import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from greenlight.application import event_handlers
from greenlight.application.directory import PeopleDirectory
from greenlight.application.errors import NotFound
from greenlight.application.loading import active_person, request_by_reference, usd_rate
from greenlight.application.ports import Clock, FileStorage, InvoiceReader, UnitOfWork
from greenlight.domain.catalog import Tag
from greenlight.domain.cover import Cover
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.invoice_matching import Invoice, InvoiceMatch, rank_matches
from greenlight.domain.money import Money
from greenlight.domain.spend_request import Quote, RequestStatus, SpendDetails, SpendRequest

ALLOWED_UPLOAD_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic"}


@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str
    content: bytes


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
        quotes = self._store_quotes(uploads, requester_id, now)
        with self._unit_of_work() as uow:
            requester = active_person(uow, requester_id)
            _check_catalog(uow, details)
            request = SpendRequest.submit(
                reference=uow.requests.next_reference(),
                requester=requester,
                details=details,
                usd_rate=usd_rate(uow, details.amount.currency),
                quotes=quotes,
                now=now,
            )
            _start_route(uow, request, now)
            uow.requests.add(request)
            event_handlers.handle(uow, request, now)
            uow.commit()
            return request.reference

    def approve(self, actor_id: UUID, reference: str, comment: str = "") -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            actor = active_person(uow, actor_id)
            request = request_by_reference(uow, reference)
            covers = uow.covers.held_by(actor.id, now.date())
            request.approve(actor, now, comment=comment, covers_held=covers)
            event_handlers.handle(uow, request, now)
            uow.commit()

    def reject(self, actor_id: UUID, reference: str, reason: str) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            actor = active_person(uow, actor_id)
            request = request_by_reference(uow, reference)
            covers = uow.covers.held_by(actor.id, now.date())
            request.reject(actor, now, reason=reason, covers_held=covers)
            event_handlers.handle(uow, request, now)
            uow.commit()

    def withdraw(self, actor_id: UUID, reference: str, reason: str = "") -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            request = request_by_reference(uow, reference)
            request.withdraw(active_person(uow, actor_id), now, reason)
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
        quotes = self._store_quotes(uploads, actor_id, now)
        with self._unit_of_work() as uow:
            actor = active_person(uow, actor_id)
            request = request_by_reference(uow, reference)
            _check_catalog(uow, details)
            needs_reapproval = request.revise(
                actor, details, usd_rate(uow, details.amount.currency), quotes, reason
            )
            if needs_reapproval:
                _start_route(uow, request, now)
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
            request = request_by_reference(uow, reference)
            request.record_payment(
                active_person(uow, actor_id),
                invoice_reference=invoice_reference,
                amount=amount,
                usd_rate=usd_rate(uow, amount.currency),
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
            actor = active_person(uow, actor_id)
            if not actor.can_record_payments:
                raise NotPermitted("Only finance can match invoices.")
            invoice = self._invoice_reader.read(upload.content, upload.content_type)
            matches = rank_matches(
                invoice,
                uow.requests.with_status(RequestStatus.APPROVED),
                usd_rate(uow, invoice.currency),
            )
            event_handlers.audit(
                uow,
                "invoice_read",
                self._clock.now(),
                actor_id=actor.id,
                supplier=invoice.supplier,
                invoice_number=invoice.invoice_number,
                total=str(invoice.total_money),
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
            absent = active_person(uow, absent_id)
            covering = active_person(uow, covering_id)
            cover = Cover.arrange(
                arranged_by=active_person(uow, actor_id),
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
                now,
            )
            uow.commit()

    def cancel_cover(self, actor_id: UUID, cover_id: UUID) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            cover = uow.covers.get(cover_id)
            if cover is None:
                raise NotFound("That cover no longer exists.")
            cover.cancel(active_person(uow, actor_id), now)
            event_handlers.audit(uow, "cover_cancelled", now, actor_id=actor_id, cover=cover.id)
            uow.commit()

    def open_trip(
        self,
        actor_id: UUID,
        name: str,
        starts_on: date | None = None,
        ends_on: date | None = None,
    ) -> UUID:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            active_person(uow, actor_id)
            trip = Tag.open_trip(name, uow.catalog.tags(), starts_on, ends_on)
            uow.catalog.add_tag(trip)
            event_handlers.audit(uow, "trip_opened", now, actor_id=actor_id, name=trip.name)
            uow.commit()
            return trip.id

    def send_reminders(self, older_than: timedelta) -> int:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            reminded = 0
            for request in uow.requests.with_status(RequestStatus.PENDING):
                if request.remind_approver(now, older_than):
                    event_handlers.handle(uow, request, now)
                    reminded += 1
            uow.commit()
            return reminded

    def quote_content(self, quote: Quote) -> bytes:
        return self._storage.load(quote.storage_key)

    def _check_upload(self, upload: Upload) -> None:
        if upload.content_type not in ALLOWED_UPLOAD_TYPES:
            raise InvalidRequest(f"{upload.filename}: upload a PDF or a photo.")
        if not upload.content:
            raise InvalidRequest(f"{upload.filename} is empty.")
        if len(upload.content) > self._max_upload_bytes:
            limit_mb = self._max_upload_bytes // (1024 * 1024)
            raise InvalidRequest(f"{upload.filename} is larger than {limit_mb} MB.")

    def _store_quotes(self, uploads: list[Upload], uploader_id: UUID, now: datetime) -> list[Quote]:
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
                    uploaded_by_id=uploader_id,
                    uploaded_at=now,
                )
            )
        return quotes


def _start_route(uow: UnitOfWork, request: SpendRequest, now: datetime) -> None:
    requester = uow.people.get(request.requester_id)
    category = uow.catalog.category(request.category_id)
    route = uow.catalog.approval_policy().plan(
        requester, category, request.total_usd, PeopleDirectory(uow.people)
    )
    request.start_approval(route, now)


def _check_catalog(uow: UnitOfWork, details: SpendDetails) -> None:
    category = uow.catalog.category(details.category_id)
    if category is None or not category.is_active:
        raise InvalidRequest("Choose a category.")
    if details.tag_id is not None:
        tag = uow.catalog.tag(details.tag_id)
        if tag is None or not tag.is_active:
            raise InvalidRequest("That project or trip is closed.")
