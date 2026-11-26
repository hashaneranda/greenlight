import enum
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from greenlight.domain.catalog import Category
from greenlight.domain.errors import NoApproverAvailable
from greenlight.domain.people import Employee


class ApprovalStage(enum.StrEnum):
    LINE_MANAGER = "line_manager"
    IT = "it"
    EXECUTIVE = "executive"

    @property
    def label(self) -> str:
        return _STAGE_LABELS[self]


_STAGE_LABELS = {
    ApprovalStage.LINE_MANAGER: "line manager",
    ApprovalStage.IT: "IT",
    ApprovalStage.EXECUTIVE: "COO",
}


class ApproverDirectory(Protocol):
    def line_manager_of(self, employee: Employee) -> Employee | None: ...

    def department_head_of(self, employee: Employee) -> Employee | None: ...

    def it_approver(self) -> Employee | None: ...

    def executive_approver(self) -> Employee | None: ...


@dataclass
class PlannedStep:
    approver: Employee
    stages: list[ApprovalStage] = field(default_factory=list)


@dataclass
class Route:
    steps: list[PlannedStep]
    notes: list[str]


@dataclass
class ApprovalPolicy:
    """Who must approve what. Thresholds are in USD and apply to the full committed amount."""

    executive_threshold_usd: Decimal | None
    category_thresholds_usd: dict[UUID, Decimal] = field(default_factory=dict)

    def executive_threshold_for(self, category: Category) -> Decimal | None:
        return self.category_thresholds_usd.get(category.id, self.executive_threshold_usd)

    def needs_executive(self, category: Category, total_usd: Decimal) -> bool:
        threshold = self.executive_threshold_for(category)
        return threshold is not None and total_usd >= threshold

    def plan(
        self,
        requester: Employee,
        category: Category,
        total_usd: Decimal,
        directory: ApproverDirectory,
    ) -> Route:
        notes: list[str] = []
        wanted = [(ApprovalStage.LINE_MANAGER, self._line_approver(requester, directory, notes))]

        if category.requires_it_review:
            it_approver = _require(directory.it_approver(), "No IT approver is set up.")
            if it_approver.id == requester.id:
                notes.append("IT review not needed because the requester is the IT approver.")
            else:
                wanted.append((ApprovalStage.IT, it_approver))

        if self.needs_executive(category, total_usd):
            executive = _require(directory.executive_approver(), "No COO approver is set up.")
            if executive.id == requester.id:
                notes.append("COO approval not needed because the requester is the COO.")
            else:
                wanted.append((ApprovalStage.EXECUTIVE, executive))

        return Route(steps=_merge_repeat_approvers(wanted, notes), notes=notes)

    @staticmethod
    def _line_approver(
        requester: Employee, directory: ApproverDirectory, notes: list[str]
    ) -> Employee:
        manager = directory.line_manager_of(requester)
        if manager and manager.is_active and manager.id != requester.id:
            return manager

        head = directory.department_head_of(requester)
        if head and head.is_active and head.id != requester.id:
            notes.append("No active line manager on record, so it goes to the department head.")
            return head

        executive = directory.executive_approver()
        if executive and executive.id != requester.id:
            notes.append("No line manager or department head on record, so it goes to the COO.")
            return executive

        raise NoApproverAvailable(f"Nobody is set up to approve requests from {requester.name}.")


def _require(approver: Employee | None, message: str) -> Employee:
    if approver is None or not approver.is_active:
        raise NoApproverAvailable(message)
    return approver


def _merge_repeat_approvers(
    wanted: list[tuple[ApprovalStage, Employee]], notes: list[str]
) -> list[PlannedStep]:
    steps: list[PlannedStep] = []
    for stage, approver in wanted:
        existing = next((step for step in steps if step.approver.id == approver.id), None)
        if existing:
            existing.stages.append(stage)
            notes.append(f"{approver.name} approves once, covering {stage.label} too.")
        else:
            steps.append(PlannedStep(approver=approver, stages=[stage]))
    return steps
