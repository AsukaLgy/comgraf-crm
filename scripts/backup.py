"""Portable Compose backup. SQL bytes are never passed through PowerShell pipes."""
import argparse
from datetime import datetime
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('command', choices=['backup', 'restore'])
parser.add_argument('directory', nargs='?')
parser.add_argument('--confirm-replace', action='store_true')
args = parser.parse_args()


def docker(*parts, **kwargs):
    return subprocess.run(['docker', 'compose', *parts], cwd=root, check=True, **kwargs)


if args.command == 'backup':
    target = Path(args.directory) if args.directory else root / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
    target.mkdir(parents=True, exist_ok=False)
    docker('stop', 'app')
    try:
        with (target / 'database.sql').open('wb') as out:
            docker('exec', '-T', 'db', 'sh', '-c', 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers --hex-blob --no-tablespaces "$MYSQL_DATABASE"', stdout=out)
        # docker cp works with a stopped container; app is quiescent for both snapshots.
        docker('cp', 'app:/data/uploads', str(target / 'uploads'))
        (target / 'COMPLETE').write_text('Comgraf: SQL + adjuntos. Conservar .env por separado y protegido.\n', encoding='utf-8')
    finally:
        docker('start', 'app')
    print('Respaldo completo:', target)
else:
    if not args.directory or not args.confirm_replace:
        raise SystemExit('Restaurar reemplaza los datos. Indica directorio y --confirm-replace; crea antes un respaldo actual.')
    source = Path(args.directory).resolve()
    if not (source / 'COMPLETE').is_file() or not (source / 'database.sql').is_file() or not (source / 'uploads').is_dir():
        raise SystemExit('Respaldo incompleto')
    docker('stop', 'app')
    try:
        with (source / 'database.sql').open('rb') as data:
            docker('exec', '-T', 'db', 'sh', '-c', 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot "$MYSQL_DATABASE"', stdin=data)
        docker('cp', str(source / 'uploads') + '/.', 'app:/data/uploads')
        docker('run', '--rm', '--no-deps', '--user', 'root', 'app', 'chown', '-R', '10001:10001', '/data/uploads')
    except Exception:
        raise SystemExit('Restauración incompleta. La aplicación permanece detenida; revisa el error antes de iniciarla.')
    docker('start', 'app')
    print('Restauración completa; comprueba login, expediente y descarga de adjuntos.')
