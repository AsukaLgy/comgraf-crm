"""Add encrypted Yahoo mail configuration, inbound cursors and persistent outbox."""
from alembic import op
import sqlalchemy as sa


revision = '010_yahoo_mail'
down_revision = '009_binding_scan_groups'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('drafts', sa.Column('email_to', sa.String(1000), nullable=False, server_default=''))
    op.add_column('drafts', sa.Column('email_subject', sa.String(998), nullable=False, server_default=''))
    op.add_column('drafts', sa.Column('email_thread_root', sa.String(255), nullable=False, server_default=''))
    op.add_column('drafts', sa.Column('email_attachment_ids', sa.JSON(), nullable=True))
    op.execute("UPDATE drafts SET email_attachment_ids = JSON_ARRAY() WHERE email_attachment_ids IS NULL")
    op.alter_column('drafts', 'email_attachment_ids', existing_type=sa.JSON(), nullable=False)

    op.create_table(
        'mail_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(254), nullable=False, unique=True),
        sa.Column('encrypted_password', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status', sa.String(40), nullable=False, server_default='disconnected'),
        sa.Column('last_error', sa.String(40), nullable=True),
        sa.Column('imap_status', sa.String(40), nullable=False, server_default='not_checked'),
        sa.Column('smtp_status', sa.String(40), nullable=False, server_default='not_checked'),
        sa.Column('last_sync_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
    )
    op.create_index('ix_mail_accounts_active', 'mail_accounts', ['active'])

    op.create_table(
        'mail_sync_cursors',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('folder', sa.String(80), nullable=False),
        sa.Column('uidvalidity', sa.String(32), nullable=True),
        sa.Column('last_uid', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('activated_at', sa.DateTime(), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['mail_accounts.id']),
        sa.UniqueConstraint('account_id', 'folder', name='uq_mail_sync_account_folder'),
    )
    op.create_index('ix_mail_sync_cursors_account_id', 'mail_sync_cursors', ['account_id'])

    op.create_table(
        'mail_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=True),
        sa.Column('folder', sa.String(80), nullable=True),
        sa.Column('uidvalidity', sa.String(32), nullable=True),
        sa.Column('uid', sa.BigInteger(), nullable=True),
        sa.Column('message_id', sa.String(255), nullable=True),
        sa.Column('content_hash', sa.String(64), nullable=True),
        sa.Column('in_reply_to', sa.String(255), nullable=True),
        sa.Column('references_json', sa.JSON(), nullable=False),
        sa.Column('thread_root', sa.String(255), nullable=True),
        sa.Column('direction', sa.String(20), nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='Pendiente'),
        sa.Column('send_key', sa.String(80), nullable=True),
        sa.Column('request_hash', sa.String(64), nullable=True),
        sa.Column('sender', sa.String(254), nullable=False, server_default=''),
        sa.Column('to_addresses', sa.JSON(), nullable=False),
        sa.Column('cc_addresses', sa.JSON(), nullable=False),
        sa.Column('subject', sa.String(998), nullable=False, server_default=''),
        sa.Column('body_text', sa.Text(), nullable=False),
        sa.Column('attachment_ids', sa.JSON(), nullable=False),
        sa.Column('error_code', sa.String(40), nullable=True),
        sa.Column('smtp_accepted_at', sa.DateTime(), nullable=True),
        sa.Column('sent_copy_status', sa.String(30), nullable=True),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['mail_accounts.id']),
        sa.ForeignKeyConstraint(['case_id'], ['cases.id']),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id']),
        sa.UniqueConstraint('account_id', 'folder', 'uidvalidity', 'uid', name='uq_mail_messages_account_uid'),
        sa.UniqueConstraint('account_id', 'message_id', name='uq_mail_messages_account_message_id'),
        sa.UniqueConstraint('account_id', 'content_hash', name='uq_mail_messages_account_content_hash'),
        sa.UniqueConstraint('send_key', name='uq_mail_messages_send_key'),
    )
    op.create_index('ix_mail_messages_account_id', 'mail_messages', ['account_id'])
    op.create_index('ix_mail_messages_case_id', 'mail_messages', ['case_id'])
    op.create_index('ix_mail_messages_created_at', 'mail_messages', ['created_at'])
    op.create_index('ix_mail_messages_case_created', 'mail_messages', ['case_id', 'created_at'])
    op.create_index('ix_mail_messages_account_state', 'mail_messages', ['account_id', 'direction', 'status'])

    op.create_table(
        'mail_attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(240), nullable=False),
        sa.Column('mime', sa.String(100), nullable=False),
        sa.Column('storage_key', sa.String(80), nullable=False, unique=True),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['mail_messages.id']),
    )
    op.create_index('ix_mail_attachments_message_id', 'mail_attachments', ['message_id'])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT COUNT(*) FROM mail_messages')).scalar_one():
        raise RuntimeError('Hay mensajes e historial de correo; no se puede retirar la migración sin perderlos.')
    if bind.execute(sa.text('SELECT COUNT(*) FROM mail_accounts')).scalar_one():
        raise RuntimeError('Hay cuentas de correo configuradas; desconéctalas y exporta su progreso antes de revertir.')
    op.drop_index('ix_mail_attachments_message_id', table_name='mail_attachments')
    op.drop_table('mail_attachments')
    op.drop_index('ix_mail_messages_account_state', table_name='mail_messages')
    op.drop_index('ix_mail_messages_case_created', table_name='mail_messages')
    op.drop_index('ix_mail_messages_created_at', table_name='mail_messages')
    op.drop_index('ix_mail_messages_case_id', table_name='mail_messages')
    op.drop_index('ix_mail_messages_account_id', table_name='mail_messages')
    op.drop_table('mail_messages')
    op.drop_index('ix_mail_sync_cursors_account_id', table_name='mail_sync_cursors')
    op.drop_table('mail_sync_cursors')
    op.drop_index('ix_mail_accounts_active', table_name='mail_accounts')
    op.drop_table('mail_accounts')
    op.drop_column('drafts', 'email_attachment_ids')
    op.drop_column('drafts', 'email_subject')
    op.drop_column('drafts', 'email_thread_root')
    op.drop_column('drafts', 'email_to')
