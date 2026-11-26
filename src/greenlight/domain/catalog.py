import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4


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
