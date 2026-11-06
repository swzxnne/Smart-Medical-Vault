"""structured health facts: observations, medications, conditions; profile summary citations

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _owner_columns(document_nullable: bool) -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=document_nullable),
        sa.Column("page_no", sa.Integer()),
    ]


def upgrade() -> None:
    op.add_column("summaries", sa.Column("citations", sa.JSON()))

    op.create_table(
        "observations",
        *_owner_columns(document_nullable=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("value_num", sa.Float()),
        sa.Column("value_text", sa.String(80), nullable=False),
        sa.Column("unit", sa.String(40)),
        sa.Column("ref_low", sa.Float()),
        sa.Column("ref_high", sa.Float()),
        sa.Column("ref_text", sa.String(80)),
        sa.Column("flag", sa.String(10)),
        sa.Column("observed_at", sa.Date()),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_observations_user_id", "observations", ["user_id"])
    op.create_index("ix_observations_document_id", "observations", ["document_id"])
    op.create_index("ix_observations_profile_code_date", "observations", ["profile_id", "code", "observed_at"])

    op.create_table(
        "medications",
        *_owner_columns(document_nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("dose", sa.String(120)),
        sa.Column("frequency", sa.String(120)),
        sa.Column("duration", sa.String(80)),
        sa.Column("prescribed_on", sa.Date()),
    )
    op.create_table(
        "conditions",
        *_owner_columns(document_nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("noted_on", sa.Date()),
    )
    for table in ("medications", "conditions"):
        for column in ("user_id", "profile_id", "document_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("conditions")
    op.drop_table("medications")
    op.drop_table("observations")
    op.drop_column("summaries", "citations")
