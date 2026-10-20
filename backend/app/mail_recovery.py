"""Bounded, read-only preview and explicit recovery for a selected Yahoo UID range."""
import argparse
import re
import sys

from sqlalchemy import select, text

from .db import SessionLocal, engine
from .mail_security import decrypt_password
from .mail_service import _imap_internal_date, _process_sync_uid
from .mail_transport import _uidvalidity, open_imap
from .models import MailAccount, MailMessage, MailSyncCursor, MailSyncOutcome

MAX_RANGE = 100


def inspect_range(start_uid, end_uid):
    if start_uid < 1 or end_uid < start_uid or end_uid - start_uid + 1 > MAX_RANGE:
        raise ValueError(f'El rango debe tener entre 1 y {MAX_RANGE} UID consecutivos.')
    with SessionLocal() as db:
        account = db.scalar(select(MailAccount).where(MailAccount.active.is_(True)).order_by(MailAccount.id.desc()).limit(1))
        if not account or not account.encrypted_password:
            raise RuntimeError('No hay una cuenta Yahoo activa.')
        account_id, email, encrypted = account.id, account.email, account.encrypted_password
        cursor = db.scalar(select(MailSyncCursor).where(MailSyncCursor.account_id == account_id, MailSyncCursor.folder == 'INBOX'))
        if not cursor:
            raise RuntimeError('La cuenta no tiene un cursor INBOX activo.')
        activation_floor = cursor.activated_at.replace(microsecond=0)
        current_validity = cursor.uidvalidity
    password = decrypt_password(encrypted)
    connection = open_imap()
    try:
        connection.login(email, password)
        result, _ = connection.select('INBOX', readonly=True)
        if result != 'OK':
            raise RuntimeError('No se pudo abrir INBOX en modo de solo lectura.')
        validity = _uidvalidity(connection)
        if validity != current_validity:
            raise RuntimeError('UIDVALIDITY cambió; no se puede aplicar este rango con el cursor actual.')
        result, rows = connection.uid('search', None, 'UID', f'{start_uid}:{end_uid}')
        if result != 'OK':
            raise RuntimeError('Yahoo no pudo consultar el rango solicitado.')
        uids = [int(uid) for uid in (rows[0] or b'').split() if uid.isdigit() and start_uid <= int(uid) <= end_uid]
        preview = []
        with SessionLocal() as db:
            for uid in uids:
                outcome = db.scalar(select(MailSyncOutcome).where(
                    MailSyncOutcome.account_id == account_id, MailSyncOutcome.folder == 'INBOX',
                    MailSyncOutcome.uidvalidity == validity, MailSyncOutcome.uid == uid,
                ))
                existing = db.scalar(select(MailMessage.id).where(
                    MailMessage.account_id == account_id, MailMessage.folder == 'INBOX',
                    MailMessage.uidvalidity == validity, MailMessage.uid == uid,
                ))
                if existing or (outcome and outcome.outcome in {'imported', 'ignored'}):
                    preview.append({'uid': uid, 'state': 'deduplicado', 'date': None, 'size': None})
                    continue
                status, data = connection.uid('fetch', str(uid), '(RFC822.SIZE INTERNALDATE)')
                metadata = next((part[0] if isinstance(part, tuple) else part for part in data or []
                    if isinstance(part, bytes) or (isinstance(part, tuple) and isinstance(part[0], bytes))), b'')
                size_match = re.search(rb'\bRFC822\.SIZE\s+(\d+)', metadata, re.I)
                internal_date = _imap_internal_date(metadata)
                if status != 'OK' or not size_match:
                    state = 'metadatos pendientes de recuperar'
                elif internal_date is None:
                    state = 'fecha no interpretable; requiere revisión'
                elif internal_date < activation_floor:
                    state = 'anterior a la activación; se conservará sin importar'
                else:
                    state = 'candidato posterior a la activación'
                    header_status, header_rows = connection.uid('fetch', str(uid), '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])')
                    header = next((part[1] for part in header_rows or [] if isinstance(part, tuple) and len(part) > 1 and isinstance(part[1], bytes)), b'') if header_status == 'OK' else b''
                    match = re.search(rb'^Message-ID:\s*(.+)$', header, re.I | re.M)
                    if match:
                        message_id = match.group(1).decode('ascii', errors='ignore').strip()
                        if db.scalar(select(MailMessage.id).where(MailMessage.account_id == account_id, MailMessage.message_id == message_id)):
                            state = 'duplicado por Message-ID'
                preview.append({'uid': uid, 'state': state,
                    'date': internal_date.isoformat() if internal_date else None,
                    'size': int(size_match.group(1)) if size_match else None})
        return account_id, validity, activation_floor, preview
    finally:
        try:
            connection.logout()
        except Exception:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-uid', type=int, required=True)
    parser.add_argument('--end-uid', type=int, required=True)
    parser.add_argument('--apply', action='store_true', help='Importa únicamente los UID listados explícitamente con --uids.')
    parser.add_argument('--uids', help='UID candidatos separados por coma; obligatorio junto a --apply.')
    args = parser.parse_args(argv)
    try:
        account_id, validity, activation_floor, preview = inspect_range(args.start_uid, args.end_uid)
        print(f'Vista previa INBOX: UIDVALIDITY={validity}, rango={args.start_uid}:{args.end_uid}, máximo={MAX_RANGE}')
        for row in preview:
            print(f"UID {row['uid']}: {row['state']}; INTERNALDATE={row['date'] or 'no disponible'}; bytes={row['size'] if row['size'] is not None else 'no disponible'}")
        candidates = {row['uid'] for row in preview if row['state'] == 'candidato posterior a la activación'}
        if not args.apply:
            print(f'Candidatos nuevos, acotados y posteriores a la activación: {len(candidates)}. No se modificó la base de datos.')
            return 0
        if not args.uids:
            raise ValueError('Para aplicar, proporciona --uids con los UID elegidos de la vista previa.')
        selected = {int(value) for value in args.uids.split(',') if value.strip()}
        if not selected or not selected.issubset(candidates):
            raise ValueError('Solo se pueden aplicar UID candidatos que aparecieron en esta vista previa.')
        with SessionLocal() as db:
            account = db.get(MailAccount, account_id)
            email, encrypted = account.email, account.encrypted_password
        password = decrypt_password(encrypted)
        lock_connection = engine.connect()
        acquired = lock_connection.execute(text('SELECT GET_LOCK(:name, 0)'), {'name': 'comgraf-mail-inbox'}).scalar()
        lock_connection.commit()
        if acquired != 1:
            lock_connection.close()
            raise RuntimeError('La recepción está activa; espera y vuelve a ejecutar la recuperación.')
        connection = None
        try:
            connection = open_imap()
            connection.login(email, password)
            result, _ = connection.select('INBOX', readonly=True)
            if result != 'OK' or _uidvalidity(connection) != validity:
                raise RuntimeError('INBOX o UIDVALIDITY cambió después de la vista previa; no se importó nada.')
            for uid in sorted(selected):
                outcome = _process_sync_uid(SessionLocal, connection, account_id, validity, uid, activation_floor)
                print(f'UID {uid}: {outcome}')
        finally:
            if connection is not None:
                try:
                    connection.logout()
                except Exception:
                    pass
            try:
                lock_connection.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': 'comgraf-mail-inbox'})
                lock_connection.commit()
            finally:
                lock_connection.close()
        return 0
    except Exception as exc:
        print(f'Recuperación detenida: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
