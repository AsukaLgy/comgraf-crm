"""Yahoo IMAP/SMTP clients. Protocol details and network failures stay here."""
import imaplib
import re
import smtplib
import socket
import ssl
import time
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import settings

IMAP_HOST = 'imap.mail.yahoo.com'
IMAP_PORT = 993
SMTP_HOST = 'smtp.mail.yahoo.com'
SMTP_PORT = 465
SENDER_NAME = 'Comgraf | Comunicación Gráfica'
ALLOWED_MIME = {'application/pdf', 'image/png', 'image/jpeg', 'text/plain'}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


class MailFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def open_imap():
    return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=settings.mail_connect_timeout_seconds)


def _uidvalidity(connection):
    try:
        value = connection.response('UIDVALIDITY')[1]
        if value and value[0]:
            return str(value[0].decode() if isinstance(value[0], bytes) else value[0])
    except Exception:
        pass
    raise MailFailure('connection_error')


def _imap_checkpoint(email_address, password):
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
        result, data = connection.uid('search', None, 'ALL')
        if result != 'OK':
            raise MailFailure('connection_error')
        uids = [int(value) for value in (data[0] or b'').split() if value.isdigit()]
        return {'uidvalidity': validity, 'last_uid': max(uids, default=0)}
    except MailFailure:
        raise
    except (imaplib.IMAP4.error, OSError, socket.timeout, TimeoutError) as exc:
        raise MailFailure('connection_error') from exc
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass


def check_imap(email_address, password):
    try:
        checkpoint = _imap_checkpoint(email_address, password)
        return {'ok': True, 'status': 'connected', 'checkpoint': checkpoint}
    except MailFailure as exc:
        return {'ok': False, 'status': exc.code, 'checkpoint': None}


def check_smtp(email_address, password):
    connection = None
    try:
        connection = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=settings.mail_connect_timeout_seconds, context=ssl.create_default_context())
        try:
            connection.login(email_address, password)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailFailure('authentication_error') from exc
        return {'ok': True, 'status': 'connected'}
    except MailFailure as exc:
        return {'ok': False, 'status': exc.code}
    except (smtplib.SMTPException, OSError, socket.timeout, TimeoutError) as exc:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return {'ok': False, 'status': 'authentication_error'}
        return {'ok': False, 'status': 'connection_error'}
    finally:
        if connection is not None:
            try:
                connection.quit()
            except Exception:
                pass


def check_credentials(email_address, password):
    # A valid IMAP login does not imply SMTP works; verify each protocol independently.
    return {'imap': check_imap(email_address, password), 'smtp': check_smtp(email_address, password)}


def status_label(code):
    return {
        'connected': 'Conectado',
        'authentication_error': 'Error de autenticación',
        'connection_error': 'Error de conexión',
        'not_checked': 'Sin comprobar',
        'disabled': 'Desconectado',
        'uncertain': 'Resultado incierto',
    }.get(code, 'Error de conexión' if code else 'Sin comprobar')


def send_test_mail(email_address, password):
    message = EmailMessage(policy=policy.SMTP)
    message['From'] = formataddr((SENDER_NAME, email_address))
    message['To'] = email_address
    message['Reply-To'] = email_address
    message['Subject'] = 'Prueba de conexión · Comgraf'
    message['Date'] = datetime.now(timezone.utc)
    message['Message-ID'] = make_msgid(domain=email_address.rsplit('@', 1)[-1])
    message.set_content('Este mensaje confirma que Comgraf pudo enviar correo mediante Yahoo SMTP.\n\nLa aceptación SMTP no confirma entrega ni lectura.')
    payload = message.as_bytes(policy=policy.SMTP)
    smtp_send(email_address, password, [email_address], payload)
    return payload


def smtp_send(email_address, password, recipients, payload):
    connection = None
    phase = 'connect'
    try:
        connection = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=settings.mail_connect_timeout_seconds, context=ssl.create_default_context())
        phase = 'login'
        try:
            connection.login(email_address, password)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailFailure('authentication_error') from exc
        phase = 'mail'
        code, _ = connection.mail(email_address)
        if code < 200 or code >= 300:
            raise MailFailure('smtp_rejected')
        for recipient in recipients:
            phase = 'recipient'
            code, _ = connection.rcpt(recipient)
            if code < 200 or code >= 300:
                raise MailFailure('recipient_rejected')
        phase = 'data'
        code, _ = connection.data(payload)
        if code < 200 or code >= 300:
            raise MailFailure('smtp_rejected')
        return True
    except MailFailure:
        raise
    except smtplib.SMTPAuthenticationError as exc:
        raise MailFailure('authentication_error') from exc
    except smtplib.SMTPDataError as exc:
        raise MailFailure('smtp_rejected') from exc
    except (smtplib.SMTPException, OSError, socket.timeout, TimeoutError) as exc:
        if phase == 'data':
            raise MailFailure('uncertain') from exc
        raise MailFailure('connection_error') from exc
    finally:
        if connection is not None:
            try:
                connection.quit()
            except Exception:
                pass


def _sent_folder(connection):
    result, rows = connection.list()
    if result != 'OK':
        return None
    for row in rows or []:
        line = row if isinstance(row, bytes) else str(row).encode()
        if b'\\sent' in line.lower():
            match = re.search(rb'"([^\"]+)"\s*$', line)
            if match:
                return match.group(1).decode('utf-8', errors='replace')
            return line.rsplit(b' ', 1)[-1].strip(b'"').decode('utf-8', errors='replace')
    for row in rows or []:
        line = row if isinstance(row, bytes) else str(row).encode()
        if re.search(rb'\b(sent|sent messages)\b', line, re.IGNORECASE):
            match = re.search(rb'"([^\"]+)"\s*$', line)
            if match:
                return match.group(1).decode('utf-8', errors='replace')
    return None


def append_sent_copy(email_address, password, message_id, payload):
    connection = None
    try:
        connection = open_imap()
        connection.login(email_address, password)
        folder = _sent_folder(connection)
        if not folder:
            return 'No disponible'
        result, _ = connection.select(folder)
        if result != 'OK':
            return 'Error'
        result, data = connection.uid('search', None, 'HEADER', 'Message-ID', message_id)
        if result != 'OK':
            return 'Error'
        if data and data[0]:
            return 'Guardada'
        result, _ = connection.append(folder, '\\Seen', imaplib.Time2Internaldate(time.time()), payload)
        return 'Guardada' if result == 'OK' else 'Error'
    except Exception:
        return 'Error'
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass
