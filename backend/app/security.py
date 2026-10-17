import hashlib
import secrets
from datetime import timedelta
from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import select
from .db import get_db
from .models import User, Role, Session, now
from .config import settings

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(24))
DEFAULT_ROLES = {
    'Administrador': ['*'],
    'Coordinador': ['read_all', 'attention', 'assign', 'reopen', 'production', 'finance', 'adjust', 'clients', 'reports'],
    'Atención': ['attention', 'clients'],
    'Producción': ['production'],
    'Cobranza': ['finance', 'reports'],
}
PERMISSIONS = sorted({p for values in DEFAULT_ROLES.values() for p in values})


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def permissions(db, user):
    return {p for role in db.scalars(select(Role).where(Role.name.in_(user.roles))) for p in role.permissions}


def can(db, user, permission):
    available = permissions(db, user)
    return '*' in available or permission in available


def require(db, user, permission):
    if not can(db, user, permission):
        raise HTTPException(403, 'No tienes permiso para esta operación')


def current_user(request: Request, db=Depends(get_db, scope='function')):
    raw = request.cookies.get('comgraf_session', '')
    session = db.get(Session, digest(raw)) if raw else None
    if not session or session.expires_at < now():
        raise HTTPException(401, 'Inicia sesión para continuar')
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, 'Cuenta desactivada')
    return user


def user_data(db, user):
    return {'id': user.id, 'username': user.username, 'name': user.name,
            'roles': user.roles, 'active': user.active, 'capacity': user.capacity,
            'permissions': sorted(permissions(db, user)),
            'timezone': settings.app_timezone}
