import enum
from datetime import UTC

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)

from greenlight.domain.catalog import TagKind
from greenlight.domain.people import Role
from greenlight.domain.spend_request import BillingPeriod, RequestStatus, StepStatus

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class RoleSet(TypeDecorator):
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return sorted(role.value for role in value or ())

    def process_result_value(self, value, dialect):
        return {Role(role) for role in value or ()}


def enum_of(enum_type: type[enum.Enum]) -> Enum:
    return Enum(
        enum_type,
        native_enum=False,
        length=30,
        values_callable=lambda members: [member.value for member in members],
    )


def money() -> Numeric:
    return Numeric(14, 2)


class UtcDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            raise ValueError("Store timezone-aware datetimes only.")
        return value.astimezone(UTC) if value else value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def moment() -> UtcDateTime:
    return UtcDateTime()


departments = Table(
    "departments",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("head_id", Uuid, ForeignKey("employees.id", use_alter=True)),
)

sites = Table(
    "sites",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("country", String(80), nullable=False),
    Column("local_currency", String(3), nullable=False),
)

employees = Table(
    "employees",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("job_title", String(120), nullable=False, default=""),
    Column("department_id", Uuid, ForeignKey("departments.id")),
    Column("site_id", Uuid, ForeignKey("sites.id")),
    Column("manager_id", Uuid, ForeignKey("employees.id")),
    Column("roles", RoleSet, nullable=False, default=list),
    Column("is_active", Boolean, nullable=False, default=True),
)

categories = Table(
    "categories",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("requires_it_review", Boolean, nullable=False, default=False),
    Column("is_active", Boolean, nullable=False, default=True),
)

approval_thresholds = Table(
    "approval_thresholds",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("category_id", Uuid, ForeignKey("categories.id"), unique=True),
    Column("amount_usd", money(), nullable=False),
)

exchange_rates = Table(
    "exchange_rates",
    metadata,
    Column("currency", String(3), primary_key=True),
    Column("usd_per_unit", Numeric(18, 8), nullable=False),
    Column("updated_at", moment(), nullable=False),
)

tags = Table(
    "tags",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("kind", enum_of(TagKind), nullable=False),
    Column("name", String(160), nullable=False),
    Column("budget_usd", money()),
    Column("starts_on", Date),
    Column("ends_on", Date),
    Column("is_active", Boolean, nullable=False, default=True),
    UniqueConstraint("kind", "name"),
)

spend_requests = Table(
    "spend_requests",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("reference", String(20), nullable=False, unique=True),
    Column("requester_id", Uuid, ForeignKey("employees.id"), nullable=False, index=True),
    Column("department_id", Uuid, ForeignKey("departments.id"), index=True),
    Column("site_id", Uuid, ForeignKey("sites.id")),
    Column("title", String(200), nullable=False),
    Column("supplier", String(200), nullable=False),
    Column("justification", Text, nullable=False),
    Column("category_id", Uuid, ForeignKey("categories.id"), nullable=False),
    Column("tag_id", Uuid, ForeignKey("tags.id"), index=True),
    Column("amount", money(), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("usd_rate", Numeric(18, 8), nullable=False),
    Column("billing_period", enum_of(BillingPeriod)),
    Column("term_months", Integer),
    Column("no_quote_reason", String(300), nullable=False, default=""),
    Column("is_confidential", Boolean, nullable=False, default=False),
    Column("submitted_at", moment(), nullable=False),
    Column("status", enum_of(RequestStatus), nullable=False, index=True),
    Column("revision", Integer, nullable=False),
    Column("approved_total_usd", money()),
    Column("decided_at", moment()),
    Column("version", Integer, nullable=False, default=1),
)

approval_steps = Table(
    "approval_steps",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("request_id", Uuid, ForeignKey("spend_requests.id"), nullable=False, index=True),
    Column("revision", Integer, nullable=False),
    Column("position", Integer, nullable=False),
    Column("stages", JSON, nullable=False),
    Column("approver_id", Uuid, ForeignKey("employees.id"), nullable=False, index=True),
    Column("status", enum_of(StepStatus), nullable=False),
    Column("activated_at", moment()),
    Column("decided_at", moment()),
    Column("decided_by_id", Uuid, ForeignKey("employees.id")),
    Column("comment", Text, nullable=False, default=""),
    Column("reminded_at", moment()),
)

quotes = Table(
    "quotes",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("request_id", Uuid, ForeignKey("spend_requests.id"), nullable=False, index=True),
    Column("filename", String(255), nullable=False),
    Column("content_type", String(120), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("storage_key", String(200), nullable=False),
    Column("uploaded_by_id", Uuid, ForeignKey("employees.id"), nullable=False),
    Column("uploaded_at", moment(), nullable=False),
)

payments = Table(
    "payments",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("request_id", Uuid, ForeignKey("spend_requests.id"), nullable=False, index=True),
    Column("invoice_reference", String(120), nullable=False),
    Column("amount", money(), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("amount_usd", money(), nullable=False),
    Column("paid_on", Date, nullable=False),
    Column("recorded_by_id", Uuid, ForeignKey("employees.id"), nullable=False),
    Column("recorded_at", moment(), nullable=False),
)

covers = Table(
    "covers",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("absent_id", Uuid, ForeignKey("employees.id"), nullable=False, index=True),
    Column("covering_id", Uuid, ForeignKey("employees.id"), nullable=False, index=True),
    Column("starts_on", Date, nullable=False),
    Column("ends_on", Date, nullable=False),
    Column("arranged_by_id", Uuid, ForeignKey("employees.id"), nullable=False),
    Column("note", String(300), nullable=False, default=""),
    Column("cancelled_at", moment()),
)

audit_entries = Table(
    "audit_entries",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("occurred_at", moment(), nullable=False),
    Column("action", String(60), nullable=False),
    Column("actor_id", Uuid, ForeignKey("employees.id")),
    Column("on_behalf_of_id", Uuid, ForeignKey("employees.id")),
    Column("request_id", Uuid, ForeignKey("spend_requests.id"), index=True),
    Column("details", JSON, nullable=False),
    Column("previous_hash", String(64), nullable=False, unique=True),
    Column("hash", String(64), nullable=False, unique=True),
)

notifications = Table(
    "notifications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recipient_id", Uuid, ForeignKey("employees.id"), nullable=False, index=True),
    Column("request_id", Uuid, ForeignKey("spend_requests.id")),
    Column("subject", String(200), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", moment(), nullable=False),
    Column("delivered_at", moment()),
    Column("delivery_error", String(500), nullable=False, default=""),
    Column("attempts", Integer, nullable=False, default=0),
)

stored_files = Table(
    "stored_files",
    metadata,
    Column("key", String(200), primary_key=True),
    Column("content_type", String(120), nullable=False),
    Column("content", LargeBinary, nullable=False),
)

counters = Table(
    "counters",
    metadata,
    Column("name", String(40), primary_key=True),
    Column("value", Integer, nullable=False),
)
