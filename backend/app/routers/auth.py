import secrets
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, delete
from ..db import get_db
from ..models import User, Role, Session, LoginAttempt, now
from ..schemas import LoginInput, UserInput, UserUpdate, Input
from ..security import current_user, passwords, digest, require, user_data, DUMMY_HASH, PERMISSIONS
from ..config import settings

router = APIRouter(prefix='/api', tags=['Usuarios'])


@router.post('/auth/login')
def login(data: LoginInput, request: Request, response: Response, db=Depends(get_db, scope='function')):
    key = digest(request.client.host + ':' + data.username.lower())
    attempt = db.get(LoginAttempt, key)
    if attempt and attempt.window_start > now() - timedelta(minutes=15) and attempt.attempts >= 10:
        raise HTTPException(429, 'Demasiados intentos. Espera 15 minutos.')
    user = db.scalar(select(User).where(User.username == data.username.lower()))
    valid = passwords.verify(data.password, user.password_hash if user else DUMMY_HASH)
    if not valid or not user or not user.active:
        if not attempt:
            attempt = LoginAttempt(key=key, attempts=0)
            db.add(attempt)
        if attempt.window_start and attempt.window_start < now() - timedelta(minutes=15):
            attempt.window_start, attempt.attempts = now(), 0
        attempt.attempts += 1
        db.commit()
        raise HTTPException(401, 'Usuario o contraseña incorrectos')
    if attempt: db.delete(attempt)
    token = secrets.token_urlsafe(48)
    db.add(Session(token_hash=digest(token), user_id=user.id, expires_at=now() + timedelta(hours=settings.session_hours)))
    response.set_cookie('comgraf_session', token, httponly=True, samesite='strict', secure=settings.cookie_secure, max_age=settings.session_hours * 3600)
    return user_data(db, user)


@router.post('/auth/logout')
def logout(request: Request, response: Response, user=Depends(current_user), db=Depends(get_db, scope='function')):
    db.execute(delete(Session).where(Session.token_hash == digest(request.cookies.get('comgraf_session', ''))))
    response.delete_cookie('comgraf_session')
    return {'ok': True}


@router.get('/auth/me')
def me(user=Depends(current_user), db=Depends(get_db, scope='function')):
    return {**user_data(db, user), 'timezone': settings.app_timezone}


@router.get('/users')
def users(user=Depends(current_user), db=Depends(get_db, scope='function')):
    return [user_data(db, u) for u in db.scalars(select(User).order_by(User.name))]


def validate_roles(db, roles):
    if len(set(roles)) != len(roles) or any(not db.get(Role, r) for r in roles):
        raise HTTPException(422, 'Roles no válidos')


@router.post('/users', status_code=201)
def add_user(data: UserInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, '*')
    validate_roles(db, data.roles)
    if db.scalar(select(User.id).where(User.username == data.username.lower())): raise HTTPException(409, 'El usuario ya existe')
    created = User(username=data.username.lower(), name=data.name, password_hash=passwords.hash(data.password), roles=data.roles, capacity=data.capacity)
    db.add(created)
    db.flush()
    return user_data(db, created)


@router.put('/users/{user_id}')
def update_user(user_id: int, data: UserUpdate, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, '*')
    target = db.get(User, user_id)
    if not target: raise HTTPException(404, 'Usuario no encontrado')
    validate_roles(db, data.roles)
    if user_id == user.id and (not data.active or 'Administrador' not in data.roles):
        raise HTTPException(422, 'Conserva tu acceso de administrador; otro administrador puede modificarlo')
    for key in ('name', 'roles', 'active', 'capacity'): setattr(target, key, getattr(data, key))
    if data.password: target.password_hash = passwords.hash(data.password)
    db.execute(delete(Session).where(Session.user_id == user_id))
    return user_data(db, target)


class PasswordInput(Input):
    current_password: str
    new_password: str


@router.post('/auth/password')
def change_password(data: PasswordInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    if len(data.new_password) < 12 or len(data.new_password) > 200: raise HTTPException(422, 'Usa entre 12 y 200 caracteres')
    if not passwords.verify(data.current_password, user.password_hash): raise HTTPException(403, 'Contraseña actual incorrecta')
    user.password_hash = passwords.hash(data.new_password)
    db.execute(delete(Session).where(Session.user_id == user.id))
    return {'ok': True}


@router.get('/roles')
def roles(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, '*')
    return {'roles': [{'name': r.name, 'permissions': r.permissions} for r in db.scalars(select(Role))], 'available': PERMISSIONS}


class RoleInput(Input):
    permissions: list[str]


@router.put('/roles/{name}')
def update_role(name: str, data: RoleInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, '*')
    role = db.get(Role, name)
    if not role or name == 'Administrador' or '*' in data.permissions or any(p not in PERMISSIONS for p in data.permissions):
        raise HTTPException(422, 'Configuración de rol no permitida')
    role.permissions = list(set(data.permissions))
    return {'ok': True}
