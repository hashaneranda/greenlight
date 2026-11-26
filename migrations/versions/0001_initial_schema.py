"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-26 07:38:29.535952
"""

import sqlalchemy as sa
from alembic import op

from greenlight.infrastructure.persistence import tables

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("requires_it_review", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("name", name=op.f("uq_categories_name")),
    )
    op.create_table(
        "counters",
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_counters")),
    )
    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("head_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("name", name=op.f("uq_departments_name")),
    )
    op.create_table(
        "exchange_rates",
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("usd_per_unit", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column(
            "updated_at",
            tables.UtcDateTime(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("currency", name=op.f("pk_exchange_rates")),
    )
    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=80), nullable=False),
        sa.Column("local_currency", sa.String(length=3), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites")),
        sa.UniqueConstraint("name", name=op.f("uq_sites_name")),
    )
    op.create_table(
        "stored_files",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_stored_files")),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("project", "trip", name="tagkind", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("budget_usd", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("kind", "name", name=op.f("uq_tags_kind")),
    )
    op.create_table(
        "approval_thresholds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("amount_usd", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_approval_thresholds_category_id_categories"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_thresholds")),
        sa.UniqueConstraint("category_id", name=op.f("uq_approval_thresholds_category_id")),
    )
    op.create_table(
        "employees",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("job_title", sa.String(length=120), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("site_id", sa.Uuid(), nullable=True),
        sa.Column("manager_id", sa.Uuid(), nullable=True),
        sa.Column("roles", tables.RoleSet(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_employees_department_id_departments"),
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"], ["employees.id"], name=op.f("fk_employees_manager_id_employees")
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], name=op.f("fk_employees_site_id_sites")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
        sa.UniqueConstraint("email", name=op.f("uq_employees_email")),
    )
    with op.batch_alter_table("departments") as batch:
        batch.create_foreign_key(
            op.f("fk_departments_head_id_employees"), "employees", ["head_id"], ["id"]
        )
    op.create_table(
        "covers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("absent_id", sa.Uuid(), nullable=False),
        sa.Column("covering_id", sa.Uuid(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("arranged_by_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column(
            "cancelled_at",
            tables.UtcDateTime(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["absent_id"], ["employees.id"], name=op.f("fk_covers_absent_id_employees")
        ),
        sa.ForeignKeyConstraint(
            ["arranged_by_id"], ["employees.id"], name=op.f("fk_covers_arranged_by_id_employees")
        ),
        sa.ForeignKeyConstraint(
            ["covering_id"], ["employees.id"], name=op.f("fk_covers_covering_id_employees")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_covers")),
    )
    with op.batch_alter_table("covers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_covers_absent_id"), ["absent_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_covers_covering_id"), ["covering_id"], unique=False)

    op.create_table(
        "spend_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=20), nullable=False),
        sa.Column("requester_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("site_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("supplier", sa.String(length=200), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("usd_rate", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column(
            "billing_period",
            sa.Enum(
                "monthly",
                "quarterly",
                "annually",
                name="billingperiod",
                native_enum=False,
                length=30,
            ),
            nullable=True,
        ),
        sa.Column("term_months", sa.Integer(), nullable=True),
        sa.Column("no_quote_reason", sa.String(length=300), nullable=False),
        sa.Column("is_confidential", sa.Boolean(), nullable=False),
        sa.Column(
            "submitted_at",
            tables.UtcDateTime(),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "withdrawn",
                name="requeststatus",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("approved_total_usd", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "decided_at",
            tables.UtcDateTime(),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_spend_requests_category_id_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_spend_requests_department_id_departments"),
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"],
            ["employees.id"],
            name=op.f("fk_spend_requests_requester_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["site_id"], ["sites.id"], name=op.f("fk_spend_requests_site_id_sites")
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_spend_requests_tag_id_tags")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spend_requests")),
        sa.UniqueConstraint("reference", name=op.f("uq_spend_requests_reference")),
    )
    with op.batch_alter_table("spend_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_spend_requests_department_id"), ["department_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_spend_requests_requester_id"), ["requester_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_spend_requests_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_spend_requests_tag_id"), ["tag_id"], unique=False)

    op.create_table(
        "approval_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("approver_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "waiting",
                "pending",
                "approved",
                "rejected",
                "cancelled",
                name="stepstatus",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column(
            "activated_at",
            tables.UtcDateTime(),
            nullable=True,
        ),
        sa.Column(
            "decided_at",
            tables.UtcDateTime(),
            nullable=True,
        ),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "reminded_at",
            tables.UtcDateTime(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"], ["employees.id"], name=op.f("fk_approval_steps_approver_id_employees")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["employees.id"],
            name=op.f("fk_approval_steps_decided_by_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["spend_requests.id"],
            name=op.f("fk_approval_steps_request_id_spend_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_steps")),
    )
    with op.batch_alter_table("approval_steps", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_approval_steps_approver_id"), ["approver_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_approval_steps_request_id"), ["request_id"], unique=False
        )

    op.create_table(
        "audit_entries",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "occurred_at",
            tables.UtcDateTime(),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("on_behalf_of_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["employees.id"], name=op.f("fk_audit_entries_actor_id_employees")
        ),
        sa.ForeignKeyConstraint(
            ["on_behalf_of_id"],
            ["employees.id"],
            name=op.f("fk_audit_entries_on_behalf_of_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["spend_requests.id"],
            name=op.f("fk_audit_entries_request_id_spend_requests"),
        ),
        sa.PrimaryKeyConstraint("sequence", name=op.f("pk_audit_entries")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_entries_hash")),
        sa.UniqueConstraint("previous_hash", name=op.f("uq_audit_entries_previous_hash")),
    )
    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_audit_entries_request_id"), ["request_id"], unique=False
        )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            tables.UtcDateTime(),
            nullable=False,
        ),
        sa.Column(
            "delivered_at",
            tables.UtcDateTime(),
            nullable=True,
        ),
        sa.Column("delivery_error", sa.String(length=500), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipient_id"], ["employees.id"], name=op.f("fk_notifications_recipient_id_employees")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["spend_requests.id"],
            name=op.f("fk_notifications_request_id_spend_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notifications_recipient_id"), ["recipient_id"], unique=False
        )

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_reference", sa.String(length=120), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_usd", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("recorded_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "recorded_at",
            tables.UtcDateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_id"], ["employees.id"], name=op.f("fk_payments_recorded_by_id_employees")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["spend_requests.id"],
            name=op.f("fk_payments_request_id_spend_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
    )
    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_payments_request_id"), ["request_id"], unique=False)

    op.create_table(
        "quotes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "uploaded_at",
            tables.UtcDateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["spend_requests.id"], name=op.f("fk_quotes_request_id_spend_requests")
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"], ["employees.id"], name=op.f("fk_quotes_uploaded_by_id_employees")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotes")),
    )
    with op.batch_alter_table("quotes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_quotes_request_id"), ["request_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("departments") as batch:
        batch.drop_constraint(op.f("fk_departments_head_id_employees"), type_="foreignkey")
    with op.batch_alter_table("quotes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quotes_request_id"))

    op.drop_table("quotes")
    with op.batch_alter_table("payments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_payments_request_id"))

    op.drop_table("payments")
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_recipient_id"))

    op.drop_table("notifications")
    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_entries_request_id"))

    op.drop_table("audit_entries")
    with op.batch_alter_table("approval_steps", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_approval_steps_request_id"))
        batch_op.drop_index(batch_op.f("ix_approval_steps_approver_id"))

    op.drop_table("approval_steps")
    with op.batch_alter_table("spend_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_spend_requests_tag_id"))
        batch_op.drop_index(batch_op.f("ix_spend_requests_status"))
        batch_op.drop_index(batch_op.f("ix_spend_requests_requester_id"))
        batch_op.drop_index(batch_op.f("ix_spend_requests_department_id"))

    op.drop_table("spend_requests")
    with op.batch_alter_table("covers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_covers_covering_id"))
        batch_op.drop_index(batch_op.f("ix_covers_absent_id"))

    op.drop_table("covers")
    op.drop_table("employees")
    op.drop_table("approval_thresholds")
    op.drop_table("tags")
    op.drop_table("stored_files")
    op.drop_table("sites")
    op.drop_table("exchange_rates")
    op.drop_table("departments")
    op.drop_table("counters")
    op.drop_table("categories")
