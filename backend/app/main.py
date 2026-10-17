import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from .config import settings
from .db import engine, SessionLocal
from .routers import auth, clients, cases, production, reports
from .services import reopen_overdue_attention

logger = logging.getLogger(__name__)

async def attention_recovery_loop():
    """Reopen due follow-ups even when no worker has a browser open."""
    while True:
        try:
            with SessionLocal.begin() as db:
                reopen_overdue_attention(db)
        except Exception:
            # A database outage is retried on the next cycle; it never blocks startup.
            logger.exception('Falló la recuperación de seguimientos vencidos')
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(attention_recovery_loop())
    try:
        yield
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass


app = FastAPI(title='Comgraf · HTTP interno', docs_url=None, redoc_url=None, lifespan=lifespan)


def error_code(status, detail):
    message = str(detail).lower()
    if 'bloqueo' in message or 'bloqueado' in message: return 'PRODUCTION_BLOCKED'
    if 'contrato' in message or 'interfaz' in message: return 'CLIENT_CONTRACT_MISMATCH'
    if status == 403: return 'PERMISSION_DENIED'
    if 'seguimiento' in message and ('vencido' in message or 'reprograma' in message): return 'OVERDUE_FOLLOWUP'
    if 'etapa ya está' in message or 'etapa ya esta' in message: return 'SAME_STAGE'
    if 'etapa' in message and ('siguiente' in message or 'retroceder' in message): return 'INVALID_TRANSITION'
    if status == 422: return 'FIELD_VALIDATION'
    if status == 409:
        if 'cambió' in message or 'cambio' in message or 'versión' in message or 'version' in message: return 'VERSION_CONFLICT'
        return 'BUSINESS_CONFLICT'
    return 'HTTP_ERROR'


@app.middleware('http')
async def guard(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.url.path.startswith('/api'):
        if request.headers.get('X-Comgraf-Request') != '1':
            return JSONResponse({'detail': 'Solicitud de origen no autorizado'}, 403)
        contract = request.headers.get('X-Comgraf-Contract')
        if contract != '2':
            return JSONResponse({'detail': 'Contrato HTTP incompatible. Actualiza la interfaz y reinicia Comgraf.', 'code': 'CLIENT_CONTRACT_MISMATCH'}, 409)
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Origen no autorizado'}, 403)
    response = await call_next(request)
    if request.method in ('GET', 'HEAD') and not request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api'): response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    detail = error.detail
    return JSONResponse({'detail': detail, 'code': error_code(error.status_code, detail)}, status_code=error.status_code, headers=error.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return JSONResponse({'detail': error.errors(), 'code': 'FIELD_VALIDATION'}, status_code=422)


@app.exception_handler(IntegrityError)
async def integrity(request, error):
    return JSONResponse({'detail': 'El registro cambió o ya existe. Actualiza y revisa antes de reintentar.'}, 409)


@app.exception_handler(OperationalError)
async def unavailable(request, error):
    return JSONResponse({'detail': 'La base de datos no está disponible o hubo un conflicto transaccional. Conserva el formulario y reintenta.'}, 503)


for router in (auth.router, clients.router, cases.router, production.router, reports.router):
    app.include_router(router)


@app.get('/api/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return {'status': 'ok', 'database': 'mysql', 'app': 'Comgraf'}


@app.get('/api/version')
def version():
    return {'product': 'Comgraf', 'product_version': settings.product_version, 'build': settings.build_id, 'contract_version': '2'}


static = Path(settings.static_dir)
if (static / 'assets').exists(): app.mount('/assets', StaticFiles(directory=static / 'assets'), name='assets')


@app.get('/{path:path}')
def spa(path: str):
    if path.startswith('api/'):
        return JSONResponse({'detail': 'Ruta no encontrada'}, 404)
    if (static / 'index.html').exists(): return FileResponse(static / 'index.html')
    return JSONResponse({'detail': 'Compila la interfaz: npm --prefix frontend run build'}, 503)
