"""Explicit fictional browser-test data. Never targets a real installation."""
import os
import json
import secrets
from pathlib import Path
os.environ['MYSQL_DATABASE'] = os.environ.get('TEST_MYSQL_DATABASE', 'comgraf_test')
from app.config import settings
from app.db import SessionLocal
from app.models import User, Client, Case
from app.repository import normalize
from app.security import passwords

if not settings.mysql_database.endswith('_test'): raise SystemExit('Solo base _test')
token = secrets.token_hex(4)
password = secrets.token_urlsafe(24)
with SessionLocal.begin() as db:
    user = User(username='e2e_' + token, name='DEMO · Coordinación Comgraf', password_hash=passwords.hash(password), roles=['Administrador'], capacity=500)
    db.add(user)
    db.flush()
    for name, subject, priority in [('DEMO · Café del Valle','Tarjetas para nueva sucursal','Alta'),('DEMO · Colegio Horizonte','Talonarios de matrícula','Normal'),('DEMO · Estudio Naranja','Consulta sobre impresión de portafolio','Normal')]:
        client = Client(name=name, company=name, search_text=normalize(name), phones=['+504 2222-0000'])
        db.add(client)
        db.flush()
        db.add(Case(client_id=client.id, subject=subject, priority=priority, kind='Consulta'))
    credentials = {'username':user.username,'password':password,'user_id':user.id}
Path('../.runtime/e2e.json').write_text(json.dumps(credentials), encoding='utf-8')
print('Identidad temporal E2E creada en comgraf_test. Instalación real intacta.')
