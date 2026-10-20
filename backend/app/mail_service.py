"""Yahoo IMAP/SMTP adapter and message classification for Comgraf."""
import hashlib
import html
import imaplib
import logging
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from email import policy
from email.header import decode_header
from email.message import EmailMessage as MIMEMessage
from email.parser import BytesParser
from email.utils import formataddr, getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError

from .config import settings
from .mail_security import decrypt_password
from .mail_transport import ALLOWED_MIME, MAX_ATTACHMENT_BYTES, SENDER_NAME, MailFailure, _uidvalidity, append_sent_copy, open_imap, smtp_send, status_label
from .models import Case, Client, Contact, Event, MailAccount, MailAttachment, MailMessage, MailSyncCursor, User, now
from .repository import client_search_text
from .services import attach_incoming_mail, complete_resolved_production, create_incoming_email_case, event as add_event

logger = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_BODY_CHARS = 20000
EMAIL_RE = re.compile(r'^[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+$')
MESSAGE_ID_RE = re.compile(r'<[^<>\s]+>')
CASE_REF_RE = re.compile(r'\bCG-\d{6}\b', re.IGNORECASE)


def clean_address(value):
    _, address = parseaddr(str(value or '').strip())
    address = address.strip().lower()
    return address if EMAIL_RE.fullmatch(address) else ''


def account_projection(account):
    if not account:
        return {
            'configured': False,
            'smtp_ready': False,
            'email': account.email if account else '',
            'has_password': bool(account and account.encrypted_password),
            'status': 'disconnected',
            'status_label': 'No configurado',
            'imap_status': 'not_checked',
            'smtp_status': 'not_checked',
            'imap_label': status_label('not_checked'),
            'smtp_label': status_label('not_checked'),
            'last_sync_at': account.last_sync_at.isoformat() + 'Z' if account and account.last_sync_at else None,
        }
    if not account.active:
        status = account.status if account.status in {'authentication_error', 'connection_error', 'protocol_error'} else 'disconnected'
        return {
            'configured': False,
            'smtp_ready': False,
            'email': account.email,
            'has_password': bool(account.encrypted_password),
            'status': status,
            'status_label': status_label(status) if status != 'disconnected' else 'No configurado',
            'imap_status': account.imap_status,
            'smtp_status': account.smtp_status,
            'imap_label': status_label(account.imap_status),
            'smtp_label': status_label(account.smtp_status),
            'last_sync_at': account.last_sync_at.isoformat() + 'Z' if account.last_sync_at else None,
        }
    protocol_errors = {account.imap_status, account.smtp_status} & {'authentication_error', 'connection_error', 'protocol_error'}
    status = 'authentication_error' if 'authentication_error' in protocol_errors else 'connection_error' if 'connection_error' in protocol_errors else 'protocol_error' if protocol_errors else 'connected'
    return {
        'configured': status == 'connected',
        'smtp_ready': account.smtp_status == 'connected',
        'email': account.email,
        'has_password': bool(account.encrypted_password),
        'status': status,
        'status_label': status_label(status),
        'imap_status': account.imap_status,
        'smtp_status': account.smtp_status,
        'imap_label': status_label(account.imap_status),
        'smtp_label': status_label(account.smtp_status),
        'last_sync_at': account.last_sync_at.isoformat() + 'Z' if account.last_sync_at else None,
    }


def _decode_header(value):
    if value is None:
        return ''
    try:
        return str(value)
    except (UnicodeDecodeError, TypeError):
        pieces = []
        for part, charset in decode_header(str(value)):
            if isinstance(part, bytes):
                try:
                    pieces.append(part.decode(charset or 'utf-8', errors='replace'))
                except LookupError:
                    pieces.append(part.decode('utf-8', errors='replace'))
            else:
                pieces.append(part)
        return ''.join(pieces)


