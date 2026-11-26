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

    @classmethod
    def create(cls, name: str, requires_it_review: bool, existing: list["Category"]) -> "Category":
        return cls(_category_name(name, existing), requires_it_review)

    def update(
        self, *, name: str, requires_it_review: bool, is_active: bool, existing: list["Category"]
    ) -> None:
        self.name = _category_name(name, [c for c in existing if c.id != self.id])
        self.requires_it_review = requires_it_review
        self.is_active = is_active


def _category_name(name: str, others: list[Category]) -> str:
    name = name.strip()
    if not name:
        raise InvalidRequest("Give the category a name.")
    taken = next((c for c in others if c.name.lower() == name.lower()), None)
    if taken:
        raise InvalidRequest(f"There is already a category called {taken.name}.")
    return name


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
