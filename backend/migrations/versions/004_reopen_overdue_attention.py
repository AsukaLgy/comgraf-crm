"""Reopen legacy closed cases that already had an overdue follow-up.

Revision ID: 004_reopen_overdue_attention
Revises: 003_attention_production_flow
"""
from alembic import op

revision = '004_reopen_overdue_attention'
down_revision = '003_attention_production_flow'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE cases
        SET status = 'Abierto'
        WHERE status = 'Cerrado'
          AND id IN (
            SELECT overdue.case_id
            FROM (
              SELECT case_id
              FROM tasks
              WHERE done_at IS NULL AND due_at <= UTC_TIMESTAMP()
              GROUP BY case_id
            ) overdue
          )
    """)


def downgrade():
    raise RuntimeError(
        'No se puede volver a cerrar expedientes sin conocer su estado previo. Restaura el respaldo si necesitas revertir.'
    )
