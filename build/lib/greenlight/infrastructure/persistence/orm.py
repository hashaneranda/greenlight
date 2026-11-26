from sqlalchemy.orm import registry, relationship

from greenlight.application.ports import AuditEntry, OutgoingNotification
from greenlight.domain.catalog import Category, Tag
from greenlight.domain.cover import Cover
from greenlight.domain.people import Department, Employee, Site
from greenlight.domain.spend_request import ApprovalStep, Payment, Quote, SpendRequest
from greenlight.infrastructure.persistence import tables

mapper_registry = registry(metadata=tables.metadata)
_mapped = False


def start_mappers() -> None:
    global _mapped
    if _mapped:
        return

    mapper_registry.map_imperatively(Department, tables.departments)
    mapper_registry.map_imperatively(Site, tables.sites)
    mapper_registry.map_imperatively(Employee, tables.employees)
    mapper_registry.map_imperatively(Category, tables.categories)
    mapper_registry.map_imperatively(Tag, tables.tags)
    mapper_registry.map_imperatively(Cover, tables.covers)

    mapper_registry.map_imperatively(ApprovalStep, tables.approval_steps)
    mapper_registry.map_imperatively(Quote, tables.quotes)
    mapper_registry.map_imperatively(Payment, tables.payments)
    mapper_registry.map_imperatively(
        SpendRequest,
        tables.spend_requests,
        version_id_col=tables.spend_requests.c.version,
        properties={
            "steps": relationship(
                ApprovalStep,
                lazy="selectin",
                cascade="all, delete-orphan",
                order_by=[tables.approval_steps.c.revision, tables.approval_steps.c.position],
            ),
            "quotes": relationship(
                Quote,
                lazy="selectin",
                cascade="all, delete-orphan",
                order_by=tables.quotes.c.uploaded_at,
            ),
            "payments": relationship(
                Payment,
                lazy="selectin",
                cascade="all, delete-orphan",
                order_by=tables.payments.c.paid_on,
            ),
        },
    )

    mapper_registry.map_imperatively(AuditEntry, tables.audit_entries)
    mapper_registry.map_imperatively(OutgoingNotification, tables.notifications)
    _mapped = True