class _TextOnlyHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ('script', 'style', 'iframe', 'object', 'svg'):
            self.hidden += 1
        elif tag.lower() in ('br', 'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag.lower() in ('script', 'style', 'iframe', 'object', 'svg') and self.hidden:
            self.hidden -= 1
        elif tag.lower() in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _html_to_text(value):
    parser = _TextOnlyHTML()
    try:
        parser.feed(value)
        return html.unescape(''.join(parser.parts))
    except Exception:
        return ''


def _decode_part(part):
    try:
        content = part.get_content()
        return content if isinstance(content, str) else content.decode('utf-8', errors='replace')
    except Exception:
        payload = part.get_payload(decode=True) or b''
        charset = part.get_content_charset() or 'utf-8'
        try:
            return payload.decode(charset, errors='replace')
        except LookupError:
            return payload.decode('utf-8', errors='replace')


def _parse_ids(value):
    return [value for value in MESSAGE_ID_RE.findall(str(value or '')) if len(value) <= 255][:50]


def _imap_internal_date(metadata):
    match = re.search(rb'\bINTERNALDATE\s+"([^"]+)"', metadata or b'', re.IGNORECASE)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1).decode('ascii'), '%d-%b-%Y %H:%M:%S %z').astimezone(timezone.utc).replace(tzinfo=None)
    except (UnicodeDecodeError, ValueError):
        return None


def parse_raw_message(raw):
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError('message_too_large')
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sender_name, sender = parseaddr(_decode_header(msg.get('From', '')))
    sender = clean_address(sender)
    to = sorted({clean_address(address) for _, address in getaddresses([_decode_header(msg.get('To', ''))]) if clean_address(address)})
    cc = sorted({clean_address(address) for _, address in getaddresses([_decode_header(msg.get('Cc', ''))]) if clean_address(address)})
    plain_parts, html_parts, attachments = [], [], []
    attachment_bytes = 0
    if msg.is_multipart():
        parts = list(msg.walk())
    else:
        parts = [msg]
    for part in parts:
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == 'attachment' or filename:
            content = part.get_payload(decode=True) or b''
            mime = part.get_content_type().lower()
            safe_name = Path(str(_decode_header(filename or 'archivo')).replace('\\', '/')).name
            safe_name = re.sub(r'[\x00-\x1f\x7f]+', '_', safe_name).strip(' .')[:240] or 'archivo'
            if mime in ALLOWED_MIME and content and len(content) <= MAX_ATTACHMENT_BYTES and attachment_bytes + len(content) <= MAX_ATTACHMENT_BYTES:
                attachments.append((safe_name[:240] or 'archivo', mime, content))
                attachment_bytes += len(content)
            continue
        if part.get_content_type() == 'text/plain':
            plain_parts.append(_decode_part(part))
        elif part.get_content_type() == 'text/html':
            html_parts.append(_decode_part(part))
    body = '\n'.join(value for value in plain_parts if value.strip())
    if not body and html_parts:
        body = _html_to_text('\n'.join(html_parts))
    body = body.replace('\x00', '')[:MAX_BODY_CHARS]
    date_value = None
    try:
        date_value = parsedate_to_datetime(msg.get('Date')).astimezone(timezone.utc).replace(tzinfo=None) if msg.get('Date') else None
    except (TypeError, ValueError, OverflowError):
        pass
    refs = _parse_ids(msg.get('References'))
    in_reply_to = _parse_ids(msg.get('In-Reply-To'))
    return {
        'sender_name': sender_name.strip()[:180],
        'sender': sender,
        'to': to,
        'cc': cc,
        'subject': _decode_header(msg.get('Subject', '')).replace('\r', ' ').replace('\n', ' ')[:998],
        'body': body,
        'message_id': _parse_ids(msg.get('Message-ID'))[0] if _parse_ids(msg.get('Message-ID')) else None,
        'in_reply_to': in_reply_to[-1] if in_reply_to else None,
        'references': refs,
        'received_at': date_value,
        'auto_submitted': _decode_header(msg.get('Auto-Submitted', '')).lower(),
        'precedence': _decode_header(msg.get('Precedence', '')).lower(),
        'content_type': msg.get_content_type().lower(),
        'attachments': attachments,
    }


def is_automatic(parsed):
    sender = parsed['sender'].split('@', 1)[0] if parsed['sender'] else ''
    return bool(
        (parsed['auto_submitted'] and parsed['auto_submitted'] != 'no')
        or parsed['precedence'] in {'bulk', 'list', 'junk'}
        or parsed['content_type'] == 'multipart/report'
        or sender in {'mailer-daemon', 'postmaster', 'noreply', 'no-reply', 'donotreply', 'do-not-reply'}
    )


