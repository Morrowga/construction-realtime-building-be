"""add layer_order to zone_tasks and rollback to approvals

Revision ID: a1b2c3d4e5f6
Revises: 50557818b926
Create Date: 2026-07-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '50557818b926'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add layer_order to zone_tasks
    # Lower number = earlier in construction sequence (framing=1, concrete=2, etc.)
    op.add_column('zone_tasks',
        sa.Column('layer_order', sa.Integer(), server_default='0', nullable=False)
    )

    # Remove the UNIQUE constraint on approvals.report_id
    # so we can have multiple approvals per report (rollback creates a new record)
    op.drop_constraint('approvals_report_id_key', 'approvals', type_='unique')

    # Add rollback fields to approvals
    op.add_column('approvals',
        sa.Column('is_rolled_back', sa.Boolean(), server_default='false', nullable=False)
    )
    op.add_column('approvals',
        sa.Column('rolled_back_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('approvals',
        sa.Column('rolled_back_by', sa.UUID(), nullable=True)
    )
    op.add_column('approvals',
        sa.Column('rollback_reason', sa.Text(), nullable=True)
    )
    op.create_foreign_key(
        'fk_approvals_rolled_back_by',
        'approvals', 'users',
        ['rolled_back_by'], ['id']
    )

    # Add colour_signal to zone_tasks for quick 3D rendering
    # Values: 'grey' | 'amber' | 'green'
    op.add_column('zone_tasks',
        sa.Column('colour_signal', sa.String(10), server_default='grey', nullable=False)
    )
    # Add active_layer_name for viewer display
    op.add_column('zone_tasks',
        sa.Column('active_layer_name', sa.String(255), nullable=True)
    )
    op.add_column('zone_tasks',
        sa.Column('active_layer_pct', sa.Float(), server_default='0.0', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('zone_tasks', 'active_layer_pct')
    op.drop_column('zone_tasks', 'active_layer_name')
    op.drop_column('zone_tasks', 'colour_signal')
    op.drop_constraint('fk_approvals_rolled_back_by', 'approvals', type_='foreignkey')
    op.drop_column('approvals', 'rollback_reason')
    op.drop_column('approvals', 'rolled_back_by')
    op.drop_column('approvals', 'rolled_back_at')
    op.drop_column('approvals', 'is_rolled_back')
    op.create_unique_constraint('approvals_report_id_key', 'approvals', ['report_id'])
    op.drop_column('zone_tasks', 'layer_order')