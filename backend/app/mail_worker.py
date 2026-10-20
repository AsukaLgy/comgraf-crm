"""Independent, MySQL-coordinated Yahoo receive, send and Sent-copy workers."""
import logging
import threading
import time
from sqlalchemy import or_, select, text

from .config import settings
from .db import SessionLocal, engine
from .mail_service import process_outbox_message, process_sent_copy, recover_stuck_outbox, sync_account
from .models import MailAccount, MailMessage, MailWorkerTask, now

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger(__name__)
LOCKS = {'inbox': 'comgraf-mail-inbox', 'outbox': 'comgraf-mail-outbox', 'sent_copy': 'comgraf-mail-copy'}


def _active_account_id():
    with SessionLocal() as db:
        return db.scalar(select(MailAccount.id).where(MailAccount.active.is_(True)).order_by(MailAccount.id.desc()).limit(1))


def _task_health(task_name, account_id, *, success=None, error=None):
    with SessionLocal.begin() as db:
        row = db.get(MailWorkerTask, task_name, with_for_update=True)
        if row is None:
            row = MailWorkerTask(task_name=task_name)
            db.add(row)
        row.account_id = account_id
        row.heartbeat_at = now()
        row.last_attempt_at = now()
        if success:
            row.last_success_at = row.heartbeat_at
            row.last_error = None
        elif error:
            row.last_error = error[:40]


def _outbox_once(account_id):
    recover_stuck_outbox(SessionLocal)
    with SessionLocal() as db:
        message_id = db.scalar(select(MailMessage.id).where(
            MailMessage.account_id == account_id,
            MailMessage.direction == 'Saliente',
            MailMessage.status == 'Pendiente',
            or_(MailMessage.send_next_attempt_at.is_(None), MailMessage.send_next_attempt_at <= now()),
        ).order_by(MailMessage.created_at, MailMessage.id).limit(1))
    if message_id is None:
        return True, None
    process_outbox_message(SessionLocal, message_id)
    with SessionLocal() as db:
        row = db.get(MailMessage, message_id)
        error = row.error_code if row and row.status in {'Error', 'Resultado incierto'} else None
    return True, error


def _inbox_once(account_id):
    healthy = sync_account(SessionLocal, account_id)
    if healthy:
        return True, None
    with SessionLocal() as db:
        account = db.get(MailAccount, account_id)
        if not account:
            return False, 'account_disconnected'
        if account.imap_status not in {'connected', 'not_checked', 'disabled'}:
            return False, account.imap_status
        if account.last_error in {'persistence_error', 'message_processing_error', 'inbox_metadata_error', 'inbox_query_error', 'inbox_select_error', 'message_fetch_error'}:
            return False, account.last_error
        # A false sync result with a healthy IMAP protocol status is an
        # application-side failure, not evidence that Yahoo rejected IMAP.
        return False, 'worker_internal_error'


def _copy_once(account_id):
    with SessionLocal() as db:
        message_id = db.scalar(select(MailMessage.id).where(
            MailMessage.account_id == account_id,
            MailMessage.direction == 'Saliente',
            MailMessage.status == 'Aceptado por Yahoo',
            or_(
                MailMessage.sent_copy_status == 'Pendiente',
                (MailMessage.sent_copy_status == 'Error') & (MailMessage.copy_attempts < 6)
                & or_(MailMessage.copy_next_attempt_at.is_(None), MailMessage.copy_next_attempt_at <= now()),
            ),
        ).order_by(MailMessage.created_at, MailMessage.id).limit(1))
    if message_id is None:
        return True, None
    process_sent_copy(SessionLocal, message_id)
    with SessionLocal() as db:
        row = db.get(MailMessage, message_id)
        error = 'sent_copy_error' if row and row.sent_copy_status == 'Error' else None
    return True, error


def _task_loop(task_name, work, interval, max_backoff=60):
    failures = 0
    lock_name = LOCKS[task_name]
    while True:
        account_id = None
        worked = False
        delay = interval
        try:
            account_id = _active_account_id()
            _task_health(task_name, account_id)
            if account_id is not None:
                with engine.connect() as lock_connection:
                    acquired = lock_connection.execute(text('SELECT GET_LOCK(:name, 0)'), {'name': lock_name}).scalar()
                    lock_connection.commit()
                    if acquired == 1:
                        try:
                            account_id = _active_account_id()
                            if account_id is not None:
                                succeeded, error = work(account_id)
                                _task_health(task_name, account_id, success=succeeded, error=error)
                                worked = True
                                failures = 0 if succeeded else failures + 1
                                if not succeeded:
                                    delay = min(interval * (2 ** min(failures, 5)), max_backoff)
                        finally:
                            lock_connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': lock_name})
                            lock_connection.commit()
                    else:
                        _task_health(task_name, account_id, error='worker_lock_held')
            else:
                _task_health(task_name, None, success=True)
                failures = 0
        except Exception as exc:
            failures += 1
            logger.error('Tarea de correo falló task=%s cause=%s', task_name, type(exc).__name__)
            try:
                _task_health(task_name, account_id, error='worker_internal_error')
            except Exception:
                logger.error('No se pudo persistir latido task=%s', task_name)
            delay = min(interval * (2 ** min(failures, 5)), max_backoff)
        # The loops are isolated: an IMAP backoff never delays the SMTP or copy loop.
        time.sleep(max(1, delay))


def run_worker_threads():
    specs = {
        'inbox': (_inbox_once, max(5, settings.mail_sync_interval_seconds), 300, 'mail-inbox'),
        'outbox': (_outbox_once, max(1, settings.mail_outbox_poll_seconds), 30, 'mail-outbox'),
        'sent_copy': (_copy_once, max(1, settings.mail_copy_poll_seconds), 60, 'mail-sent-copy'),
    }
    def create_task(task_name):
        work, interval, max_backoff, thread_name = specs[task_name]
        return threading.Thread(target=_task_loop, args=(task_name, work, interval, max_backoff),
            name=thread_name, daemon=True)
    tasks = {name: create_task(name) for name in specs}
    for task in tasks.values():
        task.start()
    logger.info('Trabajador de correo activo; tareas independientes: INBOX, salida SMTP y copia Enviados')
    while True:
        for task_name, task in list(tasks.items()):
            if not task.is_alive():
                logger.error('Tarea de correo terminada inesperadamente task=%s; se reiniciará', task.name)
                tasks[task_name] = create_task(task_name)
                tasks[task_name].start()
        time.sleep(2)


if __name__ == '__main__':
    try:
        run_worker_threads()
    except KeyboardInterrupt:
        logger.info('Trabajador de correo detenido por señal')
