import hashlib
import json
import re
import uuid
from datetime import datetime
from email.utils import make_msgid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy import func

from .. import schemas as s, services as service
from ..db import SessionLocal, get_db
from ..mail_security import encrypt_password, decrypt_password
from ..mail_service import (
    account_projection, clean_address, pause_outbox_for_account, retry_sent_copy, unclassified_messages,
    _message_public,
)
from ..mail_transport import append_sent_copy, check_credentials, status_label
from ..models import (Attachment, Case, Client, Event, MailAccount, MailAttachment, MailMessage,
                      MailSyncCursor, MailSyncOutcome, Role, now)
from ..repository import get_case, row
from ..security import can, current_user, require
from ..mail_transport import MailFailure

router = APIRouter(prefix='/api', tags=['Correo'])
CASE_REF_RE = re.compile(r'\bCG-\d{6}\b', re.IGNORECASE)


def require_admin(db, user):
    require(db, user, '*')


def _current_account(db, lock=False):
    query = select(MailAccount).where(MailAccount.active.is_(True)).order_by(MailAccount.id.desc())
    if lock:
        query = query.with_for_update()
    return db.scalar(query.limit(1))


def _latest_account(db):
    return db.scalar(select(MailAccount).order_by(MailAccount.id.desc()).limit(1))


def _status_from_results(result):
    statuses = {result['imap']['status'], result['smtp']['status']}
    if 'authentication_error' in statuses:
        return 'authentication_error'
    priority = ('dns_error', 'tls_error', 'timeout_error', 'inbox_select_error', 'inbox_query_error',
                'inbox_metadata_error', 'connection_error')
    return next((code for code in priority if code in statuses), 'protocol_error')


def _configured_result(db):
    return account_projection(_current_account(db) or _latest_account(db), db)


