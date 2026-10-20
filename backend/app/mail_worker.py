"""Single MySQL-coordinated worker for Yahoo inbox synchronization and outbox."""
import logging
import time
from sqlalchemy import select, text
from .config import settings
from .db import SessionLocal, engine
from .mail_service import process_outbox_message, recover_stuck_outbox, sync_account
from .models import MailAccount, MailMessage

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger(__name__)
LOCK_NAME = 'comgraf-yahoo-mail-worker'


def run_cycle():
    with SessionLocal() as db:
        account = db.scalar(select(MailAccount).where(MailAccount.active.is_(True)).order_by(MailAccount.id.desc()).limit(1))
        active_id = account.id if account else None
    if active_id is None:
        return True
    recover_stuck_outbox(SessionLocal)
    sync_healthy = sync_account(SessionLocal, active_id)
    with SessionLocal() as db:
        account = db.get(MailAccount, active_id)
        if not account or not account.active:
            return True
    if account.smtp_status != 'connected':
        return sync_healthy
    for _ in range(10):
        with SessionLocal() as db:
            message_id = db.scalar(select(MailMessage.id).join(MailAccount, MailAccount.id == MailMessage.account_id).where(
                MailAccount.active.is_(True), MailMessage.account_id == active_id,
                MailMessage.direction == 'Saliente', MailMessage.status == 'Pendiente',
            ).order_by(MailMessage.created_at, MailMessage.id).limit(1))
        if message_id is None:
            break
        try:
            process_outbox_message(SessionLocal, message_id)
        except Exception:
            # Credentials, message bodies and raw MIME are never included in logs.
            logger.exception('Error interno al procesar una salida de correo #%s', message_id)
    return sync_healthy


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    failures = 0
    while True:
        try:
            with engine.connect() as lock_connection:
                acquired = lock_connection.execute(text('SELECT GET_LOCK(:name, 0)'), {'name': LOCK_NAME}).scalar()
                lock_connection.commit()
                if acquired == 1:
                    logger.info('Sincronizador Yahoo activo')
                    try:
                        while True:
                            still_owner = lock_connection.execute(text(
                                'SELECT IS_USED_LOCK(:name) = CONNECTION_ID()'
                            ), {'name': LOCK_NAME}).scalar()
                            lock_connection.commit()
                            if not still_owner:
                                logger.warning('El sincronizador Yahoo perdió su bloqueo; dejará de trabajar.')
                                break
                            healthy = run_cycle()
                            failures = 0 if healthy else failures + 1
                            delay = max(15, settings.mail_sync_interval_seconds) if healthy else min(30 * (2 ** min(failures - 1, 5)), 900)
                            time.sleep(delay)
                    finally:
                        lock_connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': LOCK_NAME})
                        lock_connection.commit()
                else:
                    logger.info('Otro proceso ya ejecuta el sincronizador Yahoo')
        except KeyboardInterrupt:
            return
        except Exception:
            failures += 1
            logger.exception('El sincronizador Yahoo no pudo completar el ciclo; reintentará en 30 segundos.')
        time.sleep(min(30 * (2 ** min(failures, 5)), 900))


if __name__ == '__main__':
    main()
