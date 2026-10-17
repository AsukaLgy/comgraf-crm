"""Development verification on Windows without installing a service. Loopback only."""
import os
from pathlib import Path
import secrets
import subprocess
import time
import pymysql

root = Path(__file__).resolve().parent.parent
runtime = root / '.runtime'
base = next(runtime.glob('mysql-*-winx64'))
data = runtime / 'mysql-data'
env = root / '.env'
if env.exists():
    raise SystemExit('Ya existe .env. Usa scripts/start-local.ps1 para reiniciar; no se sobrescribe configuración.')
password, root_password = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
env.write_text(f'MYSQL_DATABASE=comgraf\nMYSQL_USER=comgraf\nMYSQL_PASSWORD={password}\nMYSQL_ROOT_PASSWORD={root_password}\nDB_HOST=127.0.0.1\nDB_PORT=3307\nAPP_TIMEZONE=America/Tegucigalpa\nUPLOAD_DIR=../uploads\nSTATIC_DIR=../frontend/dist\n', encoding='utf-8')
exe = str(base / 'bin' / 'mysqld.exe')
subprocess.run([exe, '--initialize-insecure', f'--basedir={base}', f'--datadir={data}', '--console'], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
init = runtime / 'mysql-init.sql'
init.write_text(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{root_password}';\nCREATE DATABASE comgraf CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;\nCREATE DATABASE comgraf_test CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;\nCREATE USER 'comgraf'@'localhost' IDENTIFIED BY '{password}';\nGRANT ALL ON comgraf.* TO 'comgraf'@'localhost';\nGRANT ALL ON comgraf_test.* TO 'comgraf'@'localhost';\n", encoding='utf-8')
args = [exe, f'--basedir={base}', f'--datadir={data}', '--bind-address=127.0.0.1', '--port=3307', '--mysqlx=OFF', f'--init-file={init}', f'--log-error={runtime / "mysql.log"}']
proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
(runtime / 'mysql.pid').write_text(str(proc.pid))
for _ in range(60):
    try:
        conn = pymysql.connect(host='127.0.0.1', port=3307, user='comgraf', password=password, database='comgraf')
        with conn.cursor() as cursor:
            cursor.execute('SELECT VERSION()')
            print('MySQL real disponible en loopback:3307:', cursor.fetchone()[0])
        conn.close()
        init.unlink()
        break
    except pymysql.Error:
        time.sleep(1)
else:
    raise SystemExit('MySQL no inició; revisar .runtime/mysql.log')
