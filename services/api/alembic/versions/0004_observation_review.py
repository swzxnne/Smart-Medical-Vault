"""observation review flags: verified, review_note

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("observations", sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("observations", sa.Column("review_note", sa.String(120)))


def downgrade() -> None:
    op.drop_column("observations", "review_note")
    op.drop_column("observations", "verified")