def _is_bounce(parsed):
    subject = (parsed['subject'] or '').casefold()
    sender = parsed['sender'].split('@', 1)[0] if parsed['sender'] else ''
    return parsed['content_type'] == 'multipart/report' or sender in {'mailer-daemon', 'postmaster'} or any(
        marker in subject for marker in ('delivery failed', 'undeliverable', 'delivery status notification', 'failure notice', 'no se pudo entregar')
    )


def _save_attachment(message, filename, mime, content):
    ext = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg', 'text/plain': '.txt'}[mime]
    key = uuid.uuid4().hex + ext
    directory = Path(settings.upload_dir) / 'email'
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        directory.chmod(0o700)
    path = directory / key
    with path.open('xb') as file:
        file.write(content)
    attachment = MailAttachment(message_id=message.id, filename=filename, mime=mime, storage_key='email/' + key, size=len(content), sha256=hashlib.sha256(content).hexdigest())
    return attachment, path


def _matching_clients(db, address):
    if not address:
        return []
    target = address.casefold()
    found = {}
    for client in db.scalars(select(Client)):
        if any(str(value).casefold() == target for value in (client.emails or [])):
            found[client.id] = client
    for contact in db.scalars(select(Contact).where(func.lower(Contact.email) == target)):
        client = db.get(Client, contact.client_id)
        if client:
            found[client.id] = client
    return list(found.values())


def _case_subject(subject):
    subject = (subject or '').strip()
    return (subject or 'Consulta recibida por correo')[:240]


def _create_incoming_case(db, client, parsed, mail_row):
    return create_incoming_email_case(
        db, client, mail_row, _case_subject(parsed['subject']), parsed['body'], _event_body(parsed),
    )


def _event_body(parsed):
    body = parsed['body'] or '(El mensaje no contiene texto legible.)'
    return f"De: {parsed['sender']}\nAsunto: {parsed['subject'] or '(sin asunto)'}\n\n{body}"[:25000]


def _link_incoming(db, case, parsed, mail_row):
    if not mail_row.thread_root:
        mail_row.thread_root = mail_row.message_id or mail_row.in_reply_to
    attach_incoming_mail(db, case, mail_row, _event_body(parsed))


def _record_bounce(db, account_id, parsed, mail_row):
    references = list(dict.fromkeys(([parsed['in_reply_to']] if parsed['in_reply_to'] else []) + parsed['references']))
    if not references:
        mail_row.status = 'Ignorado'
        return False
    sent = db.scalars(select(MailMessage).where(
        MailMessage.account_id == account_id, MailMessage.direction == 'Saliente',
        MailMessage.message_id.in_(references), MailMessage.case_id.is_not(None),
    )).all()
    cases = {row.case_id for row in sent}
    if len(cases) != 1:
        mail_row.status = 'Ignorado'
        return False
    original = sent[0]
    case = db.get(Case, original.case_id)
    if not case:
        mail_row.status = 'Ignorado'
        return False
    original.status = 'Error'
    original.error_code = 'bounce'
    mail_row.thread_root = original.thread_root or original.message_id
    return attach_incoming_mail(db, case, mail_row, _event_body(parsed), event_kind='Incidencia de correo',
        event_data={'bounced_email_message_id': original.id}, reopen_origin='email_bounce',
        reopen_body='Atención abierta por un rebote de correo')


def _thread_target(db, account_id, parsed):
    reference_ids = list(dict.fromkeys(([parsed['in_reply_to']] if parsed['in_reply_to'] else []) + parsed['references']))
    if not reference_ids:
        return None, None
    known = db.scalars(select(MailMessage).where(MailMessage.account_id == account_id, MailMessage.message_id.in_(reference_ids))).all()
    cases = {message.case_id for message in known if message.case_id is not None and message.status in {'Vinculado', 'Aceptado por Yahoo'}}
    roots = {message.thread_root or message.message_id for message in known if message.case_id is not None}
    if len(cases) != 1:
        return None, 'Referencia ambigua' if len(cases) > 1 else None
    case_id = next(iter(cases))
    participants = set()
    if roots:
        thread_rows = db.scalars(select(MailMessage).where(MailMessage.account_id == account_id, MailMessage.thread_root.in_(roots))).all()
        for previous in thread_rows:
            participants.add(previous.sender.casefold())
            participants.update(str(address).casefold() for address in (previous.to_addresses or []) + (previous.cc_addresses or []))
    if parsed['sender'].casefold() not in participants:
        return None, 'Participante no reconocido'
    case = db.get(Case, case_id)
    return (case, None) if case else (None, 'Solicitud no disponible')


