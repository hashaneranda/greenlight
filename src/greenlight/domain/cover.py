from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from greenlight.domain.errors import InvalidRequest, NotPermitted
from greenlight.domain.people import Employee, Role

MAX_COVER_DAYS = 90


@dataclass(eq=False)
class Cover:
    """While someone is away, a named colleague may approve on their behalf."""

    absent_id: UUID
    covering_id: UUID
    starts_on: date
    ends_on: date
    arranged_by_id: UUID
    note: str = ""
    cancelled_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    @classmethod
    def arrange(
        cls,
        *,
        arranged_by: Employee,
        absent: Employee,
        covering: Employee,
        starts_on: date,
        ends_on: date,
        today: date,
        existing: list["Cover"],
        note: str = "",
    ) -> "Cover":
        if arranged_by.id != absent.id and not arranged_by.has_role(Role.ADMIN):
            raise NotPermitted("You can only arrange cover for yourself.")
        if covering.id == absent.id or not covering.is_active:
            raise InvalidRequest("Choose an active colleague other than the person who is away.")
        if starts_on < today:
            raise InvalidRequest("Cover cannot start in the past.")
        if ends_on < starts_on:
            raise InvalidRequest("Cover must end on or after the day it starts.")
        if ends_on - starts_on > timedelta(days=MAX_COVER_DAYS):
            raise InvalidRequest(f"Cover can last at most {MAX_COVER_DAYS} days.")
        if any(other.overlaps(starts_on, ends_on) for other in existing):
            raise InvalidRequest(f"{absent.name} already has cover for part of that period.")

        return cls(
            absent_id=absent.id,
            covering_id=covering.id,
            starts_on=starts_on,
            ends_on=ends_on,
            arranged_by_id=arranged_by.id,
            note=note.strip(),
        )

    def is_active_on(self, day: date) -> bool:
        return self.cancelled_at is None and self.starts_on <= day <= self.ends_on

    def is_current_or_upcoming(self, today: date) -> bool:
        return self.cancelled_at is None and self.ends_on >= today

    def overlaps(self, starts_on: date, ends_on: date) -> bool:
        return self.cancelled_at is None and self.starts_on <= ends_on and self.ends_on >= starts_on

    def cancel(self, by: Employee, now: datetime) -> None:
        if by.id != self.absent_id and not by.has_role(Role.ADMIN):
            raise NotPermitted("Only the person who is away, or an admin, can cancel cover.")
        self.cancelled_at = now
