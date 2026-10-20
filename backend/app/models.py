from datetime import datetime, timezone
import uuid
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, Date, Numeric, JSON, ForeignKey, UniqueConstraint, Index
from .db import Base


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(Base):
    __tablename__ = 'roles'
    name = Column(String(40), primary_key=True)
    permissions = Column(JSON, nullable=False)


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    name = Column(String(160), nullable=False)
    password_hash = Column(String(255), nullable=False)
    roles = Column(JSON, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    capacity = Column(Integer, nullable=False, default=20)
    created_at = Column(DateTime, nullable=False, default=now)


class Session(Base):
    __tablename__ = 'sessions'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(ForeignKey('users.id'), nullable=False)
    expires_at = Column(DateTime, nullable=False)


class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    key = Column(String(64), primary_key=True)
    attempts = Column(Integer, nullable=False, default=0)
    window_start = Column(DateTime, nullable=False, default=now)


class Client(Base):
    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True)
    name = Column(String(180), nullable=False)
    company = Column(String(180), nullable=False, default='')
    rtn = Column(String(30), nullable=False, default='')
    phones = Column(JSON, nullable=False, default=list)
    emails = Column(JSON, nullable=False, default=list)
    addresses = Column(JSON, nullable=False, default=list)
    observations = Column(Text, nullable=False, default='')
    provisional = Column(Boolean, nullable=False, default=True)
    search_text = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=now)


class Contact(Base):
    __tablename__ = 'contacts'
    id = Column(Integer, primary_key=True)
    client_id = Column(ForeignKey('clients.id'), nullable=False, index=True)
    name = Column(String(180), nullable=False)
    phone = Column(String(60), nullable=False, default='')
    email = Column(String(180), nullable=False, default='')
    position = Column(String(120), nullable=False, default='')


class Case(Base):
    __tablename__ = 'cases'
    id = Column(Integer, primary_key=True)
    client_id = Column(ForeignKey('clients.id'), nullable=False, index=True)
    contact_id = Column(ForeignKey('contacts.id'))
    related_case_id = Column(ForeignKey('cases.id'))
    subject = Column(String(240), nullable=False)
    description = Column(Text, nullable=False, default='')
    kind = Column(String(30), nullable=False, default='Consulta')
    channel = Column(String(30), nullable=False, default='Presencial')
    owner_id = Column(ForeignKey('users.id'), index=True)
    # Legacy area-specific owner retained for historical visibility; new work uses owner_id only.
    collection_owner_id = Column(ForeignKey('users.id'))
    priority = Column(String(20), nullable=False, default='Normal')
    status = Column(String(40), nullable=False, default='Abierto', index=True)
    mode = Column(String(20), nullable=False, default='Atención', index=True)
    next_action = Column(String(500), nullable=False, default='')
    pending_message = Column(Boolean, nullable=False, default=False)
    lifecycle = Column(String(20), nullable=False, default='Activo', server_default='Activo', index=True)
    completed_at = Column(DateTime)
    completed_by_id = Column(ForeignKey('users.id'))
    closed_at = Column(DateTime)
    close_reason = Column(Text)
    close_summary = Column(Text)
    first_response_at = Column(DateTime)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now)


class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    actor_id = Column(ForeignKey('users.id'))
    kind = Column(String(40), nullable=False)
    channel = Column(String(30), nullable=False, default='Sistema')
    body = Column(Text, nullable=False)
    data = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=now, index=True)


class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    production_id = Column(ForeignKey('productions.id'), index=True)
    owner_id = Column(ForeignKey('users.id'))
    category = Column(String(20), nullable=False, default='GENERAL', server_default='GENERAL')
    title = Column(String(500), nullable=False)
    due_at = Column(DateTime, nullable=False, index=True)
    done_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=now)


class Quote(Base):
    __tablename__ = 'quotes'
    __table_args__ = (UniqueConstraint('case_id', 'version'),)
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    items = Column(JSON, nullable=False)
    total = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    valid_until = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default='Pendiente')
    decision_evidence = Column(Text)
    decided_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=now)