def _case_number_target(db, parsed):
    match = CASE_REF_RE.search(parsed['subject'] or '')
    if not match:
        return None, None
    case = db.get(Case, int(match.group(0).split('-', 1)[1]))
    clients = _matching_clients(db, parsed['sender'])
    if case and len(clients) == 1 and clients[0].id == case.client_id:
        return case, None
    return None, 'Referencia ambigua'


def _store_incoming(db, account, folder, uidvalidity, uid, raw):
    written_files = []
    try:
        return _store_incoming_impl(db, account, folder, uidvalidity, uid, raw, written_files)
    except Exception:
        for path in written_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _store_oversized_message(db, account, folder, uidvalidity, uid, raw_headers):
    try:
        parsed = parse_raw_message(raw_headers)
    except Exception:
        parsed = {'sender': '', 'to': [], 'cc': [], 'subject': 'Correo demasiado grande', 'message_id': None,
            'in_reply_to': None, 'references': [], 'received_at': None}
    digest = hashlib.sha256(raw_headers).hexdigest()
    condition = or_(MailMessage.content_hash == digest, MailMessage.message_id == parsed['message_id']) if parsed['message_id'] else MailMessage.content_hash == digest
    existing = db.scalar(select(MailMessage).where(MailMessage.account_id == account.id, condition))
    if existing:
        return existing
    message = MailMessage(
        account_id=account.id, folder=folder, uidvalidity=uidvalidity, uid=uid,
        message_id=parsed['message_id'], content_hash=digest, in_reply_to=parsed['in_reply_to'],
        references_json=parsed['references'], thread_root=parsed['references'][0] if parsed['references'] else parsed['in_reply_to'],
        direction='Entrante', status='Error de análisis', sender=parsed['sender'], to_addresses=parsed['to'],
        cc_addresses=parsed['cc'], subject=parsed['subject'][:998],
        body_text='El correo supera el límite de 25 MB. Revisa el mensaje original en Yahoo.',
        attachment_ids=[], received_at=parsed['received_at'] or now(),
    )
    db.add(message)
    db.flush()
    return message


