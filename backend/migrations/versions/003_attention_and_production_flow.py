"""Separate attention status from the five physical production stages.

Legacy stages are copied verbatim before classification. Only Preparación and
Listo para entregar have an unambiguous mapping. Delivered orders are considered
complete only when their recorded quantities prove full delivery.
"""
from alembic import op
import sqlalchemy as sa

revision = '003_attention_production_flow'
down_revision = '002_payment_terms'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('productions', sa.Column('legacy_stage', sa.String(40), nullable=True))
    op.add_column('productions', sa.Column('classification_required', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE productions SET legacy_stage = stage")
    op.alter_column('productions', 'stage', existing_type=sa.String(40), nullable=True)
    op.execute("UPDATE productions SET stage = 'NEW' WHERE stage = 'Preparación'")
    op.execute("UPDATE productions SET stage = 'READY' WHERE stage = 'Listo para entregar'")
    op.execute("""
        UPDATE productions p
        SET p.stage = 'READY'
        WHERE p.stage = 'Entregado'
          AND NOT EXISTS (
            SELECT i.id FROM items i
            LEFT JOIN (SELECT item_id, SUM(quantity) AS delivered FROM deliveries GROUP BY item_id) d
              ON d.item_id = i.id
            WHERE i.case_id = p.case_id AND COALESCE(d.delivered, 0) < i.quantity
          )
          AND EXISTS (SELECT 1 FROM items i WHERE i.case_id = p.case_id)
    """)
    op.execute("""
        UPDATE productions
        SET stage = NULL, classification_required = TRUE
        WHERE stage IN ('Programado', 'En producción', 'Calidad', 'Entregado')
    """)
    op.execute("UPDATE cases SET status = 'Abierto' WHERE status <> 'Cerrado'")
    op.execute("UPDATE cases SET status = 'Abierto' WHERE status = 'Cerrado' AND pending_message = TRUE")
    op.create_index('ix_task_due_open_case', 'tasks', ['done_at', 'due_at', 'case_id'])
    op.create_index('ix_ledger_due_case', 'ledger', ['due_date', 'case_id'])
    op.create_index('ix_delivery_case_item', 'deliveries', ['case_id', 'item_id'])
    op.create_index('ix_production_active_stage', 'productions', ['cancelled_at', 'stage', 'case_id'])


def downgrade():
    # The new stage vocabulary cannot be mapped back without guessing. Keep the
    # audit values rather than dropping them during a downgrade.
    raise RuntimeError('Esta migración no es reversible automáticamente: conserva la etapa anterior y requiere un respaldo para volver atrás.')
