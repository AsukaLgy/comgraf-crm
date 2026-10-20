"""Allow a request to contain multiple production orders.

Existing production primary keys equal their case IDs. Copying that value into
the new order ID preserves every historical OP number and direct order link.
Items and ledger entries keep case_id for compatibility and gain an explicit
production_id so new activity can be isolated to one order.
"""
from alembic import op
import sqlalchemy as sa

revision = '008_multiple_production_orders'
down_revision = '007_case_completion_pickup'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    op.add_column('productions', sa.Column('id', sa.Integer(), nullable=True))
    op.execute('UPDATE productions SET id = case_id')
    op.create_index('ux_productions_id_migration', 'productions', ['id'], unique=True)

    # Remove the legacy item -> production(case_id) link before changing its key.
    for fk in inspector.get_foreign_keys('items'):
        if fk.get('referred_table') == 'productions':
            op.drop_constraint(fk['name'], 'items', type_='foreignkey')
    # case_id remains a foreign key to cases and needs its own supporting key.
    op.create_index('ix_productions_case_id', 'productions', ['case_id'])
    op.drop_constraint('PRIMARY', 'productions', type_='primary')
    op.execute('ALTER TABLE productions MODIFY id INTEGER NOT NULL AUTO_INCREMENT')
    op.create_primary_key('pk_productions', 'productions', ['id'])
    op.drop_index('ux_productions_id_migration', table_name='productions')
    op.add_column('productions', sa.Column('created_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_productions_created_by', 'productions', 'users', ['created_by_id'], ['id'])

    op.add_column('items', sa.Column('production_id', sa.Integer(), nullable=True))
    op.execute('UPDATE items i JOIN productions p ON p.case_id = i.case_id SET i.production_id = p.id')
    op.alter_column('items', 'production_id', existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key('fk_items_case_id', 'items', 'cases', ['case_id'], ['id'])
    op.create_foreign_key('fk_items_production_id', 'items', 'productions', ['production_id'], ['id'])
    op.create_index('ix_items_production_id', 'items', ['production_id'])

    op.add_column('ledger', sa.Column('production_id', sa.Integer(), nullable=True))
    op.execute('UPDATE ledger l JOIN productions p ON p.case_id = l.case_id SET l.production_id = p.id')
    op.create_foreign_key('fk_ledger_production_id', 'ledger', 'productions', ['production_id'], ['id'])
    op.create_index('ix_ledger_production_id', 'ledger', ['production_id'])

    op.add_column('tasks', sa.Column('production_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_tasks_production_id', 'tasks', 'productions', ['production_id'], ['id'])
    op.create_index('ix_tasks_production_id', 'tasks', ['production_id'])


def downgrade():
    raise RuntimeError('Varias órdenes ya pueden compartir una solicitud; no se puede volver a una orden única sin perder datos.')