def _store_incoming_impl(db, account, folder, uidvalidity, uid, raw, written_files):
    digest = hashlib.sha256(raw).hexdigest()
    try:
        parsed = parse_raw_message(raw)
        state = 'Pendiente de clasificación'
    except Exception:
        parsed = {
            'sender': '', 'sender_name': '', 'to': [], 'cc': [], 'subject': 'Mensaje de correo malformado',
            'body': 'No se pudo interpretar el contenido MIME. Revisa el buzón original en Yahoo.',
            'message_id': None, 'in_reply_to': None, 'references': [], 'received_at': None,
            'auto_submitted': '', 'precedence': '', 'content_type': '', 'attachments': [],
        }
        state = 'Error de análisis'
    existing = db.scalar(select(MailMessage).where(
        MailMessage.account_id == account.id,
        or_(MailMessage.content_hash == digest, MailMessage.message_id == parsed['message_id']) if parsed['message_id'] else MailMessage.content_hash == digest,
    ))
    if existing:
        return existing, []
    message = MailMessage(
        account_id=account.id, folder=folder, uidvalidity=uidvalidity, uid=uid,
        message_id=parsed['message_id'], content_hash=digest,
        in_reply_to=parsed['in_reply_to'], references_json=parsed['references'],
        thread_root=parsed['references'][0] if parsed['references'] else parsed['in_reply_to'] or parsed['message_id'],
        direction='Entrante', status=state, sender=parsed['sender'],
        to_addresses=parsed['to'], cc_addresses=parsed['cc'], subject=parsed['subject'],
        body_text=parsed['body'], attachment_ids=[], received_at=parsed['received_at'] or now(),
    )
    db.add(message)
    db.flush()
    for filename, mime, content in parsed['attachments']:
        attachment, path = _save_attachment(message, filename, mime, content)
        written_files.append(path)
        db.add(attachment)
    if state == 'Error de análisis':
        return message, written_files
    if account.email.casefold() == parsed['sender'].casefold() or is_automatic(parsed) or not parsed['sender']:
        if account.email.casefold() != parsed['sender'].casefold() and _is_bounce(parsed):
            _record_bounce(db, account.id, parsed, message)
        else:
            message.status = 'Ignorado'
        return message, written_files
    if _is_bounce(parsed):
        _record_bounce(db, account.id, parsed, message)
        return message, written_files
    thread_case, thread_problem = _thread_target(db, account.id, parsed)
    if not thread_case and not thread_problem:
        thread_case, thread_problem = _case_number_target(db, parsed)
    if thread_case:
        message.thread_root = next((row.thread_root or row.message_id for row in db.scalars(
            select(MailMessage).where(MailMessage.account_id == account.id, MailMessage.message_id.in_(([parsed['in_reply_to']] if parsed['in_reply_to'] else []) + parsed['references']))
        ) if row.thread_root or row.message_id), message.thread_root)
        _link_incoming(db, thread_case, parsed, message)
        return message, written_files
    if thread_problem:
        message.status = thread_problem
        return message, written_files
    clients = _matching_clients(db, parsed['sender'])
    if len(clients) == 1:
        _create_incoming_case(db, clients[0], parsed, message)
    elif len(clients) > 1:
        message.status = 'Coincidencia ambigua'
    else:
        display = (parsed['sender_name'] or parsed['sender'])[:180]
        client = Client(name=display, emails=[parsed['sender']], search_text='')
        client.search_text = client_search_text(client)
        db.add(client)
        db.flush()
        _create_incoming_case(db, client, parsed, message)
    return message, written_files


def _message_public(db, message):
    attachments = db.scalars(select(MailAttachment).where(MailAttachment.message_id == message.id).order_by(MailAttachment.id)).all()
    return {
        'id': message.id, 'case_id': message.case_id, 'direction': message.direction, 'status': message.status,
        'retry_of_id': message.retry_of_id,
        'sender': message.sender, 'to': message.to_addresses or [], 'cc': message.cc_addresses or [],
        'subject': message.subject, 'body_text': message.body_text, 'message_id': message.message_id,
        'attachment_ids': message.attachment_ids or [],
        'thread_root': message.thread_root, 'created_at': message.created_at.isoformat() + 'Z',
        'received_at': message.received_at.isoformat() + 'Z' if message.received_at else None,
        'error_code': message.error_code, 'sent_copy_status': message.sent_copy_status,
        'attachments': [{'id': attachment.id, 'filename': attachment.filename, 'mime': attachment.mime, 'size': attachment.size} for attachment in attachments],
    }


def _outgoing_mime(db, row, account, password):
    msg = MIMEMessage(policy=policy.SMTP)
    msg['From'] = formataddr((SENDER_NAME, account.email))
    msg['To'] = ', '.join(row.to_addresses)
    msg['Reply-To'] = account.email
    msg['Subject'] = row.subject
    msg['Date'] = row.created_at.replace(tzinfo=timezone.utc)
    msg['Message-ID'] = row.message_id
    if row.thread_root:
        latest = db.scalar(select(MailMessage).where(
            MailMessage.account_id == account.id,
            MailMessage.case_id == row.case_id,
            MailMessage.thread_root == row.thread_root,
            MailMessage.id != row.id,
            MailMessage.message_id.is_not(None),
        ).order_by(MailMessage.created_at.desc(), MailMessage.id.desc()).limit(1))
        if latest:
            row.in_reply_to = latest.message_id
            msg['In-Reply-To'] = latest.message_id
            refs = list(latest.references_json or [])
            if latest.message_id not in refs:
                refs.append(latest.message_id)
            row.references_json = refs[-50:]
            msg['References'] = ' '.join(row.references_json)
    msg.set_content(row.body_text)
    attachment_ids = row.attachment_ids or []
    if attachment_ids:
        from .models import Attachment
        attachments = db.scalars(select(Attachment).where(Attachment.id.in_(attachment_ids), Attachment.case_id == row.case_id)).all()
        if len(attachments) != len(set(attachment_ids)):
            raise MailFailure('attachment_missing')
        total = 0
        for attachment in attachments:
            if attachment.mime not in ALLOWED_MIME:
                raise MailFailure('attachment_type')
            path = Path(settings.upload_dir) / attachment.storage_key
            if not path.is_file():
                raise MailFailure('attachment_missing')
            content = path.read_bytes()
            total += len(content)
            if len(content) != attachment.size or total > MAX_ATTACHMENT_BYTES:
                raise MailFailure('attachment_size')
            maintype, subtype = attachment.mime.split('/', 1)
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=Path(attachment.name).name)
    return msg.as_bytes(policy=policy.SMTP)