class Production(Base):
    __tablename__ = 'productions'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    created_by_id = Column(ForeignKey('users.id'))
    scan_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    workflow_cycle = Column(Integer, nullable=False, default=0, server_default='0')
    binding_confirmed_cycle = Column(Integer)
    binding_confirmed_at = Column(DateTime)
    binding_confirmed_by_id = Column(ForeignKey('users.id'))
    quote_id = Column(ForeignKey('quotes.id'), unique=True)
    contact_id = Column(ForeignKey('contacts.id'))
    client_snapshot = Column(JSON)
    # Legacy assignment column; current ownership is the case's principal owner.
    owner_id = Column(ForeignKey('users.id'))
    # Stable workflow code; NULL means an old stage needs explicit classification.
    stage = Column(String(40), nullable=True, default='NEW', index=True)
    legacy_stage = Column(String(40))
    classification_required = Column(Boolean, nullable=False, default=False)
    stage_started_at = Column(DateTime, nullable=False, default=now)
    blocked_since = Column(DateTime)
    block_reason = Column(Text)
    cancelled_at = Column(DateTime)
    cancelled_stage = Column(String(40))
    cancellation_reason = Column(Text)
    cancellation_review_pending = Column(Boolean, nullable=False, default=False, server_default='0')
    cancellation_final_amount = Column(Numeric(14, 2))
    cancellation_resolved_at = Column(DateTime)
    cancellation_actor_id = Column(ForeignKey('users.id'))
    requested_date = Column(Date)
    original_committed_date = Column(Date)
    committed_date = Column(Date, index=True)
    scheduled_at = Column(DateTime)
    price = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False, default='HNL')
    payment_terms = Column(String(240), nullable=False, default='Contado')
    advance_required = Column(Numeric(14, 2), nullable=False, default=0)
    payment_due_date = Column(Date, nullable=False)
    delivery_method = Column(String(120), nullable=False)
    instructions = Column(Text, nullable=False, default='')
    specs_snapshot = Column(JSON)
    # Legacy approval workflow fields. Keep historical values; no new approval state is collected.
    art_version = Column(String(100), nullable=False, default='')
    approval_evidence = Column(Text)
    approved_snapshot = Column(JSON)
    authorized_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=now)


class Item(Base):
    __tablename__ = 'items'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    production_id = Column(ForeignKey('productions.id'), nullable=False, index=True)
    product = Column(String(80), nullable=False)
    quantity = Column(Numeric(14, 3), nullable=False)
    unit = Column(String(30), nullable=False)
    unit_price = Column(Numeric(14, 2), nullable=False)
    specs = Column(JSON, nullable=False)
    # Legacy value only. The active workflow uses Production.stage for the whole case.
    substage = Column(String(40), nullable=False, default='')


class Attachment(Base):
    __tablename__ = 'attachments'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    actor_id = Column(ForeignKey('users.id'), nullable=False)
    name = Column(String(240), nullable=False)
    storage_key = Column(String(80), nullable=False, unique=True)
    mime = Column(String(80), nullable=False)
    size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    art_version = Column(String(100), nullable=False, default='')
    created_at = Column(DateTime, nullable=False, default=now)


