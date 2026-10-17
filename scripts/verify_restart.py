"""Verify persistence by restarting the project-owned portable MySQL and test HTTP server.

Run after stopping other test HTTP processes. Does not delete or modify business rows.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx
import pymysql

root = Path(__file__).resolve().parent.parent
env = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
credentials = json.loads((root / '.runtime/e2e.json').read_text())
base = next((root / '.runtime').glob('mysql-*-winx64'))
flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
test_env = {**os.environ, 'MYSQL_DATABASE':'comgraf_test','UPLOAD_DIR':'../.runtime/test-uploads'}


def start_http():
    log = (root / '.runtime/restart-http.log').open('ab')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8002'], cwd=root / 'backend', env=test_env, stdout=log, stderr=log, creationflags=flags)
    log.close()
    for _ in range(100):
        try:
            if httpx.get('http://127.0.0.1:8002/api/health').status_code == 200: return process
        except httpx.HTTPError: pass
        time.sleep(.1)
    process.terminate()
    raise RuntimeError('Servidor de verificación no disponible')


def snapshot(client):
    cases = client.get('/api/cases?view=all').json()['items']
    values = sorted((client.get('/api/cases/' + str(c['id'])).json() for c in cases), key=lambda c: c['id'])
    for c in values: c.pop('requires_action', None)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest(), len(values)


http = start_http()
client = httpx.Client(base_url='http://127.0.0.1:8002', headers={'X-Comgraf-Request':'1'})
response = client.post('/api/auth/login', json={k:credentials[k] for k in ('username','password')})
response.raise_for_status()
before, count = snapshot(client)
http.terminate()
http.wait(timeout=15)
connection = pymysql.connect(host='127.0.0.1', port=3307, user='root', password=env['MYSQL_ROOT_PASSWORD'])
with connection.cursor() as cursor: cursor.execute('SHUTDOWN')
connection.close()
for _ in range(100):
    try:
        probe = pymysql.connect(host='127.0.0.1', port=3307, user='comgraf', password=env['MYSQL_PASSWORD'], connect_timeout=1)
        probe.close()
        time.sleep(.1)
    except pymysql.Error: break
time.sleep(5)  # Windows can release InnoDB file locks shortly after SHUTDOWN returns.
mysql = subprocess.Popen([str(base / 'bin/mysqld.exe'), f'--basedir={base}', f'--datadir={root / ".runtime/mysql-data"}', '--bind-address=127.0.0.1','--port=3307','--mysqlx=OFF',f'--log-error={root / ".runtime/mysql.log"}'], creationflags=flags)
(root / '.runtime/mysql.pid').write_text(str(mysql.pid))
http = start_http()
try:
    # Reuse the same HTTP cookie: sessions must survive restart too.
    after, after_count = snapshot(client)
    assert before == after and count == after_count
    result = {'mysql':'8.4.11', 'cases_compared':count, 'same_persistent_state':True, 'session_survived':True, 'sha256':after}
    (root / '.runtime/restart-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
finally:
    http.terminate()
    http.wait(timeout=15)
    client.close()