def process_outbox_message(SessionLocal, message_id):
    """Send one committed queue row; only explicitly safe failures can be retried."""
    with SessionLocal.begin() as db:
        row = db.scalar(select(MailMessage).where(MailMessage.id == message_id).with_for_update())
        if not row or row.direction != 'Saliente' or row.status != 'Pendiente':
            return
        account = db.get(MailAccount, row.account_id)
        if not account or not account.active or not account.encrypted_password:
            row.status = 'Pausado'
            row.error_code = 'account_disconnected'
            return
        row.status = 'Enviando'
        row.error_code = None
        email_address, encrypted = account.email, account.encrypted_password
        try:
            password = decrypt_password(encrypted)
        except RuntimeError:
            row.status = 'Error'
            row.error_code = 'key_unavailable'
            return
        try:
            payload = _outgoing_mime(db, row, account, password)
        except MailFailure as exc:
            row.status = 'Error'
            row.error_code = exc.code
            return
        except OSError:
            row.status = 'Error'
            row.error_code = 'attachment_unavailable'
            return
        except Exception:
            row.status = 'Error'
            row.error_code = 'message_build_error'
            return
        db.flush()
        case_id = row.case_id
        actor_id = row.actor_id
        recipients = row.to_addresses
        stored_message_id = row.message_id
        stored_payload = payload

    try:
        smtp_send(email_address, password, recipients, stored_payload)
        send_status, error_code = 'Aceptado por Yahoo', None
        sent_copy_status = append_sent_copy(email_address, password, stored_message_id, stored_payload)
    except MailFailure as exc:
        if exc.code == 'uncertain':
            send_status, error_code, sent_copy_status = 'Resultado incierto', exc.code, None
        else:
            send_status, error_code, sent_copy_status = 'Error', exc.code, None

    with SessionLocal.begin() as db:
        row = db.scalar(select(MailMessage).where(MailMessage.id == message_id).with_for_update())
        if not row or row.status != 'Enviando':
            return
        row.status, row.error_code, row.sent_copy_status = send_status, error_code, sent_copy_status
        if send_status != 'Aceptado por Yahoo':
            if error_code in {'authentication_error', 'connection_error'}:
                account = db.get(MailAccount, row.account_id)
                if account and account.active:
                    account.status = error_code
                    account.last_error = error_code
                    account.smtp_status = error_code
            return
        row.smtp_accepted_at = now()
        case = db.get(Case, case_id)
        actor = db.get(User, actor_id) if actor_id else None
        if case:
            # Compare server import time; the sender-controlled Date header is only display metadata.
            incoming_time = db.scalar(select(func.max(MailMessage.created_at)).where(
                MailMessage.case_id == case.id, MailMessage.direction == 'Entrante', MailMessage.status == 'Vinculado'))
            if incoming_time is None or incoming_time <= row.smtp_accepted_at:
                case.pending_message = False
            if case.first_response_at is None:
                case.first_response_at = row.smtp_accepted_at
            add_event(db, case, actor, f"Correo enviado a {', '.join(row.to_addresses)}\nAsunto: {row.subject}\n\n{row.body_text}"[:25000],
                kind='Correo saliente', channel='Correo', data={
                    'direction': 'Saliente', 'email_message_id': row.id, 'message_id': row.message_id,
                    'thread_root': row.thread_root, 'smtp_status': 'Aceptado por Yahoo',
                    'sent_copy_status': row.sent_copy_status,
                })
            case.version += 1
            complete_resolved_production(db, case, actor, 'email_sent')


