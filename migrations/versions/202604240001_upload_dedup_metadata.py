"""add upload deduplication metadata

Revision ID: 202604240001
Revises:
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.models import Base


revision: str = "202604240001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    inspector = inspect(bind)
    if "documents" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("documents")}
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "content_hash" not in columns:
        op.add_column("documents", sa.Column("content_hash", sa.String(length=64), nullable=True))
    if "file_size" not in columns:
        op.add_column("documents", sa.Column("file_size", sa.Integer(), nullable=True))
    if "ix_documents_content_hash" not in indexes:
        op.create_index("ix_documents_content_hash", "documents", ["content_hash"], unique=False)

    if "installment_plans" in inspector.get_table_names():
        plan_columns = {column["name"] for column in inspector.get_columns("installment_plans")}
        if "plan_type" not in plan_columns:
            op.add_column("installment_plans", sa.Column("plan_type", sa.String(length=20), nullable=True))
            op.execute(
                "UPDATE installment_plans "
                "SET plan_type = CASE "
                "WHEN lower(category) LIKE '%financi%' THEN 'FINANCING' "
                "ELSE 'INSTALLMENT' END "
                "WHERE plan_type IS NULL"
            )
            op.alter_column("installment_plans", "plan_type", nullable=False)


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "documents" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("documents")}
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "ix_documents_content_hash" in indexes:
        op.drop_index("ix_documents_content_hash", table_name="documents")
    if "file_size" in columns:
        op.drop_column("documents", "file_size")
    if "content_hash" in columns:
        op.drop_column("documents", "content_hash")
