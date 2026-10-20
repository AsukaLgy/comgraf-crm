"""Make explicit outbox retries idempotent."""
from alembic import op
import sqlalchemy as sa


revision = '011_mail_retry_idempotency'
down_revision = '010_yahoo_mail'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('mail_messages', sa.Column('retry_of_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_mail_messages_retry_of', 'mail_messages', 'mail_messages', ['retry_of_id'], ['id'])
    op.create_unique_constraint('uq_mail_messages_retry_of', 'mail_messages', ['retry_of_id'])


def downgrade():
    op.drop_constraint('uq_mail_messages_retry_of', 'mail_messages', type_='unique')
    op.drop_constraint('fk_mail_messages_retry_of', 'mail_messages', type_='foreignkey')
    op.drop_column('mail_messages', 'retry_of_id')
