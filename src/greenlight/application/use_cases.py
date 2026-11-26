import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from greenlight.application import event_handlers
from greenlight.application.directory import PeopleDirectory
from greenlight.application.errors import NotFound
from greenlight.application.loading import active_person, request_by_reference, usd_rate
from greenlight.application.ports import (
    Clock,
    FileStorage,
    InvoiceReader,
    QuoteReader,
    UnitOfWork,
)
from greenlight.domain.catalog import Tag
from greenlight.domain.cover import Cover
from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.invoice_matching import InvoiceCheck, check_invoice
from greenlight.domain.learning import learned_filing
from greenlight.domain.money import Money
from greenlight.domain.quote_reading import QuoteReading
from greenlight.domain.spend_request import Quote, RequestStatus, SpendDetails, SpendRequest

ALLOWED_UPLOAD_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic"}


@dataclass(frozen=True)
class Upload:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class KeptQuote:
    """A quote already stored while it was read, so the requester doesn't upload it twice."""

    sha256: str
    filename: str
    content_type: str


@dataclass(frozen=True)
class QuoteDraft:
    reading: QuoteReading
    kept: KeptQuote
    category_id: UUID | None
    tag_id: UUID | None
    filed_like: str = ""


class SpendApprovals:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        storage: FileStorage,
        clock: Clock,
        invoice_reader: InvoiceReader | None = None,
        max_upload_bytes: int = 10 * 1024 * 1024,
        quote_reader: QuoteReader | None = None,
    ):
        self._unit_of_work = unit_of_work
        self._storage = storage
        self._clock = clock
        self._invoice_reader = invoice_reader
        self._quote_reader = quote_reader
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
            request = request_by_reference(uow, reference, for_update=True)
            covers = uow.covers.held_by(actor.id, now.date())
            request.approve(actor, now, comment=comment, covers_held=covers)
            event_handlers.handle(uow, request, now)
            uow.commit()

    def reject(self, actor_id: UUID, reference: str, reason: str) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            actor = active_person(uow, actor_id)
            request = request_by_reference(uow, reference, for_update=True)
            covers = uow.covers.held_by(actor.id, now.date())
            request.reject(actor, now, reason=reason, covers_held=covers)
            event_handlers.handle(uow, request, now)
            uow.commit()

    def withdraw(self, actor_id: UUID, reference: str, reason: str = "") -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            request = request_by_reference(uow, reference, for_update=True)
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
            request = request_by_reference(uow, reference, for_update=True)
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
        confirm_over_approval: bool = False,
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            request = request_by_reference(uow, reference, for_update=True)
            request.record_payment(
                active_person(uow, actor_id),
                invoice_reference=invoice_reference,
                amount=amount,
                usd_rate=usd_rate(uow, amount.currency),
                paid_on=paid_on,
                now=now,
                confirm_over_approval=confirm_over_approval,
            )
            event_handlers.handle(uow, request, now)
            uow.commit()

    def read_invoice(self, actor_id: UUID, upload: Upload) -> InvoiceCheck:
        if self._invoice_reader is None:
            raise InvalidRequest("Invoice reading is not switched on.")
        self._check_upload(upload)
        with self._unit_of_work() as uow:
            if not active_person(uow, actor_id).can_record_payments:
                raise NotPermitted("Only finance can match invoices.")

        invoice = self._invoice_reader.read(upload.content, upload.content_type)

        with self._unit_of_work() as uow:
            candidates = [
                *uow.requests.with_status(RequestStatus.APPROVED),
                *uow.requests.with_status(RequestStatus.PENDING),
            ]
            check = check_invoice(invoice, candidates, usd_rate(uow, invoice.currency))
            event_handlers.audit(
                uow,
                "invoice_read",
                self._clock.now(),
                actor_id=actor_id,
                supplier=invoice.supplier,
                invoice_number=invoice.invoice_number,
                total=str(invoice.total_money),
                suggested=[match.request.reference for match in check.matches],
            )
            uow.commit()
            return check

    def read_quote(self, actor_id: UUID, upload: Upload) -> QuoteDraft:
        if self._quote_reader is None:
            raise InvalidRequest("Quote reading is not switched on.")
        self._check_upload(upload)
        with self._unit_of_work() as uow:
            active_person(uow, actor_id)
            categories = [c for c in uow.catalog.categories() if c.is_active]

        reading = self._quote_reader.read(
            upload.content, upload.content_type, [c.name for c in categories]
        )
        [quote] = self._store_quotes([upload], actor_id, self._clock.now())

        with self._unit_of_work() as uow:
            shared = [r for r in uow.requests.all() if not r.is_confidential]
            filing = learned_filing(reading.supplier, shared)
            open_tags = {t.id for t in uow.catalog.tags() if t.is_active}

        kept = _kept(quote)
        if filing:
            tag_id = filing.tag_id if filing.tag_id in open_tags else None
            return QuoteDraft(reading, kept, filing.category_id, tag_id, filing.learned_from)
        guessed = next((c.id for c in categories if c.name == reading.category_name), None)
        return QuoteDraft(reading, kept, guessed, None)

    def keep_quote(self, actor_id: UUID, upload: Upload) -> KeptQuote:
        """Store a confidential quote for the request without sending it to an AI model."""
        with self._unit_of_work() as uow:
            active_person(uow, actor_id)
        [quote] = self._store_quotes([upload], actor_id, self._clock.now())
        return _kept(quote)

    def kept_upload(self, kept: KeptQuote) -> Upload:
        if not re.fullmatch(r"[0-9a-f]{64}", kept.sha256):
            raise InvalidRequest("Attach the quote again.")
        try:
            content = self._storage.load(f"quotes/{kept.sha256}")
        except FileNotFoundError as error:
            raise InvalidRequest("Attach the quote again.") from error
        return Upload(kept.filename, kept.content_type, content)

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

    def change_coo_limit(
        self, actor_id: UUID, category_id: UUID, amount_usd: Decimal, *, suggested: bool
    ) -> None:
        now = self._clock.now()
        with self._unit_of_work() as uow:
            if not active_person(uow, actor_id).can_change_rules:
                raise NotPermitted("Only the COO or an admin can change approval rules.")
            category = uow.catalog.category(category_id)
            if category is None:
                raise NotFound("That category no longer exists.")
            if amount_usd <= 0:
                raise InvalidRequest("The limit must be more than zero.")
            policy = uow.catalog.approval_policy()
            uow.catalog.set_executive_threshold(category_id, amount_usd)
            event_handlers.audit(
                uow,
                "approval_rule_changed",
                now,
                actor_id=actor_id,
                category=category.name,
                coo_limit_from=str(policy.executive_threshold_for(category)),
                coo_limit_to=str(amount_usd),
                suggested_by_greenlight=suggested,
            )
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

    def mark_inbox_read(self, reader_id: UUID) -> None:
        with self._unit_of_work() as uow:
            uow.outbox.mark_all_read(reader_id, self._clock.now())
            uow.commit()

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


def _kept(quote: Quote) -> KeptQuote:
    return KeptQuote(quote.sha256, quote.filename, quote.content_type)


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