class Delivery(Base):
    __tablename__ = 'deliveries'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    item_id = Column(ForeignKey('items.id'), nullable=False)
    quantity = Column(Numeric(14, 3), nullable=False)
    delivered_at = Column(DateTime, nullable=False)
    receiver = Column(String(180), nullable=False)
    method = Column(String(100), nullable=False)
    evidence = Column(Text, nullable=False)
    actor_id = Column(ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class Ledger(Base):
    __tablename__ = 'ledger'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    production_id = Column(ForeignKey('productions.id'), index=True)
    kind = Column(String(30), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    due_date = Column(Date)
    reference = Column(String(240), nullable=False)
    reason = Column(Text, nullable=False, default='')
    reverses_id = Column(ForeignKey('ledger.id'), unique=True)
    actor_id = Column(ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class Idempotency(Base):
    __tablename__ = 'idempotency'
    __table_args__ = (UniqueConstraint('case_id', 'key'),)
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False)
    key = Column(String(80), nullable=False)
    request_hash = Column(String(64), nullable=False)
    action = Column(String(40), nullable=False)
    actor_id = Column(ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class Draft(Base):
    __tablename__ = 'drafts'
    __table_args__ = (UniqueConstraint('user_id', 'case_id', 'channel'),)
    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey('users.id'), nullable=False)
    case_id = Column(ForeignKey('cases.id'), nullable=False)
    channel = Column(String(30), nullable=False)
    body = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    email_to = Column(String(1000), nullable=False, default='')
    email_subject = Column(String(998), nullable=False, default='')
    email_attachment_ids = Column(JSON, nullable=False, default=list)
    email_thread_root = Column(String(255), nullable=False, default='')


class SavedView(Base):
    __tablename__ = 'saved_views'
    __table_args__ = (UniqueConstraint('user_id', 'name'),)
    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey('users.id'), nullable=False)
    name = Column(String(100), nullable=False)
    filters = Column(JSON, nullable=False)


class PartialReleaseRequest(Base):
    __tablename__ = 'partial_release_requests'
    __table_args__ = (Index('ix_partial_release_status_case', 'status', 'case_id'),)
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('cases.id'), nullable=False, index=True)
    production_id = Column(ForeignKey('productions.id'), nullable=False, index=True)
    production_cycle = Column(Integer, nullable=False)
    requester_id = Column(ForeignKey('users.id'), nullable=False)
    request_comment = Column(Text, nullable=False)
    request_key = Column(String(80), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default='Pendiente')
    approver_id = Column(ForeignKey('users.id'))
    decision_comment = Column(Text)
    decided_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=now)


class ProviderInbox(Base):
    __tablename__ = 'provider_inbox'
    __table_args__ = (UniqueConstraint('provider', 'external_id'),)
    id = Column(Integer, primary_key=True)
    provider = Column(String(30), nullable=False)
    external_id = Column(String(180), nullable=False)
    case_id = Column(ForeignKey('cases.id'))
    payload = Column(JSON, nullable=False)
    classification = Column(String(30), nullable=False, default='Pendiente')


class MailAccount(Base):
    __tablename__ = 'mail_accounts'
    id = Column(Integer, primary_key=True)
    email = Column(String(254), nullable=False, unique=True)
    encrypted_password = Column(Text)
    active = Column(Boolean, nullable=False, default=False, index=True)
    status = Column(String(40), nullable=False, default='disconnected')
    last_error = Column(String(40))
    imap_status = Column(String(40), nullable=False, default='not_checked')
    smtp_status = Column(String(40), nullable=False, default='not_checked')
    last_sync_at = Column(DateTime)
    created_by_id = Column(ForeignKey('users.id'))
    created_at = Column(DateTime, nullable=False, default=now)


class MailSyncCursor(Base):
    __tablename__ = 'mail_sync_cursors'
    __table_args__ = (UniqueConstraint('account_id', 'folder', name='uq_mail_sync_account_folder'),)
    id = Column(Integer, primary_key=True)
    account_id = Column(ForeignKey('mail_accounts.id'), nullable=False, index=True)
    folder = Column(String(80), nullable=False, default='INBOX')
    uidvalidity = Column(String(32))
    last_uid = Column(BigInteger, nullable=False, default=0)
    activated_at = Column(DateTime, nullable=False, default=now)
    last_synced_at = Column(DateTime)
    recovery_scan = Column(Boolean, nullable=False, default=False)


class MailWorkerTask(Base):
    __tablename__ = 'mail_worker_tasks'
    task_name = Column(String(30), primary_key=True)
    account_id = Column(ForeignKey('mail_accounts.id'))
    heartbeat_at = Column(DateTime)
    last_attempt_at = Column(DateTime)
    last_success_at = Column(DateTime)
    last_error = Column(String(40))


class MailSyncOutcome(Base):
    __tablename__ = 'mail_sync_outcomes'
    __table_args__ = (
        UniqueConstraint('account_id', 'folder', 'uidvalidity', 'uid', name='uq_mail_sync_outcome_uid'),
        Index('ix_mail_sync_outcomes_retry', 'account_id', 'outcome', 'last_attempt_at'),
    )
    id = Column(Integer, primary_key=True)
    account_id = Column(ForeignKey('mail_accounts.id'), nullable=False, index=True)
    folder = Column(String(80), nullable=False, default='INBOX')
    uidvalidity = Column(String(32), nullable=False)
    uid = Column(BigInteger, nullable=False)
    outcome = Column(String(24), nullable=False, default='retry')
    reason = Column(String(48))
    attempts = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime, nullable=False, default=now)
    resolved_at = Column(DateTime)


