from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Date, Numeric, JSON, ForeignKey, UniqueConstraint, Index
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
    collection_owner_id = Column(ForeignKey('users.id'))
    priority = Column(String(20), nullable=False, default='Normal')
    status = Column(String(40), nullable=False, default='Nuevo', index=True)
    mode = Column(String(20), nullable=False, default='Atención', index=True)
    next_action = Column(String(500), nullable=False, default='')
    pending_message = Column(Boolean, nullable=False, default=False)
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
    owner_id = Column(ForeignKey('users.id'), nullable=False)
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
    case_id = Column(ForeignKey('cases.id'), primary_key=True)
    quote_id = Column(ForeignKey('quotes.id'), unique=True)
    owner_id = Column(ForeignKey('users.id'))
    stage = Column(String(40), nullable=False, default='Preparación', index=True)
    stage_started_at = Column(DateTime, nullable=False, default=now)
    blocked_since = Column(DateTime)
    block_reason = Column(Text)
    cancelled_at = Column(DateTime)
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
    art_version = Column(String(100), nullable=False, default='')
    approval_evidence = Column(Text)
    approved_snapshot = Column(JSON)
    authorized_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=now)


class Item(Base):
    __tablename__ = 'items'
    id = Column(Integer, primary_key=True)
    case_id = Column(ForeignKey('productions.case_id'), nullable=False, index=True)
    product = Column(String(80), nullable=False)
    quantity = Column(Numeric(14, 3), nullable=False)
    unit = Column(String(30), nullable=False)
    unit_price = Column(Numeric(14, 2), nullable=False)
    specs = Column(JSON, nullable=False)
    substage = Column(String(40), nullable=False, default='Diseño')


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


class SavedView(Base):
    __tablename__ = 'saved_views'
    __table_args__ = (UniqueConstraint('user_id', 'name'),)
    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey('users.id'), nullable=False)
    name = Column(String(100), nullable=False)
    filters = Column(JSON, nullable=False)


class ProviderInbox(Base):
    __tablename__ = 'provider_inbox'
    __table_args__ = (UniqueConstraint('provider', 'external_id'),)
    id = Column(Integer, primary_key=True)
    provider = Column(String(30), nullable=False)
    external_id = Column(String(180), nullable=False)
    case_id = Column(ForeignKey('cases.id'))
    payload = Column(JSON, nullable=False)
    classification = Column(String(30), nullable=False, default='Pendiente')


Index('ix_case_pool', Case.status, Case.owner_id)
