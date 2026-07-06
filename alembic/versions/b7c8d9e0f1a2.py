"""add organizations and scope users/projects to them

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-05 00:00:00.000000

NOTE: organization_id is added as NOT NULL on both users and projects with
no backfill step. That's intentional for this migration — per the decision
to wipe pre-launch dev data rather than backfill it. The assumed workflow:

    docker compose down -v      # wipe volumes — drops all existing rows
    docker compose up -d
    alembic upgrade head
    python seed.py              # seed.py must create an Organization FIRST
                                 # and pass its id into every User/Project
                                 # it creates — update seed.py accordingly

If this is ever run against a database that already has real rows in
users/projects, add a data migration step BEFORE the NOT NULL columns are
added: create a default Organization, then
    UPDATE users SET organization_id = '<default-org-id>';
    UPDATE projects SET organization_id = '<default-org-id>';
before altering the columns to NOT NULL, or the migration will fail.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'organizations',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('plan', sa.String(length=50), server_default='trial', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', sa.UUID(), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_index(op.f('ix_organizations_slug'), 'organizations', ['slug'], unique=True)

    op.add_column('users', sa.Column('organization_id', sa.UUID(), nullable=False))
    op.create_foreign_key(
        'fk_users_organization_id', 'users', 'organizations',
        ['organization_id'], ['id'], ondelete='CASCADE',
    )
    op.create_index(op.f('ix_users_organization_id'), 'users', ['organization_id'], unique=False)

    op.add_column('projects', sa.Column('organization_id', sa.UUID(), nullable=False))
    op.create_foreign_key(
        'fk_projects_organization_id', 'projects', 'organizations',
        ['organization_id'], ['id'], ondelete='CASCADE',
    )
    op.create_index(op.f('ix_projects_organization_id'), 'projects', ['organization_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_projects_organization_id'), table_name='projects')
    op.drop_constraint('fk_projects_organization_id', 'projects', type_='foreignkey')
    op.drop_column('projects', 'organization_id')

    op.drop_index(op.f('ix_users_organization_id'), table_name='users')
    op.drop_constraint('fk_users_organization_id', 'users', type_='foreignkey')
    op.drop_column('users', 'organization_id')

    op.drop_index(op.f('ix_organizations_slug'), table_name='organizations')
    op.drop_table('organizations')