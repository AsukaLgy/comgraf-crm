"""Persistent local encryption key for the Yahoo application password."""
import os
import time
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from .config import settings


def _key() -> bytes:
    path = Path(settings.mail_encryption_key_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = None
    else:
        try:
            value = memoryview(Fernet.generate_key())
            while value:
                value = value[os.write(descriptor, value):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if os.name != 'nt':
        path.chmod(0o600)
        path.parent.chmod(0o700)
    for attempt in range(100):
        key = path.read_bytes().strip()
        try:
            Fernet(key)
            return key
        except (ValueError, TypeError):
            if descriptor is not None or attempt == 99:
                raise RuntimeError('La clave persistente del correo no es válida; restaura la copia protegida.')
            # Another app process may have created the exclusive file and still be writing it.
            time.sleep(0.01)
    raise RuntimeError('La clave persistente del correo no es válida; restaura la copia protegida.')


def encrypt_password(password: str) -> str:
    return Fernet(_key()).encrypt(password.encode('utf-8')).decode('ascii')


def decrypt_password(token: str) -> str:
    try:
        return Fernet(_key()).decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError('No se pudo descifrar la contraseña del buzón; restaura la clave respaldada.') from exc
