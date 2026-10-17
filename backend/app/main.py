from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from .config import settings
from .db import engine
from .routers import auth, clients, cases, production, reports

app = FastAPI(title='Comgraf · HTTP interno', docs_url=None, redoc_url=None)


@app.middleware('http')
async def guard(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.url.path.startswith('/api'):
        if request.headers.get('X-Comgraf-Request') != '1':
            return JSONResponse({'detail': 'Solicitud de origen no autorizado'}, 403)
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Origen no autorizado'}, 403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api'): response.headers['Cache-Control'] = 'no-store'
    return response


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


static = Path(settings.static_dir)
if (static / 'assets').exists(): app.mount('/assets', StaticFiles(directory=static / 'assets'), name='assets')


@app.get('/{path:path}')
def spa(path: str):
    if path.startswith('api/'):
        return JSONResponse({'detail': 'Ruta no encontrada'}, 404)
    if (static / 'index.html').exists(): return FileResponse(static / 'index.html')
    return JSONResponse({'detail': 'Compila la interfaz: npm --prefix frontend run build'}, 503)