def recover_stuck_outbox(SessionLocal):
    threshold = datetime.utcnow().replace(microsecond=0)
    with SessionLocal.begin() as db:
        rows = db.scalars(select(MailMessage).where(
            MailMessage.direction == 'Saliente', MailMessage.status == 'Enviando',
            MailMessage.created_at < threshold,
        ).with_for_update()).all()
        # A worker crash may have happened after Yahoo accepted DATA. Never auto-send these again.
        for row in rows:
            row.status = 'Resultado incierto'
            row.error_code = 'worker_interrupted'


def sync_account(SessionLocal, account_id):
    with SessionLocal() as db:
        account = db.get(MailAccount, account_id)
        if not account or not account.active or not account.encrypted_password:
            return True
        email_address, encrypted = account.email, account.encrypted_password
    try:
        password = decrypt_password(encrypted)
    except RuntimeError:
        with SessionLocal.begin() as db:
            account = db.get(MailAccount, account_id)
            if account and account.active:
                account.status = 'connection_error'
                account.last_error = 'key_unavailable'
        return False
    connection = None
    try:
        connection = open_imap()
        try:
            connection.login(email_address, password)
        except imaplib.IMAP4.error as exc:
            raise MailFailure('authentication_error') from exc
        result, _ = connection.select('INBOX', readonly=True)
        if result != 'OK':
            raise MailFailure('connection_error')
        validity = _uidvalidity(connection)
        with SessionLocal.begin() as db:
            account = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
            if not account or not account.active:
                return True
            cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == account_id, MailSyncCursor.folder == 'INBOX').with_for_update())
            if not cursor:
                # This fallback is only for a partially repaired database. Normal activation writes a baseline.
                cursor = MailSyncCursor(account_id=account_id, folder='INBOX', uidvalidity=validity, last_uid=0, activated_at=now())
                db.add(cursor)
            elif cursor.uidvalidity != validity:
                # Rescan this mailbox generation; Message-ID/content hashes deduplicate retained messages.
                cursor.uidvalidity = validity
                cursor.last_uid = 0
        with SessionLocal() as db:
            cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == account_id, MailSyncCursor.folder == 'INBOX'))
            start_uid = int(cursor.last_uid if cursor and cursor.uidvalidity == validity else 0) + 1
            activation_floor = cursor.activated_at.replace(microsecond=0) if cursor and cursor.activated_at else None
        result, search_data = connection.uid('search', None, 'UID', f'{start_uid}:*')
        if result != 'OK':
            raise MailFailure('connection_error')
        uids = [int(value) for value in (search_data[0] or b'').split() if value.isdigit() and int(value) >= start_uid][:50]
        for uid in uids:
            result, size_rows = connection.uid('fetch', str(uid), '(RFC822.SIZE INTERNALDATE)')
            if result != 'OK':
                raise MailFailure('connection_error')
            size_metadata = next((part[0] for part in size_rows if isinstance(part, tuple) and isinstance(part[0], bytes)), b'')
            size_match = re.search(rb'\bRFC822\.SIZE\s+(\d+)', size_metadata, re.IGNORECASE)
            if not size_match:
                raise MailFailure('connection_error')
            message_size = int(size_match.group(1))
            internal_date = _imap_internal_date(size_metadata)
            after_activation = bool(
                activation_floor is not None
                and internal_date is not None
                and internal_date >= activation_floor
            )
            oversized = message_size > MAX_MESSAGE_BYTES
            if not after_activation:
                # Fail closed if Yahoo omits INTERNALDATE: never import ambiguous
                # history when resuming from a reset UIDVALIDITY generation.
                raw = None
            elif oversized:
                result, fetched = connection.uid('fetch', str(uid), '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO SUBJECT DATE REFERENCES IN-REPLY-TO)])')
                if result != 'OK':
                    raise MailFailure('connection_error')
                raw = next((part[1] for part in fetched if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            else:
                result, fetched = connection.uid('fetch', str(uid), '(BODY.PEEK[])')
                if result != 'OK':
                    raise MailFailure('connection_error')
                raw = next((part[1] for part in fetched if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            newly_written = []
            try:
                with SessionLocal.begin() as db:
                    account = db.scalar(select(MailAccount).where(MailAccount.id == account_id).with_for_update())
                    if not account or not account.active:
                        return True
                    cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == account_id, MailSyncCursor.folder == 'INBOX').with_for_update())
                    if raw and oversized and after_activation:
                        _store_oversized_message(db, account, 'INBOX', validity, uid, raw)
                    elif raw and not oversized and after_activation:
                        _, newly_written = _store_incoming(db, account, 'INBOX', validity, uid, raw)
                    cursor.uidvalidity, cursor.last_uid = validity, uid
                    cursor.last_synced_at = now()
                    account.last_sync_at = cursor.last_synced_at
                    account.status, account.last_error = 'connected', None
                    account.imap_status = 'connected'
            except Exception:
                # Files from this UID were written before its transaction committed.
                for path in newly_written:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
        with SessionLocal.begin() as db:
            account = db.scalar(select(MailAccount).where(MailAccount.id == account_id, MailAccount.active.is_(True)))
            if account:
                cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == account_id, MailSyncCursor.folder == 'INBOX'))
                if cursor:
                    cursor.last_synced_at = now()
                    account.last_sync_at = cursor.last_synced_at
                account.status, account.imap_status, account.last_error = 'connected', 'connected', None
        return True
    except MailFailure as exc:
        with SessionLocal.begin() as db:
            account = db.get(MailAccount, account_id)
            if account and account.active:
                account.status = exc.code
                account.last_error = exc.code
                account.imap_status = exc.code
        logger.warning('Yahoo IMAP sincronización fallida: %s', exc.code)
        return False
    except (imaplib.IMAP4.error, OSError, socket.timeout, TimeoutError) as exc:
        code = 'connection_error'
        with SessionLocal.begin() as db:
            account = db.get(MailAccount, account_id)
            if account and account.active:
                account.status, account.last_error, account.imap_status = code, code, code
        logger.warning('Yahoo IMAP sincronización fallida: %s', code)
        return False
    except Exception:
        logger.exception('Error interno al procesar correo Yahoo; la siguiente ejecución continuará desde el último UID confirmado.')
        return False
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass


