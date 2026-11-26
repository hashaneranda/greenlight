import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from greenlight.domain.errors import InvalidRequest


@dataclass(eq=False)
class Category:
    name: str
    requires_it_review: bool = False
    is_active: bool = True
    id: UUID = field(default_factory=uuid4)


class TagKind(enum.StrEnum):
    PROJECT = "project"
    TRIP = "trip"


@dataclass(eq=False)
class Tag:
    kind: TagKind
    name: str
    budget_usd: Decimal | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    is_active: bool = True
    id: UUID = field(default_factory=uuid4)

    @property
    def label(self) -> str:
        return f"{self.kind.value.title()}: {self.name}"

    @classmethod
    def open_trip(
        cls,
        name: str,
        existing: list["Tag"],
        starts_on: date | None = None,
        ends_on: date | None = None,
    ) -> "Tag":
        name = name.strip()
        if not name:
            raise InvalidRequest("Give the trip a name.")
        if starts_on and ends_on and ends_on < starts_on:
            raise InvalidRequest("The trip must end on or after the day it starts.")
        if any(tag.kind == TagKind.TRIP and tag.name.lower() == name.lower() for tag in existing):
            raise InvalidRequest("A trip with that name already exists. Pick it from the list.")
        return cls(TagKind.TRIP, name, starts_on=starts_on, ends_on=ends_on)