class MailMessage(Base):
    __tablename__ = 'mail_messages'
    __table_args__ = (
        UniqueConstraint('account_id', 'folder', 'uidvalidity', 'uid', name='uq_mail_messages_account_uid'),
        UniqueConstraint('account_id', 'message_id', name='uq_mail_messages_account_message_id'),
        UniqueConstraint('account_id', 'content_hash', name='uq_mail_messages_account_content_hash'),
        UniqueConstraint('send_key', name='uq_mail_messages_send_key'),
        UniqueConstraint('retry_of_id', name='uq_mail_messages_retry_of'),
        Index('ix_mail_messages_case_created', 'case_id', 'created_at'),
        Index('ix_mail_messages_account_state', 'account_id', 'direction', 'status'),
        Index('ix_mail_outbox_due', 'account_id', 'direction', 'status', 'send_next_attempt_at'),
        Index('ix_mail_copy_due', 'account_id', 'direction', 'status', 'sent_copy_status', 'copy_next_attempt_at'),
        Index('ix_mail_attendance', 'case_id', 'direction', 'status', 'attended_at'),
    )
    id = Column(Integer, primary_key=True)
    account_id = Column(ForeignKey('mail_accounts.id'), nullable=False, index=True)
    case_id = Column(ForeignKey('cases.id'), index=True)
    folder = Column(String(80))
    uidvalidity = Column(String(32))
    uid = Column(BigInteger)
    message_id = Column(String(255))
    content_hash = Column(String(64))
    in_reply_to = Column(String(255))
    references_json = Column(JSON, nullable=False, default=list)
    thread_root = Column(String(255))
    direction = Column(String(20), nullable=False)
    status = Column(String(30), nullable=False, default='Pendiente')
    send_key = Column(String(80))
    retry_of_id = Column(ForeignKey('mail_messages.id'))
    request_hash = Column(String(64))
    sender = Column(String(254), nullable=False, default='')
    to_addresses = Column(JSON, nullable=False, default=list)
    cc_addresses = Column(JSON, nullable=False, default=list)
    subject = Column(String(998), nullable=False, default='')
    body_text = Column(Text, nullable=False, default='')
    attachment_ids = Column(JSON, nullable=False, default=list)
    error_code = Column(String(40))
    smtp_accepted_at = Column(DateTime)
    sent_copy_status = Column(String(30))
    send_attempts = Column(Integer, nullable=False, default=0)
    send_next_attempt_at = Column(DateTime)
    processing_started_at = Column(DateTime)
    copy_attempts = Column(Integer, nullable=False, default=0)
    copy_next_attempt_at = Column(DateTime)
    copy_started_at = Column(DateTime)
    attended_at = Column(DateTime)
    attended_by_id = Column(ForeignKey('users.id'))
    actor_id = Column(ForeignKey('users.id'))
    created_at = Column(DateTime, nullable=False, default=now, index=True)
    received_at = Column(DateTime)


class MailAttachment(Base):
    __tablename__ = 'mail_attachments'
    id = Column(Integer, primary_key=True)
    message_id = Column(ForeignKey('mail_messages.id'), nullable=False, index=True)
    filename = Column(String(240), nullable=False)
    mime = Column(String(100), nullable=False)
    storage_key = Column(String(80), nullable=False, unique=True)
    size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)


Index('ix_case_pool', Case.status, Case.owner_id)
Index('ix_case_lifecycle_owner', Case.lifecycle, Case.owner_id)
Index('ix_task_due_open_case', Task.done_at, Task.due_at, Task.case_id)
Index('ix_task_category_open_case', Task.category, Task.done_at, Task.case_id)
Index('ix_ledger_due_case', Ledger.due_date, Ledger.case_id)
Index('ix_delivery_case_item', Delivery.case_id, Delivery.item_id)
Index('ix_production_active_stage', Production.cancelled_at, Production.stage, Production.case_id)
