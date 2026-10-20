"""Persist the selected contact and client identity per production order."""
from alembic import op
import sqlalchemy as sa


revision = '012_cai_order_snapshot'
down_revision = '011_mail_retry_idempotency'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('productions', sa.Column('contact_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_productions_contact_id_contacts', 'productions', 'contacts', ['contact_id'], ['id'])
    op.add_column('productions', sa.Column('client_snapshot', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('productions', 'client_snapshot')
    op.drop_constraint('fk_productions_contact_id_contacts', 'productions', type_='foreignkey')
    op.drop_column('productions', 'contact_id')
