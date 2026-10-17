"""Snapshot the existing stage on pre-005 cancelled production records.

Revision ID: 006_cancelled_stage_snapshot
Revises: 005_commercial_cancellation
"""
from alembic import op

revision = '006_cancelled_stage_snapshot'
down_revision = '005_commercial_cancellation'
branch_labels = None
depends_on = None


def upgrade():
    # Copy, do not reinterpret or change, the stage already stored on old rows.
    op.execute('UPDATE productions SET cancelled_stage = stage WHERE cancelled_at IS NOT NULL AND cancelled_stage IS NULL')


def downgrade():
    raise RuntimeError('La instantánea histórica no se elimina automáticamente; restaura un respaldo si necesitas revertir.')