@router.get('/mail/config')
def mail_config(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require_admin(db, user)
    return _configured_result(db)


@router.get('/mail/status')
def mail_status(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    return _configured_result(db)


@router.put('/mail/config')
def save_mail_config(data: s.MailConfigurationInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require_admin(db, user)
    email_address = clean_address(data.email)
    if not email_address or email_address != data.email.strip().lower():
        raise HTTPException(422, 'Ingresa una dirección de correo válida')
    existing = db.scalar(select(MailAccount).where(MailAccount.email == email_address).with_for_update())
    password = data.application_password.get_secret_value() if data.application_password else ''
    password = ''.join(password.split())
    if not password:
        if not existing or not existing.encrypted_password:
            raise HTTPException(422, 'Ingresa la contraseña de aplicación de Yahoo')
        password = decrypt_password(existing.encrypted_password)

    result = check_credentials(email_address, password)
    # Serialize competing admins using a persistent row lock held until this request commits.
    db.scalar(select(Role).where(Role.name == 'Administrador').with_for_update())
    current = _current_account(db, lock=True)
    existing = db.scalar(select(MailAccount).where(MailAccount.email == email_address).with_for_update())
    if not result['imap']['ok'] or not result['smtp']['ok']:
        # Never disturb a currently working identity because a replacement failed its test.
        if not current and (not existing or not existing.active):
            account = existing or MailAccount(email=email_address, created_by_id=user.id, created_at=now())
            account.email = email_address
            account.encrypted_password = encrypt_password(password)
            account.active = False
            account.status = _status_from_results(result)
            account.last_error = account.status
            account.imap_status = result['imap']['status']
            account.smtp_status = result['smtp']['status']
            db.add(account)
        return {'saved': False, 'connection': result, 'account': _configured_result(db)}

    previous_accounts = db.scalars(select(MailAccount).where(MailAccount.active.is_(True)).with_for_update()).all()
    for previous in previous_accounts:
        if previous.id != (existing.id if existing else None):
            pause_outbox_for_account(db, previous.id)
            previous.active = False
            previous.encrypted_password = None
            previous.status = 'disconnected'
            previous.imap_status = 'disabled'
            previous.smtp_status = 'disabled'
            previous.last_error = None

    account = existing or MailAccount(email=email_address, created_by_id=user.id, created_at=now())
    account.email = email_address
    account.encrypted_password = encrypt_password(password)
    account.active = True
    account.status = 'connected'
    account.imap_status = 'connected'
    account.smtp_status = 'connected'
    account.last_error = None
    db.add(account)
    db.flush()

    cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == account.id, MailSyncCursor.folder == 'INBOX').with_for_update())
    if not cursor:
        # The checkpoint is committed now, before the worker starts, so the initial inbox is not imported.
        checkpoint = result['imap']['checkpoint']
        cursor = MailSyncCursor(account_id=account.id, folder='INBOX', uidvalidity=checkpoint['uidvalidity'],
            last_uid=checkpoint['last_uid'], activated_at=checkpoint.get('checked_at') or now(), last_synced_at=None)
        db.add(cursor)
    account.last_sync_at = cursor.last_synced_at
    return {'saved': True, 'connection': result, 'account': account_projection(account, db)}


@router.post('/mail/test', status_code=202)
def mail_test(data: s.MailTestInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require_admin(db, user)
    account = _current_account(db, lock=True)
    if not account or not account.encrypted_password:
        raise HTTPException(409, 'Configura primero el buzón Yahoo')
    if account.smtp_status != 'connected':
        raise HTTPException(409, 'SMTP no está conectado; comprueba la conexión antes de enviar la prueba')
    send_key = 'test:' + data.send_key
    existing = db.scalar(select(MailMessage).where(MailMessage.send_key == send_key))
    if existing:
        return {'id': existing.id, 'status': existing.status, 'sent_copy_status': existing.sent_copy_status, 'queued': existing.status == 'Pendiente'}
    message_id = make_msgid(domain=account.email.rsplit('@', 1)[-1])
    message = MailMessage(
        account_id=account.id, direction='Saliente', status='Pendiente', send_key=send_key,
        request_hash=hashlib.sha256(b'comgraf-mail-test-v1').hexdigest(), sender=account.email,
        to_addresses=[account.email], cc_addresses=[], subject='Prueba de conexión · Comgraf',
        body_text='Este mensaje confirma que Comgraf pudo enviar correo mediante Yahoo SMTP. La aceptación SMTP no confirma entrega ni lectura.',
        attachment_ids=[], thread_root=message_id, message_id=message_id, actor_id=user.id, created_at=now(),
    )
    db.add(message)
    db.flush()
    return {'id': message.id, 'status': message.status, 'sent_copy_status': None, 'queued': True}


@router.get('/mail/test/{message_id}')
def mail_test_status(message_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require_admin(db, user)
    message = db.get(MailMessage, message_id)
    if not message or message.direction != 'Saliente' or not message.send_key or not message.send_key.startswith('test:'):
        raise HTTPException(404, 'Prueba de correo no encontrada')
    return {'id': message.id, 'status': message.status, 'error_code': message.error_code,
            'sent_copy_status': message.sent_copy_status, 'smtp_accepted_at': message.smtp_accepted_at.isoformat() + 'Z' if message.smtp_accepted_at else None}


@router.delete('/mail/config')
def disconnect_mail(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require_admin(db, user)
    account = _current_account(db, lock=True)
    if account:
        pause_outbox_for_account(db, account.id)
        account.active = False
        account.encrypted_password = None
        account.status = 'disconnected'
        account.imap_status = 'disabled'
        account.smtp_status = 'disabled'
        account.last_error = None
    return {'ok': True, 'account': _configured_result(db)}


def _normalize_recipients(addresses):
    result = []
    for value in addresses:
        address = clean_address(value)
        if not address or address != value.strip().lower():
            raise HTTPException(422, 'Revisa el destinatario del correo')
        if address not in result:
            result.append(address)
    if not result:
        raise HTTPException(422, 'Ingresa al menos un destinatario')
    return result


def _resolve_sync_outcome(db, message, outcome, reason):
    if not message.uid or not message.uidvalidity or not message.folder:
        return
    row = db.scalar(select(MailSyncOutcome).where(
        MailSyncOutcome.account_id == message.account_id, MailSyncOutcome.folder == message.folder,
        MailSyncOutcome.uidvalidity == message.uidvalidity, MailSyncOutcome.uid == message.uid,
    ).with_for_update())
    if row:
        row.outcome = outcome
        row.reason = reason
        row.resolved_at = now()
        row.last_attempt_at = now()


def _require_case_editor(db, user, case):
    if not can(db, user, 'assign') and case.owner_id != user.id:
        raise HTTPException(403, 'Toma la solicitud antes de modificar su comunicación')


def _request_hash(case_id, payload):
    value = {'case_id': case_id, **payload.model_dump(mode='json', exclude={'send_key'})}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@router.post('/cases/{case_id}/email/send', status_code=202)
def queue_email(case_id: int, data: s.MailSendInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    case = get_case(db, user, case_id, lock=True)
    _require_case_editor(db, user, case)
    if case.lifecycle != 'Activo':
        raise HTTPException(409, 'Reactiva la solicitud antes de enviar un correo')
    account = _current_account(db, lock=True)
    if not account or not account.encrypted_password or account_projection(account, db)['smtp_ready'] is not True:
        raise HTTPException(409, 'El correo de la empresa no está conectado')
    recipients = _normalize_recipients(data.to)
    subject = data.subject.replace('\r', ' ').replace('\n', ' ').strip()
    case_number = f'CG-{case.id:06d}'
    if case_number.casefold() not in subject.casefold():
        subject = CASE_REF_RE.sub('', subject).strip()
        subject = f'[Solicitud {case_number}] {subject}'[:998]
    attachment_ids = list(dict.fromkeys(data.attachment_ids))
    attachments = db.scalars(select(Attachment).where(Attachment.id.in_(attachment_ids), Attachment.case_id == case.id)).all() if attachment_ids else []
    if len(attachments) != len(attachment_ids):
        raise HTTPException(422, 'Uno o más adjuntos ya no pertenecen a esta solicitud')
    allowed_mime = {'application/pdf', 'image/png', 'image/jpeg', 'text/plain'}
    total_size = 0
    for attachment in attachments:
        if attachment.mime not in allowed_mime:
            raise HTTPException(422, 'El correo solo admite PDF, PNG, JPG y TXT')
        total_size += attachment.size
    if total_size > 20 * 1024 * 1024:
        raise HTTPException(422, 'Los adjuntos no pueden superar 20 MB en total')
    if data.thread_root and not db.scalar(select(MailMessage.id).where(
        MailMessage.account_id == account.id, MailMessage.case_id == case.id,
        MailMessage.thread_root == data.thread_root, MailMessage.status.in_(['Vinculado', 'Aceptado por Yahoo']),
    )):
        raise HTTPException(422, 'El hilo seleccionado no pertenece a esta solicitud')
    payload_hash = _request_hash(case.id, data)
    previous = db.scalar(select(MailMessage).where(MailMessage.send_key == data.send_key))
    if previous:
        if previous.request_hash != payload_hash:
            raise HTTPException(409, 'La clave de envío ya se usó con otro contenido')
        return _message_public(db, previous)
    message_id = make_msgid(domain=account.email.rsplit('@', 1)[-1])
    message = MailMessage(
        account_id=account.id, case_id=case.id, direction='Saliente', status='Pendiente',
        send_key=data.send_key, request_hash=payload_hash, sender=account.email,
        to_addresses=recipients, cc_addresses=[], subject=subject, body_text=data.body,
        attachment_ids=attachment_ids, thread_root=data.thread_root or message_id,
        message_id=message_id, actor_id=user.id, created_at=now(),
    )
    db.add(message)
    db.flush()
    return _message_public(db, message)


@router.get('/mail/outbox/{message_id}')
def outbox_status(message_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    message = db.get(MailMessage, message_id)
    if not message or message.direction != 'Saliente' or not message.case_id:
        raise HTTPException(404, 'Correo saliente no encontrado')
    get_case(db, user, message.case_id)
    return _message_public(db, message)


@router.post('/mail/outbox/{message_id}/retry', status_code=202)
def retry_outbox(message_id: int, data: s.MailRetryInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    original = db.scalar(select(MailMessage).where(MailMessage.id == message_id).with_for_update())
    if not original or original.direction != 'Saliente' or not original.case_id:
        raise HTTPException(404, 'Correo saliente no encontrado')
    get_case(db, user, original.case_id)
    _require_case_editor(db, user, get_case(db, user, original.case_id))
    previous_retry = db.scalar(select(MailMessage).where(MailMessage.retry_of_id == original.id).with_for_update())
    if previous_retry:
        return _message_public(db, previous_retry)
    if original.status not in {'Error', 'Resultado incierto', 'Pausado'}:
        raise HTTPException(409, 'Este correo no está disponible para reintento')
    if original.status == 'Resultado incierto' and not data.confirm_uncertain:
        raise HTTPException(409, 'Confirma que revisaste Yahoo antes de reenviar un resultado incierto')
    account = _current_account(db)
    if not account or account.id != original.account_id or not account_projection(account, db)['configured']:
        raise HTTPException(409, 'Conecta la misma cuenta antes de reintentar este correo')
    retry = MailMessage(
        account_id=original.account_id, case_id=original.case_id, direction='Saliente', status='Pendiente',
        send_key=str(uuid.uuid4()), retry_of_id=original.id, request_hash=original.request_hash, sender=original.sender,
        to_addresses=original.to_addresses, cc_addresses=original.cc_addresses, subject=original.subject,
        body_text=original.body_text, attachment_ids=original.attachment_ids, thread_root=original.thread_root,
        message_id=make_msgid(domain=account.email.rsplit('@', 1)[-1]), actor_id=user.id, created_at=now(),
    )
    db.add(retry)
    db.flush()
    service.event(db, get_case(db, user, original.case_id), user, f'Reintento explícito del correo #{original.id}',
        kind='Sistema', channel='Sistema', data={'action': 'email_retry_created', 'email_message_id': retry.id, 'source_message_id': original.id})
    return _message_public(db, retry)


@router.post('/mail/outbox/{message_id}/retry-copy')
def retry_outbox_copy(message_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    original = db.get(MailMessage, message_id)
    if not original or original.direction != 'Saliente' or not original.case_id:
        raise HTTPException(404, 'Correo saliente no encontrado')
    case = get_case(db, user, original.case_id)
    _require_case_editor(db, user, case)
    try:
        status = retry_sent_copy(SessionLocal, message_id)
    except MailFailure as exc:
        if exc.code == 'account_disconnected':
            raise HTTPException(409, 'Conecta de nuevo la misma cuenta Yahoo antes de guardar la copia')
        raise HTTPException(409, 'La copia de este mensaje no está disponible para reintento')
    return {'id': message_id, 'status': 'Aceptado por Yahoo', 'sent_copy_status': status}


@router.get('/mail/pending')
def pending_mail(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    return unclassified_messages(db)


@router.get('/mail/overview')
def mail_overview(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    account = _current_account(db)
    if not account:
        return {'received': 0, 'unclassified': 0, 'ignored': 0, 'errors': 0, 'sync_pending': 0}
    incoming = MailMessage.direction == 'Entrante'
    unclassified = MailMessage.status.in_([
        'Pendiente de clasificación', 'Coincidencia ambigua', 'Referencia ambigua',
        'Participante no reconocido', 'Solicitud no disponible', 'Error de análisis',
    ])
    return {
        'received': db.scalar(select(func.count(MailMessage.id)).where(MailMessage.account_id == account.id, incoming, MailMessage.status == 'Vinculado')) or 0,
        'unclassified': db.scalar(select(func.count(MailMessage.id)).where(MailMessage.account_id == account.id, incoming, unclassified)) or 0,
        'ignored': db.scalar(select(func.count(MailMessage.id)).where(MailMessage.account_id == account.id, incoming, MailMessage.status == 'Ignorado')) or 0,
        'errors': db.scalar(select(func.count(MailSyncOutcome.id)).where(MailSyncOutcome.account_id == account.id,
            MailSyncOutcome.outcome.in_(['retry', 'review']))) or 0,
        'sync_pending': db.scalar(select(func.count(MailSyncOutcome.id)).where(MailSyncOutcome.account_id == account.id,
            MailSyncOutcome.outcome == 'retry')) or 0,
    }


@router.get('/mail/issues')
def mail_sync_issues(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    account = _current_account(db)
    if not account:
        return []
    rows = db.scalars(select(MailSyncOutcome).where(
        MailSyncOutcome.account_id == account.id, MailSyncOutcome.outcome.in_(['retry', 'review']),
    ).order_by(MailSyncOutcome.last_attempt_at.desc(), MailSyncOutcome.uid).limit(100)).all()
    return [{'uid': row.uid, 'uidvalidity': row.uidvalidity, 'state': row.outcome,
        'reason': row.reason, 'attempts': row.attempts,
        'last_attempt_at': row.last_attempt_at.isoformat() + 'Z'} for row in rows]


@router.post('/cases/{case_id}/email/attend')
def attend_email_messages(case_id: int, data: s.MailAttendInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    case = get_case(db, user, case_id, lock=True)
    _require_case_editor(db, user, case)
    ids = list(dict.fromkeys(data.message_ids))
    messages = db.scalars(select(MailMessage).where(
        MailMessage.id.in_(ids), MailMessage.case_id == case.id,
        MailMessage.direction == 'Entrante', MailMessage.status == 'Vinculado',
    ).with_for_update()).all()
    if len(messages) != len(ids):
        raise HTTPException(409, 'Uno o más correos ya no pertenecen a esta solicitud o no están vinculados')
    attended_at = now()
    newly_attended = []
    for message in messages:
        if message.attended_at is None:
            message.attended_at = attended_at
            message.attended_by_id = user.id
            newly_attended.append(message.id)
    if not newly_attended:
        return {'ok': True, 'attended_ids': ids, 'pending_message': case.pending_message, 'version': case.version}
    has_unattended = db.scalar(select(MailMessage.id).where(
        MailMessage.case_id == case.id, MailMessage.direction == 'Entrante',
        MailMessage.status == 'Vinculado', MailMessage.attended_at.is_(None),
    ).limit(1))
    case.pending_message = bool(has_unattended)
    case.version += 1
    service.event(db, case, user, f'{len(messages)} correo(s) entrante(s) marcado(s) como atendido(s)', kind='Sistema', channel='Correo', data={
        'action': 'email_attended', 'email_message_ids': newly_attended, 'attended_at': attended_at.isoformat() + 'Z',
    })
    if not case.pending_message:
        service.complete_resolved_production(db, case, user, 'email_message_attended')
    return {'ok': True, 'attended_ids': ids, 'pending_message': case.pending_message, 'version': case.version}


@router.post('/mail/pending/{message_id}/classify')
def classify_mail(message_id: int, data: s.MailClassifyInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    message = db.scalar(select(MailMessage).where(MailMessage.id == message_id, MailMessage.direction == 'Entrante').with_for_update())
    if not message or message.status in {'Vinculado', 'Ignorado'}:
        raise HTTPException(404, 'Correo pendiente de clasificación no encontrado')
    if data.action == 'ignore':
        message.status = 'Ignorado'
        message.actor_id = user.id
        _resolve_sync_outcome(db, message, 'ignored', 'manually_ignored')
        return {'ok': True, 'case_id': None, 'status': message.status}
    if data.action == 'link':
        if not data.case_id:
            raise HTTPException(422, 'Selecciona una solicitud')
        case = get_case(db, user, data.case_id, lock=True)
        _require_case_editor(db, user, case)
        if case.lifecycle == 'Completado':
            raise HTTPException(409, 'Para asociarlo a una solicitud completada, elige reactivarla o crear una relacionada')
    elif data.action == 'reactivate':
        if not data.case_id:
            raise HTTPException(422, 'Selecciona una solicitud completada')
        case = get_case(db, user, data.case_id, lock=True)
        _require_case_editor(db, user, case)
        if case.lifecycle != 'Completado':
            raise HTTPException(409, 'La solicitud seleccionada ya está activa')
        previous_completed_at = case.completed_at.isoformat() + 'Z' if case.completed_at else None
        case.lifecycle, case.completed_at, case.completed_by_id = 'Activo', None, None
        case.status, case.closed_at = 'Abierto', None
        service.event(db, case, user, 'Solicitud reactivada para clasificar una respuesta de correo', data={
            'action': 'case_reactivated', 'origin': 'incoming_email_classification', 'previous_completed_at': previous_completed_at,
        })
        case.version += 1
    elif data.action == 'related':
        if not data.case_id:
            raise HTTPException(422, 'Selecciona la solicitud relacionada')
        base = get_case(db, user, data.case_id)
        _require_case_editor(db, user, base)
        case = Case(client_id=base.client_id, related_case_id=base.id, subject=_mail_subject(message.subject),
            description='Solicitud creada al clasificar un correo relacionado.', kind='Seguimiento', channel='Correo',
            owner_id=None, status='Abierto', mode='Atención', pending_message=True, lifecycle='Activo')
        db.add(case)
        db.flush()
        service.event(db, case, None, f'Solicitud relacionada creada desde correo de la solicitud CG-{base.id:06d}', data={
            'action': 'created_from_email', 'related_case_id': base.id, 'email_message_id': message.id,
        })
    elif data.action == 'new':
        if not data.client_id:
            raise HTTPException(422, 'Selecciona el cliente asociado al correo')
        client = db.get(Client, data.client_id)
        if not client:
            raise HTTPException(404, 'Cliente no encontrado')
        case = Case(client_id=client.id, subject=_mail_subject(message.subject), description=message.body_text[:5000],
            kind='Consulta', channel='Correo', owner_id=None, status='Abierto', mode='Atención',
            pending_message=True, lifecycle='Activo')
        db.add(case)
        db.flush()
    else:
        raise HTTPException(422, 'Acción de clasificación no reconocida')

    _attach_incoming(message, case, user, db)
    _resolve_sync_outcome(db, message, 'imported', 'manually_classified')
    return {'ok': True, 'case_id': case.id, 'case_number': f'CG-{case.id:06d}', 'status': message.status}


def _mail_subject(subject):
    return ((subject or '').strip() or 'Consulta recibida por correo')[:240]


def _attach_incoming(message, case, user, db):
    body = f"De: {message.sender}\nAsunto: {message.subject or '(sin asunto)'}\n\n{message.body_text or '(El mensaje no contiene texto legible.)'}"[:25000]
    service.attach_incoming_mail(db, case, message, body, user, reopen_origin='email_classification')


@router.get('/mail/attachments/{attachment_id}')
def download_mail_attachment(attachment_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    from pathlib import Path
    from fastapi.responses import FileResponse
    from ..config import settings
    attachment = db.get(MailAttachment, attachment_id)
    if not attachment:
        raise HTTPException(404, 'Adjunto de correo no encontrado')
    message = db.get(MailMessage, attachment.message_id)
    if not message or not message.case_id:
        raise HTTPException(404, 'El correo debe clasificarse antes de abrir sus adjuntos')
    get_case(db, user, message.case_id)
    path = Path(settings.upload_dir) / attachment.storage_key
    if not path.is_file():
        raise HTTPException(404, 'Adjunto no disponible; revisa el respaldo de archivos')
    return FileResponse(path, media_type=attachment.mime, filename=attachment.filename, headers={'X-Content-Type-Options': 'nosniff'})
