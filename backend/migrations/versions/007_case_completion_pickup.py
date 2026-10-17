"""Add explicit case completion and weekly pickup follow-ups.

Revision ID: 007_case_completion_pickup
Revises: 006_cancelled_stage_snapshot

Existing records remain active. No historical case is completed by inference.
"""
from alembic import op
import sqlalchemy as sa

revision = '007_case_completion_pickup'
down_revision = '006_cancelled_stage_snapshot'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cases', sa.Column('lifecycle', sa.String(20), nullable=False, server_default='Activo'))
    op.add_column('cases', sa.Column('completed_at', sa.DateTime(), nullable=True))
    op.add_column('cases', sa.Column('completed_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_cases_completed_by', 'cases', 'users', ['completed_by_id'], ['id'])
    op.create_index('ix_cases_lifecycle', 'cases', ['lifecycle'])
    op.create_index('ix_case_lifecycle_owner', 'cases', ['lifecycle', 'owner_id'])

    op.alter_column('tasks', 'owner_id', existing_type=sa.Integer(), nullable=True)
    op.add_column('tasks', sa.Column('category', sa.String(20), nullable=False, server_default='GENERAL'))
    op.create_index('ix_task_category_open_case', 'tasks', ['category', 'done_at', 'case_id'])


def downgrade():
    raise RuntimeError('La revisión crea seguimientos de recogida con responsable opcional; no se revierte automáticamente.')
