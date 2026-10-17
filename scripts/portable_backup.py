"""Backup/restore a native MySQL installation. Stop the HTTP app before use."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import uuid

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('command', choices=['backup', 'restore'])
parser.add_argument('directory', nargs='?')
parser.add_argument('--app-stopped', action='store_true', help='Confirma que detuviste Python para hacer consistente SQL + archivos')
parser.add_argument('--confirm-replace', action='store_true', help='Confirma reemplazo de datos al restaurar')
args = parser.parse_args()
if not args.app_stopped: raise SystemExit('Detén la aplicación HTTP y usa --app-stopped. MySQL debe seguir ejecutándose.')
env = dict(line.split('=', 1) for line in (root / '.env').read_text(encoding='utf-8').splitlines() if '=' in line and not line.startswith('#'))
base = next((root / '.runtime').glob('mysql-*-winx64'), None)
bin_dir = base / 'bin' if base else None
uploads = Path(env.get('UPLOAD_DIR', '../uploads'))
if not uploads.is_absolute(): uploads = (root / 'backend' / uploads).resolve()
temp = root / '.runtime' / ('backup-' + uuid.uuid4().hex + '.cnf')
temp.parent.mkdir(exist_ok=True)


def option(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


temp.write_text('[client]\n' + '\n'.join(f'{k}={option(v)}' for k, v in {'user':env['MYSQL_USER'],'password':env['MYSQL_PASSWORD'],'host':env.get('DB_HOST','127.0.0.1'),'port':env.get('DB_PORT','3306')}.items()) + '\n', encoding='utf-8')
database = env['MYSQL_DATABASE']
try:
    if args.command == 'backup':
        target = Path(args.directory) if args.directory else root / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
        target.mkdir(parents=True, exist_ok=False)
        exe = str(bin_dir / 'mysqldump.exe') if bin_dir else 'mysqldump'
        with (target / 'database.sql').open('wb') as out:
            subprocess.run([exe, f'--defaults-extra-file={temp}', '--single-transaction', '--no-tablespaces', '--hex-blob', '--set-gtid-purged=OFF', database], stdout=out, check=True)
        if uploads.is_dir(): shutil.copytree(uploads, target / 'uploads')
        else: (target / 'uploads').mkdir()
        (target / 'COMPLETE').write_text('Comgraf: SQL y archivos; .env se respalda por separado.\n', encoding='utf-8')
        print('Respaldo completo:', target)
    else:
        if not args.directory or not args.confirm_replace: raise SystemExit('Indica respaldo y --confirm-replace. Se reemplazan los datos actuales.')
        source = Path(args.directory).resolve()
        if not all((source / p).exists() for p in ('database.sql','uploads','COMPLETE')): raise SystemExit('Respaldo incompleto')
        exe = str(bin_dir / 'mysql.exe') if bin_dir else 'mysql'
        with (source / 'database.sql').open('rb') as data:
            subprocess.run([exe, f'--defaults-extra-file={temp}', database], stdin=data, check=True)
        shutil.copytree(source / 'uploads', uploads, dirs_exist_ok=True)
        print('Restaurado. Reinicia la aplicación y comprueba expedientes y archivos.')
finally:
    temp.unlink(missing_ok=True)
