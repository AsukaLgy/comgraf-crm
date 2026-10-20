"""Integration tests exclusively against a disposable real MySQL schema."""
import os
import secrets
import subprocess
import sys
from pathlib import Path
import pytest

os.environ['MYSQL_DATABASE'] = os.environ.get('TEST_MYSQL_DATABASE', 'comgraf_test')
os.environ['UPLOAD_DIR'] = os.environ.get('TEST_UPLOAD_DIR', str(Path('../.runtime/test-uploads').resolve()))
from app.config import settings
if not settings.mysql_database.endswith('_test'):
    raise RuntimeError('Las pruebas requieren una base terminada en _test')
from app.db import SessionLocal, engine, Base
from app.models import User
from app.security import passwords
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture(scope='session', autouse=True)
def migrate():
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    assert engine.dialect.name == 'mysql'


@pytest.fixture()
def accounts(migrate):
    # Only test data, preserving role definitions and migration version.
    with engine.begin() as conn:
        conn.execute(text('UPDATE ledger SET reverses_id = NULL WHERE reverses_id IS NOT NULL'))
        conn.execute(text('UPDATE cases SET related_case_id = NULL WHERE related_case_id IS NOT NULL'))
        conn.execute(text('UPDATE mail_messages SET retry_of_id = NULL WHERE retry_of_id IS NOT NULL'))
        for table in reversed(Base.metadata.sorted_tables):
            if table.name != 'roles': conn.execute(table.delete())
    credentials = {}
    with SessionLocal.begin() as db:
        for role, name in [('Administrador','admin'), ('Coordinador','coordinator'), ('Atención','agent'), ('Atención','agent2'), ('Producción','printer'), ('Cobranza','cashier'), ('Escáner de encuadernación','scanner'), ('Escáner de encuadernación','scanner2')]:
            password = secrets.token_urlsafe(20)
            user = User(username=name, name=name.title(), password_hash=passwords.hash(password), roles=[role], capacity=30)
            db.add(user)
            db.flush()
            credentials[name] = {'id': user.id, 'password': password}
    return credentials


@pytest.fixture()
def clients(accounts):
    instances = {}
    for name, info in accounts.items():
        client = TestClient(app, headers={'X-Comgraf-Request':'1', 'X-Comgraf-Contract':'4', 'X-Comgraf-Build':'1.4.0'})
        response = client.post('/api/auth/login', json={'username':name, 'password':info['password']})
        assert response.status_code == 200, response.text
        instances[name] = client
    yield instances
    for client in instances.values(): client.close()