def pause_outbox_for_account(db, account_id):
    rows = db.scalars(select(MailMessage).where(
        MailMessage.account_id == account_id, MailMessage.direction == 'Saliente',
        MailMessage.status == 'Pendiente',
    ).with_for_update()).all()
    for row in rows:
        row.status = 'Pausado'
        row.error_code = 'account_changed'


def unclassified_messages(db, limit=100):
    rows = db.scalars(select(MailMessage).where(
        MailMessage.direction == 'Entrante',
        MailMessage.status.in_(['Pendiente de clasificación', 'Coincidencia ambigua', 'Referencia ambigua', 'Participante no reconocido', 'Solicitud no disponible', 'Error de análisis']),
    ).order_by(MailMessage.created_at, MailMessage.id).limit(limit)).all()
    result = []
    for message in rows:
        clients = _matching_clients(db, message.sender)
        client_ids = [client.id for client in clients]
        candidate_cases = db.scalars(select(Case).where(Case.client_id.in_(client_ids)).order_by(Case.updated_at.desc()).limit(100)).all() if client_ids else []
        if message.case_id and all(case.id != message.case_id for case in candidate_cases):
            existing_candidate = db.get(Case, message.case_id)
            if existing_candidate:
                candidate_cases.append(existing_candidate)
        result.append({
            'id': message.id, 'status': message.status, 'sender': message.sender, 'subject': message.subject,
            'body_text': message.body_text, 'received_at': message.received_at.isoformat() + 'Z' if message.received_at else None,
            'candidate_case_id': message.case_id,
            'clients': [{'id': client.id, 'name': client.name, 'company': client.company, 'provisional': client.provisional} for client in clients],
            'candidate_cases': [{'id': case.id, 'number': f'CG-{case.id:06d}', 'subject': case.subject, 'lifecycle': case.lifecycle, 'status': case.status} for case in candidate_cases],
            'attachments': [{'id': attachment.id, 'filename': attachment.filename, 'mime': attachment.mime, 'size': attachment.size}
                for attachment in db.scalars(select(MailAttachment).where(MailAttachment.message_id == message.id))],
        })
    return result
