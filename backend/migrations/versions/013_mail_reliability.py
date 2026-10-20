"""Persist independent mail jobs, IMAP outcomes and attended messages."""
from alembic import op
import sqlalchemy as sa


revision = '013_mail_reliability'
down_revision = '012_cai_order_snapshot'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('mail_sync_cursors', sa.Column('recovery_scan', sa.Boolean(), nullable=False, server_default=sa.false()))
    for name, column in [
        ('send_attempts', sa.Column('send_attempts', sa.Integer(), nullable=False, server_default='0')),
        ('send_next_attempt_at', sa.Column('send_next_attempt_at', sa.DateTime(), nullable=True)),
        ('processing_started_at', sa.Column('processing_started_at', sa.DateTime(), nullable=True)),
        ('copy_attempts', sa.Column('copy_attempts', sa.Integer(), nullable=False, server_default='0')),
        ('copy_next_attempt_at', sa.Column('copy_next_attempt_at', sa.DateTime(), nullable=True)),
        ('copy_started_at', sa.Column('copy_started_at', sa.DateTime(), nullable=True)),
        ('attended_at', sa.Column('attended_at', sa.DateTime(), nullable=True)),
        ('attended_by_id', sa.Column('attended_by_id', sa.Integer(), nullable=True)),
    ]:
        op.add_column('mail_messages', column)
    op.create_foreign_key('fk_mail_messages_attended_by_id_users', 'mail_messages', 'users', ['attended_by_id'], ['id'])
    op.create_index('ix_mail_outbox_due', 'mail_messages', ['account_id', 'direction', 'status', 'send_next_attempt_at'])
    op.create_index('ix_mail_copy_due', 'mail_messages', ['account_id', 'direction', 'status', 'sent_copy_status', 'copy_next_attempt_at'])
    op.create_index('ix_mail_attendance', 'mail_messages', ['case_id', 'direction', 'status', 'attended_at'])
    # An explicit historical closure/completion is the only safe legacy signal
    # that messages received before it were already handled. Do not infer it from
    # an SMTP acceptance timestamp, the buggy condition this migration replaces.
    op.execute("""
        UPDATE mail_messages AS m
        JOIN cases AS c ON c.id = m.case_id
        SET m.attended_at = COALESCE(c.closed_at, c.completed_at)
        WHERE m.direction = 'Entrante'
          AND m.status = 'Vinculado'
          AND m.attended_at IS NULL
          AND c.pending_message = 0
          AND (c.status = 'Cerrado' OR c.lifecycle = 'Completado')
          AND COALESCE(c.closed_at, c.completed_at) IS NOT NULL
          AND COALESCE(c.closed_at, c.completed_at) >= m.created_at
    """)

    op.create_table(
        'mail_worker_tasks',
        sa.Column('task_name', sa.String(30), primary_key=True),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('last_success_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(40), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['mail_accounts.id']),
    )
    op.create_table(
        'mail_sync_outcomes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('folder', sa.String(80), nullable=False, server_default='INBOX'),
        sa.Column('uidvalidity', sa.String(32), nullable=False),
        sa.Column('uid', sa.BigInteger(), nullable=False),
        sa.Column('outcome', sa.String(24), nullable=False, server_default='retry'),
        sa.Column('reason', sa.String(48), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['mail_accounts.id']),
        sa.UniqueConstraint('account_id', 'folder', 'uidvalidity', 'uid', name='uq_mail_sync_outcome_uid'),
    )
    op.create_index('ix_mail_sync_outcomes_account_id', 'mail_sync_outcomes', ['account_id'])
    op.create_index('ix_mail_sync_outcomes_retry', 'mail_sync_outcomes', ['account_id', 'outcome', 'last_attempt_at'])

def downgrade():
    op.drop_index('ix_mail_sync_outcomes_retry', table_name='mail_sync_outcomes')
    op.drop_index('ix_mail_sync_outcomes_account_id', table_name='mail_sync_outcomes')
    op.drop_table('mail_sync_outcomes')
    op.drop_table('mail_worker_tasks')
    op.drop_index('ix_mail_attendance', table_name='mail_messages')
    op.drop_index('ix_mail_copy_due', table_name='mail_messages')
    op.drop_index('ix_mail_outbox_due', table_name='mail_messages')
    op.drop_constraint('fk_mail_messages_attended_by_id_users', 'mail_messages', type_='foreignkey')
    op.drop_column('mail_messages', 'attended_by_id')
    op.drop_column('mail_messages', 'attended_at')
    op.drop_column('mail_messages', 'copy_started_at')
    op.drop_column('mail_messages', 'copy_next_attempt_at')
    op.drop_column('mail_messages', 'copy_attempts')
    op.drop_column('mail_messages', 'processing_started_at')
    op.drop_column('mail_messages', 'send_next_attempt_at')
    op.drop_column('mail_messages', 'send_attempts')
    op.drop_column('mail_sync_cursors', 'recovery_scan')
