"""add envelope and interior s3 keys to model_files

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-07-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('model_files', sa.Column('envelope_s3_key', sa.Text(), nullable=True))
    op.add_column('model_files', sa.Column('interior_s3_key', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('model_files', 'interior_s3_key')
    op.drop_column('model_files', 'envelope_s3_key')