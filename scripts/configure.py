"""Create local secrets once. Never overwrite an installation."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
target = root / '.env'
if target.exists():
    raise SystemExit('.env ya existe; no se modificó.')
text = (root / '.env.example').read_text(encoding='utf-8')
text = text.replace('MYSQL_PASSWORD=\n', 'MYSQL_PASSWORD=' + secrets.token_urlsafe(36) + '\n')
text = text.replace('MYSQL_ROOT_PASSWORD=\n', 'MYSQL_ROOT_PASSWORD=' + secrets.token_urlsafe(36) + '\n')
target.write_text(text, encoding='utf-8')
print('.env creado con contraseñas aleatorias. No compartir ni versionar.')
