"""Yahoo IMAP/SMTP clients. Protocol details and network failures stay here."""
import imaplib
import re
import smtplib
import socket
import ssl
import time
import logging
from datetime import datetime, timezone

from .config import settings

IMAP_HOST = 'imap.mail.yahoo.com'
IMAP_PORT = 993
SMTP_HOST = 'smtp.mail.yahoo.com'
SMTP_PORT = 465
SENDER_NAME = 'Comgraf | Comunicación Gráfica'
ALLOWED_MIME = {'application/pdf', 'image/png', 'image/jpeg', 'text/plain'}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
logger = logging.getLogger(__name__)


class MailFailure(Exception):
    def __init__(self, code, *, operation=None, cause=None):
        self.code = code
        self.operation = operation
        self.cause_type = type(cause).__name__ if cause else None
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
    raise MailFailure('inbox_metadata_error', operation='uidvalidity')


def _imap_checkpoint(email_address, password):
    connection = None
    operation = 'connect'
    try:
        connection = open_imap()
        operation = 'authentication'
        try:
            connection.login(email_address, password)
        except imaplib.IMAP4.error as exc:
            raise MailFailure('authentication_error', operation=operation, cause=exc) from exc
        operation = 'inbox_select'
        result, _ = connection.select('INBOX', readonly=True)
        if result != 'OK':
            raise MailFailure('inbox_select_error', operation=operation)
        operation = 'uidvalidity'
        validity = _uidvalidity(connection)
        operation = 'inbox_checkpoint'
        status, data = connection.response('UIDNEXT')
        values = data or []
        try:
            next_uid = int(values[0].decode() if isinstance(values[0], bytes) else values[0])
        except (IndexError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise MailFailure('inbox_metadata_error', operation=operation, cause=exc) from exc
        if (status or '').upper() != 'UIDNEXT' or next_uid < 1:
            raise MailFailure('inbox_metadata_error', operation=operation)
        # UIDNEXT is the next identifier Yahoo will assign, so its predecessor
        # is a cheap activation checkpoint even for a large mailbox.
        checked_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        return {'uidvalidity': validity, 'last_uid': next_uid - 1, 'checked_at': checked_at}
    except MailFailure:
        raise
    except (imaplib.IMAP4.error, OSError, socket.timeout, TimeoutError, ssl.SSLError) as exc:
        raise _imap_failure(exc, operation) from exc
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
        return {'ok': False, 'status': exc.code, 'operation': exc.operation,
                'cause_type': exc.cause_type, 'checkpoint': None}


def check_smtp(email_address, password):
    connection = None
    phase = 'connect'
    try:
        connection = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=settings.mail_connect_timeout_seconds, context=ssl.create_default_context())
        phase = 'login'
        try:
            connection.login(email_address, password)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailFailure('authentication_error', operation=phase, cause=exc) from exc
        return {'ok': True, 'status': 'connected'}
    except MailFailure as exc:
        return {'ok': False, 'status': exc.code, 'operation': exc.operation, 'cause_type': exc.cause_type}
    except (smtplib.SMTPException, OSError, socket.timeout, TimeoutError, ssl.SSLError) as exc:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return {'ok': False, 'status': 'authentication_error', 'operation': phase, 'cause_type': type(exc).__name__}
        return {'ok': False, 'status': _network_error_code(exc), 'operation': phase, 'cause_type': type(exc).__name__}
    finally:
        if connection is not None:
            try:
                # QUIT waits for another server round trip after the credential check.
                connection.close()
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
        'dns_error': 'Error de DNS',
        'timeout_error': 'Tiempo de conexión agotado',
        'tls_error': 'Error de TLS/certificado',
        'inbox_select_error': 'Error al abrir INBOX',
        'inbox_query_error': 'Error al consultar INBOX',
        'inbox_metadata_error': 'Error al leer metadatos de INBOX',
        'message_fetch_error': 'Error al descargar mensaje',
        'message_processing_error': 'Error al procesar mensaje',
        'internaldate_unavailable': 'Mensaje pendiente: fecha IMAP no interpretable',
        'message_content_missing': 'Mensaje pendiente de volver a descargar',
        'message_metadata_missing': 'Metadatos del mensaje pendientes',
        'message_size_unavailable': 'Tamaño del mensaje pendiente de consultar',
        'sent_copy_error': 'Error al guardar copia en Enviados',
        'worker_internal_error': 'Error interno del trabajador',
        'persistence_error': 'Error al guardar correo',
        'worker_stale': 'Sin actividad reciente del sincronizador',
        'starting': 'Esperando primera consulta',
        'not_checked': 'Sin comprobar',
        'disabled': 'Desconectado',
        'uncertain': 'Resultado incierto',
    }.get(code, 'Error de conexión' if code else 'Sin comprobar')


def _network_error_code(exc):
    if isinstance(exc, socket.gaierror):
        return 'dns_error'
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return 'timeout_error'
    if isinstance(exc, ssl.SSLError):
        return 'tls_error'
    return 'connection_error'


def _imap_failure(exc, operation):
    code = _network_error_code(exc) if isinstance(exc, (OSError, socket.timeout, TimeoutError, ssl.SSLError)) else {
        'inbox_select': 'inbox_select_error',
        'uidvalidity': 'inbox_metadata_error',
        'inbox_checkpoint': 'inbox_query_error',
    }.get(operation, 'connection_error')
    return MailFailure(code, operation=operation, cause=exc)


def smtp_send(email_address, password, recipients, payload):
    connection = None
    phase = 'connect'
    try:
        connection = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=settings.mail_connect_timeout_seconds, context=ssl.create_default_context())
        phase = 'login'
        try:
            connection.login(email_address, password)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailFailure('authentication_error', operation=phase, cause=exc) from exc
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
        raise MailFailure('authentication_error', operation=phase, cause=exc) from exc
    except smtplib.SMTPDataError as exc:
        raise MailFailure('smtp_rejected', operation=phase, cause=exc) from exc
    except (smtplib.SMTPException, OSError, socket.timeout, TimeoutError) as exc:
        if phase == 'data':
            raise MailFailure('uncertain', operation=phase, cause=exc) from exc
        raise MailFailure(_network_error_code(exc), operation=phase, cause=exc) from exc
    finally:
        if connection is not None:
            try:
                # DATA's 2xx reply is Yahoo's acceptance signal. Do not wait for QUIT
                # before the caller can persist that result.
                connection.close()
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
    except Exception as exc:
        logger.warning('Copia de Enviados fallida operation=append cause=%s', type(exc).__name__)
        return 'Error'
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass
