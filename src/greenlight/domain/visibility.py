from uuid import UUID

from greenlight.domain.people import Employee
from greenlight.domain.spend_request import SpendRequest


def can_view(
    viewer: Employee,
    request: SpendRequest,
    *,
    headed_department_ids: set[UUID] = frozenset(),
    covering_for_ids: set[UUID] = frozenset(),
) -> bool:
    if viewer.sees_all_spend or viewer.id == request.requester_id:
        return True

    people_on_route = {step.approver_id for step in request.steps}
    people_on_route |= {step.decided_by_id for step in request.steps if step.decided_by_id}
    if viewer.id in people_on_route or people_on_route & covering_for_ids:
        return True

    return request.department_id in headed_department_ids and not request.is_confidential
