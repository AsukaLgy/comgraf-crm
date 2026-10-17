"""Add auditable commercial cancellation resolution and refunds.

Revision ID: 005_commercial_cancellation
Revises: 004_reopen_overdue_attention

Previously cancelled work is flagged for human economic review. No ledger,
payment, delivery, stage, or historical amount is changed by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = '005_commercial_cancellation'
down_revision = '004_reopen_overdue_attention'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('productions', sa.Column('cancelled_stage', sa.String(40), nullable=True))
    op.add_column('productions', sa.Column('cancellation_reason', sa.Text(), nullable=True))
    op.add_column('productions', sa.Column('cancellation_review_pending', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('productions', sa.Column('cancellation_final_amount', sa.Numeric(14, 2), nullable=True))
    op.add_column('productions', sa.Column('cancellation_resolved_at', sa.DateTime(), nullable=True))
    op.add_column('productions', sa.Column('cancellation_actor_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_productions_cancellation_actor', 'productions', 'users', ['cancellation_actor_id'], ['id'])
    # Historical cancellations are explicitly queued for review, never repriced.
    op.execute("UPDATE productions SET cancellation_review_pending = TRUE WHERE cancelled_at IS NOT NULL")


def downgrade():
    raise RuntimeError('Migración de trazabilidad comercial no reversible automáticamente; conserva la base y restaura un respaldo si fuera necesario.')
