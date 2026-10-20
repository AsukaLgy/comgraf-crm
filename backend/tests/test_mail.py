"""Yahoo integration tests use protocol fakes and the disposable MySQL test schema."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email import policy

from sqlalchemy import func, select, text

from app.db import SessionLocal, engine
from app.mail_security import decrypt_password
from app.mail_service import MailFailure, _case_number_target, parse_raw_message, process_outbox_message, sync_account
from app.models import Case, Client, Event, MailAccount, MailMessage, MailSyncCursor
from app.routers import mail as mail_router
from app import mail_service
from app import mail_transport


def successful_connections(uidvalidity='100', last_uid=50):
    return {
        'imap': {'ok': True, 'status': 'connected', 'checkpoint': {'uidvalidity': uidvalidity, 'last_uid': last_uid}},
        'smtp': {'ok': True, 'status': 'connected'},
    }


def raw_email(sender, recipient, message_id, subject, body, *, reply=None, references=None, html_body=None):
    message = EmailMessage(policy=policy.SMTP)
    message['From'] = sender
    message['To'] = recipient
    message['Message-ID'] = message_id
    message['Subject'] = subject
    message['Date'] = datetime(2026, 10, 19, 12, 0, 0)
    if reply:
        message['In-Reply-To'] = reply
    if references:
        message['References'] = ' '.join(references)
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype='html')
    return message.as_bytes(policy=policy.SMTP)


def test_admin_config_is_encrypted_and_protocol_failures_are_separate(clients, monkeypatch):
    api = clients['admin']
    monkeypatch.setattr(mail_router, 'check_credentials', lambda *_: successful_connections())
    response = api.put('/api/mail/config', json={
        'email': 'comgraf@yahoo.test', 'application_password': 'wxyz abcd efgh ijkl',
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['saved'] is True
    assert result['connection']['imap']['ok'] and result['connection']['smtp']['ok']
    assert 'application_password' not in response.text and 'wxyz' not in response.text
    with SessionLocal() as db:
        account = db.scalar(select(MailAccount).where(MailAccount.active.is_(True)))
        encrypted = account.encrypted_password
        assert encrypted != 'wxyzabcdefghijkl'
        assert decrypt_password(encrypted) == 'wxyzabcdefghijkl'
    assert api.get('/api/mail/config').json()['has_password'] is True
    assert clients['coordinator'].get('/api/mail/config').status_code == 403
    assert clients['coordinator'].put('/api/mail/config', json={'email': 'bad@yahoo.test', 'application_password': 'wrong'}).status_code == 403
    assert clients['coordinator'].delete('/api/mail/config').status_code == 403
    invalid_secret = 'do-not-leak-' + ('x' * 200)
    invalid = api.put('/api/mail/config', json={'email': 'comgraf@yahoo.test', 'application_password': invalid_secret})
    assert invalid.status_code == 422 and invalid_secret not in invalid.text

    failure = {
        'imap': {'ok': True, 'status': 'connected', 'checkpoint': {'uidvalidity': '100', 'last_uid': 90}},
        'smtp': {'ok': False, 'status': 'authentication_error'},
    }
    monkeypatch.setattr(mail_router, 'check_credentials', lambda *_: failure)
    rejected = api.put('/api/mail/config', json={'email': 'comgraf@yahoo.test', 'application_password': 'wrong-secret'})
    assert rejected.status_code == 200
    assert rejected.json()['saved'] is False
    assert rejected.json()['connection']['imap']['status'] == 'connected'
    assert rejected.json()['connection']['smtp']['status'] == 'authentication_error'
    assert 'wrong-secret' not in rejected.text
    with SessionLocal() as db:
        account = db.scalar(select(MailAccount).where(MailAccount.active.is_(True)))
        assert account.encrypted_password == encrypted


def test_real_outbox_persists_idempotently_without_sending_in_request(clients, monkeypatch):
    api = clients['admin']
    monkeypatch.setattr(mail_router, 'check_credentials', lambda *_: successful_connections())
    configured = api.put('/api/mail/config', json={'email': 'comgraf@yahoo.test', 'application_password': 'test-app-password'})
    assert configured.status_code == 200 and configured.json()['saved']
    client = api.post('/api/clients', json={'name': 'María', 'emails': ['maria@example.test']}).json()
    case = api.post('/api/cases', json={'client_id': client['id'], 'subject': 'Revisión del diseño'}).json()
    payload = {
        'to': ['maria@example.test'], 'subject': 'Arte listo', 'body': 'Te adjunto el arte aprobado.',
        'attachment_ids': [], 'thread_root': None, 'send_key': 'email-send-key-001',
    }
    queued = api.post(f"/api/cases/{case['id']}/email/send", json=payload)
    assert queued.status_code == 202, queued.text
    row = queued.json()
    assert row['status'] == 'Pendiente'
    assert row['to'] == ['maria@example.test']
    assert row['subject'].startswith(f"[Solicitud {case['number']}]")
    assert 'encrypted_password' not in queued.text and 'test-app-password' not in queued.text
    repeated = api.post(f"/api/cases/{case['id']}/email/send", json=payload)
    assert repeated.status_code == 202 and repeated.json()['id'] == row['id']
    changed = {**payload, 'body': 'Contenido distinto'}
    assert api.post(f"/api/cases/{case['id']}/email/send", json=changed).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count(MailMessage.id)).where(MailMessage.direction == 'Saliente')) == 1
        assert db.get(MailMessage, row['id']).status == 'Pendiente'
    assert api.delete('/api/mail/config').status_code == 200
    with SessionLocal() as db:
        assert db.get(MailMessage, row['id']).status == 'Pausado'
        account = db.get(MailAccount, db.get(MailMessage, row['id']).account_id)
        assert not account.active and account.encrypted_password is None


def test_configuration_test_sends_a_message_and_reports_sent_copy(clients, monkeypatch):
    api = clients['admin']
    monkeypatch.setattr(mail_router, 'check_credentials', lambda *_: successful_connections())
    configured = api.put('/api/mail/config', json={'email': 'comgraf@yahoo.test', 'application_password': 'test-app-password'})
    assert configured.status_code == 200 and configured.json()['saved']
    sent = []
    monkeypatch.setattr(mail_transport, 'smtp_send', lambda address, password, recipients, payload: sent.append((address, recipients, payload)) or True)
    monkeypatch.setattr(mail_router, 'append_sent_copy', lambda address, password, message_id, payload: 'Guardada')
    result = api.post('/api/mail/test', json={})
    assert result.status_code == 200 and result.json()['sent'] is True
    assert result.json()['sent_copy_status'] == 'Guardada'
    assert len(sent) == 1 and sent[0][0] == 'comgraf@yahoo.test'
    assert sent[0][1] == ['comgraf@yahoo.test']
    assert b'pudo enviar correo mediante Yahoo SMTP' in sent[0][2]


def test_switching_accounts_keeps_separate_progress_and_pauses_old_outbox(clients, monkeypatch):
    api = clients['admin']
    def check(email_address, _password):
        return successful_connections('501', 100) if email_address == 'primera@yahoo.test' else successful_connections('12', 7)
    monkeypatch.setattr(mail_router, 'check_credentials', check)
    first = api.put('/api/mail/config', json={'email': 'primera@yahoo.test', 'application_password': 'first-app-password'})
    assert first.status_code == 200 and first.json()['saved']
    with SessionLocal() as db:
        old = db.scalar(select(MailAccount).where(MailAccount.email == 'primera@yahoo.test'))
        old_id = old.id
    client = api.post('/api/clients', json={'name': 'Luz'}).json()
    case = api.post('/api/cases', json={'client_id': client['id'], 'subject': 'Cambio de cuenta'}).json()
    pending = api.post(f"/api/cases/{case['id']}/email/send", json={
        'to': ['luz@example.test'], 'subject': 'Información', 'body': 'Mensaje pendiente.',
        'attachment_ids': [], 'thread_root': None, 'send_key': 'switch-account-send-key',
    })
    assert pending.status_code == 202
    second = api.put('/api/mail/config', json={'email': 'segunda@yahoo.test', 'application_password': 'second-app-password'})
    assert second.status_code == 200 and second.json()['saved']
    with SessionLocal() as db:
        old = db.get(MailAccount, old_id)
        new = db.scalar(select(MailAccount).where(MailAccount.active.is_(True)))
        old_cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == old_id))
        new_cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == new.id))
        stored = db.get(MailMessage, pending.json()['id'])
        assert old.email == 'primera@yahoo.test' and not old.active and old.encrypted_password is None
        assert old_cursor.uidvalidity == '501' and old_cursor.last_uid == 100
        assert new.email == 'segunda@yahoo.test' and new.active
        assert new_cursor.uidvalidity == '12' and new_cursor.last_uid == 7
        assert stored.account_id == old_id and stored.status == 'Pausado'


def test_outbox_acceptance_and_uncertain_result_are_not_retried_automatically(clients, monkeypatch):
    api = clients['admin']
    monkeypatch.setattr(mail_router, 'check_credentials', lambda *_: successful_connections())
    configured = api.put('/api/mail/config', json={'email': 'comgraf@yahoo.test', 'application_password': 'test-app-password'})
    assert configured.status_code == 200 and configured.json()['saved']
    client = api.post('/api/clients', json={'name': 'Rosa', 'emails': ['rosa@example.test']}).json()
    case = api.post('/api/cases', json={'client_id': client['id'], 'subject': 'Prueba de salida'}).json()
    payload = {
        'to': ['rosa@example.test'], 'subject': 'Cotización', 'body': 'Adjunto la cotización solicitada.',
        'attachment_ids': [], 'thread_root': None, 'send_key': 'email-send-key-accepted-01',
    }
    accepted = api.post(f"/api/cases/{case['id']}/email/send", json=payload).json()
    calls = []
    monkeypatch.setattr(mail_service, 'smtp_send', lambda *args: calls.append(args) or True)
    monkeypatch.setattr(mail_service, 'append_sent_copy', lambda *_: 'Guardada')
    process_outbox_message(SessionLocal, accepted['id'])
    with SessionLocal() as db:
        stored = db.get(MailMessage, accepted['id'])
        assert stored.status == 'Aceptado por Yahoo'
        assert stored.sent_copy_status == 'Guardada'
        assert db.query(Event).filter(Event.case_id == case['id'], Event.kind == 'Correo saliente').count() == 1
        assert stored.thread_root == stored.message_id
    assert len(calls) == 1

    payload['send_key'] = 'email-send-key-uncertain-01'
    uncertain = api.post(f"/api/cases/{case['id']}/email/send", json=payload).json()
    attempts = []
    def uncertain_send(*_):
        attempts.append(True)
        raise MailFailure('uncertain')
    monkeypatch.setattr(mail_service, 'smtp_send', uncertain_send)
    process_outbox_message(SessionLocal, uncertain['id'])
    process_outbox_message(SessionLocal, uncertain['id'])
    assert len(attempts) == 1
    with SessionLocal() as db:
        assert db.get(MailMessage, uncertain['id']).status == 'Resultado incierto'
    assert api.post(f"/api/mail/outbox/{uncertain['id']}/retry", json={'confirm_uncertain': False}).status_code == 409
    confirmed = api.post(f"/api/mail/outbox/{uncertain['id']}/retry", json={'confirm_uncertain': True})
    assert confirmed.status_code == 202 and confirmed.json()['status'] == 'Pendiente'
    repeated = api.post(f"/api/mail/outbox/{uncertain['id']}/retry", json={'confirm_uncertain': True})
    assert repeated.status_code == 202 and repeated.json()['id'] == confirmed.json()['id']


def test_sync_uses_uid_checkpoint_deduplicates_and_reopens_valid_thread(clients, monkeypatch):
    api = clients['admin']
    monkeypatch.setattr(mail_router, 'check_credentials', lambda *_: successful_connections('777', 50))
    configured = api.put('/api/mail/config', json={'email': 'comgraf@yahoo.test', 'application_password': 'test-app-password'})
    assert configured.status_code == 200
    with SessionLocal() as db:
        account_id = db.scalar(select(MailAccount.id).where(MailAccount.active.is_(True)))

    customer = Client(name='Cliente conocido', emails=['cliente@example.test'], search_text='clientecocido')
    with SessionLocal.begin() as db:
        db.add(customer)
        db.flush()
        client_id = customer.id

    first = raw_email('Cliente <cliente@example.test>', 'comgraf@yahoo.test', '<in-1@example.test>', 'Consulta de impresión', 'Necesito 500 volantes.')
    historical = raw_email('Cliente <cliente@example.test>', 'comgraf@yahoo.test', '<old@example.test>', 'Correo antiguo', 'No importar este mensaje.')
    reply = raw_email('Cliente <cliente@example.test>', 'comgraf@yahoo.test', '<in-2@example.test>', 'Re: Consulta', 'Confirmo la fecha.', reply='<out-1@example.test>', references=['<in-1@example.test>', '<out-1@example.test>'])

    class FakeIMAP:
        validity = '777'
        messages = {40: historical, 51: first}
        sizes = {}
        body_fetches = set()
        internal_dates = {
            40: datetime(2020, 1, 1, tzinfo=timezone.utc),
            51: datetime.now(timezone.utc) + timedelta(days=1),
            52: datetime.now(timezone.utc) + timedelta(days=1, minutes=1),
        }
        def __init__(self, *args, **kwargs): pass
        def login(self, *_): return 'OK', []
        def select(self, *_args, **_kwargs): return 'OK', [b'2']
        def response(self, _): return 'UIDVALIDITY', [self.validity.encode()]
        def uid(self, command, *args):
            if command == 'search':
                start = int(args[-1].split(':')[0])
                found = [uid for uid in sorted(self.messages) if uid >= start]
                return 'OK', [' '.join(map(str, found)).encode()]
            if command == 'fetch':
                uid = int(args[0])
                value = self.messages[uid]
                date_value = self.internal_dates.get(uid)
                internaldate = date_value.strftime('%d-%b-%Y %H:%M:%S %z') if date_value else None
                if 'RFC822.SIZE' in args[1]:
                    date_field = f' INTERNALDATE "{internaldate}"' if internaldate else ''
                    metadata = f'1 (RFC822.SIZE {self.sizes.get(uid, len(value))}{date_field})'.encode()
                    return 'OK', [(metadata, b'')]
                if 'HEADER.FIELDS' in args[1]:
                    headers = value.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
                    return 'OK', [(b'1 (BODY[HEADER.FIELDS] {128})', headers), b')']
                self.body_fetches.add(uid)
                date_field = f' INTERNALDATE "{internaldate}"' if internaldate else ''
                metadata = f'1 ({date_field} BODY[] {{123}})'.encode()
                return 'OK', [(metadata, value), b')']
            raise AssertionError(command)
        def logout(self): return 'BYE', []

    monkeypatch.setattr(mail_service, 'open_imap', lambda: FakeIMAP())
    assert sync_account(SessionLocal, account_id) is True
    owner_id = clients['admin'].get('/api/auth/me').json()['id']
    with SessionLocal() as db:
        case = db.scalar(select(Case).where(Case.client_id == client_id))
        assert case is not None and case.owner_id is None and case.pending_message
        assert case.status == 'Abierto'
        assert db.scalar(select(func.count(MailMessage.id)).where(MailMessage.account_id == account_id)) == 1
        assert db.scalar(select(func.count(MailMessage.id)).where(MailMessage.account_id == account_id, MailMessage.message_id == '<old@example.test>')) == 0
        linked_in = db.scalar(select(MailMessage).where(MailMessage.message_id == '<in-1@example.test>'))
        linked_case_id = case.id
        linked_in_id = linked_in.id
    with SessionLocal.begin() as db:
        case = db.get(Case, linked_case_id)
        case.owner_id = owner_id
        case.status = 'Cerrado'
        case.pending_message = False
        db.add(MailMessage(
            account_id=account_id, case_id=case.id, direction='Saliente', status='Aceptado por Yahoo',
            sender='comgraf@yahoo.test', to_addresses=['cliente@example.test'], cc_addresses=[],
            subject='Re: Consulta', body_text='Gracias', attachment_ids=[], message_id='<out-1@example.test>',
            thread_root='<in-1@example.test>', references_json=['<in-1@example.test>'], created_at=datetime(2026, 10, 19, 12, 5),
        ))
    FakeIMAP.messages[52] = reply
    assert sync_account(SessionLocal, account_id) is True
    with SessionLocal() as db:
        case = db.get(Case, linked_case_id)
        assert case.status == 'Abierto' and case.pending_message
        assert case.owner_id == owner_id
        assert db.scalar(select(func.count(Case.id)).where(Case.client_id == client_id)) == 1
        assert db.scalar(select(func.count(MailMessage.id)).where(MailMessage.message_id == '<in-2@example.test>')) == 1
        assert db.get(MailMessage, linked_in_id).status == 'Vinculado'
    oversized = raw_email('Cliente <cliente@example.test>', 'comgraf@yahoo.test', '<large@example.test>', 'Archivos pesados', 'contenido omitido')
    FakeIMAP.messages[53] = oversized
    FakeIMAP.sizes[53] = mail_service.MAX_MESSAGE_BYTES + 1
    FakeIMAP.internal_dates[53] = datetime.now(timezone.utc) + timedelta(days=2)
    missing_date = raw_email('Cliente <cliente@example.test>', 'comgraf@yahoo.test', '<missing-date@example.test>', 'Fecha no disponible', 'No importar sin fecha del servidor.')
    FakeIMAP.messages[54] = missing_date
    assert sync_account(SessionLocal, account_id) is True
    assert 53 not in FakeIMAP.body_fetches
    with SessionLocal() as db:
        large = db.scalar(select(MailMessage).where(MailMessage.message_id == '<large@example.test>'))
        assert large.status == 'Error de análisis' and 'supera el límite' in large.body_text
    # A new UIDVALIDITY causes a rescan; Message-ID/content hashes prevent duplicate cases or events.
    FakeIMAP.validity = '778'
    assert sync_account(SessionLocal, account_id) is True
    assert 40 not in FakeIMAP.body_fetches and 54 not in FakeIMAP.body_fetches
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Case.id)).where(Case.client_id == client_id)) == 1
        assert db.scalar(select(func.count(MailMessage.id)).where(MailMessage.message_id == '<in-2@example.test>')) == 1
        assert db.scalar(select(func.count(MailMessage.id)).where(MailMessage.message_id == '<missing-date@example.test>')) == 0


def test_html_is_rendered_as_safe_plain_text_and_auto_mail_is_detectable():
    message = EmailMessage(policy=policy.SMTP)
    message['From'] = 'Cliente <cliente@example.test>'
    message['To'] = 'comgraf@yahoo.test'
    message['Message-ID'] = '<html-1@example.test>'
    message['Subject'] = 'Hola'
    message.set_content('<p>Texto seguro</p><script>robar()</script><img src="https://tracking.invalid/pixel">', subtype='html')
    parsed = parse_raw_message(message.as_bytes(policy=policy.SMTP))
    assert 'Texto seguro' in parsed['body']
    assert 'robar' not in parsed['body']
    assert 'tracking.invalid' not in parsed['body']
    assert '<script>' not in parsed['body']


def test_mysql_advisory_lock_prevents_two_sync_workers(clients):
    first, second = engine.connect(), engine.connect()
    lock_name = 'comgraf-yahoo-mail-test'
    try:
        assert first.execute(text('SELECT GET_LOCK(:name, 0)'), {'name': lock_name}).scalar_one() == 1
        assert second.execute(text('SELECT GET_LOCK(:name, 0)'), {'name': lock_name}).scalar_one() == 0
    finally:
        first.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': lock_name})
        first.close()
        second.close()


def test_case_number_is_only_a_thread_hint_when_sender_matches_client(clients):
    api = clients['admin']
    client = api.post('/api/clients', json={'name': 'Vértice', 'emails': ['persona@example.test']}).json()
    first = api.post('/api/cases', json={'client_id': client['id'], 'subject': 'Primer trabajo'}).json()
    second = api.post('/api/cases', json={'client_id': client['id'], 'subject': 'Segundo trabajo'}).json()
    with SessionLocal() as db:
        matching, problem = _case_number_target(db, {
            'subject': f"Re: [Solicitud {first['number']}], actualización", 'sender': 'persona@example.test',
        })
        assert problem is None and matching.id == first['id'] and matching.id != second['id']
        suspicious, issue = _case_number_target(db, {
            'subject': f"Re: [Solicitud {first['number']}], actualización", 'sender': 'otra@example.test',
        })
        assert suspicious is None and issue == 'Referencia ambigua'
