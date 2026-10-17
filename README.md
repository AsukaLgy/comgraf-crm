# Comgraf · Comunicación Gráfica

CRM local para imprenta: un expediente desde atención hasta producción, entrega y cobranza. React/JavaScript, FastAPI, SQLAlchemy, Alembic y **MySQL real**. Interfaz en español, sin Frappe, Bench, CDN ni servicios cloud obligatorios.

## Ejecución en este equipo Windows

Se dejó el código en `C:\Users\asuka\Desktop\CRM`, las dependencias instaladas, la interfaz compilada y MySQL portátil en `.runtime` escuchando únicamente en `127.0.0.1:3307`. La base real `comgraf` está separada de `comgraf_test`; los datos ficticios y las credenciales de pruebas nunca se insertan en la base real.

Crear la cuenta inicial, sin contraseña predeterminada:

```powershell
cd C:\Users\asuka\Desktop\CRM\backend
..\.venv\Scripts\python.exe -m app.cli create-admin
```

El comando solicita usuario, nombre y contraseña de al menos 12 caracteres sin mostrarla. Solo funciona si todavía no hay usuarios y protege la creación simultánea. Los usuarios siguientes se crean en Configuración.

Iniciar la aplicación:

```powershell
cd C:\Users\asuka\Desktop\CRM
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

Abrir **http://127.0.0.1:8000**. Esta ruta sirve React y la interfaz HTTP Python desde el mismo origen. El script inicia MySQL si es necesario, aplica migraciones y mantiene la aplicación en la terminal. Ctrl+C detiene Python; MySQL permanece como proceso local. No instala servicios de Windows.

`.env` ya contiene claves aleatorias de MySQL generadas para este equipo; no se muestran en el repositorio ni se deben compartir. `.runtime`, `.env`, adjuntos y dependencias están excluidos de Git y del contexto de Docker.

## Instalación reproducible Windows / Linux con Docker Compose

Requisitos: Docker Engine/Compose v2 o Docker Desktop con contenedores Linux; Python 3 para generar configuración. La instalación inicial descarga imágenes y dependencias. El uso normal posterior no requiere Internet.

En una carpeta nueva con este código:

```sh
python scripts/configure.py
docker compose up -d --build
docker compose exec app python -m app.cli create-admin
```

Abrir **http://127.0.0.1:8080**. Si ya tienes `.env`, `configure.py` se niega a sobrescribirlo. Ajusta sus variables; Compose fuerza host `db`, puerto `3306` y rutas internas, por lo que puede utilizar las claves existentes. Los volúmenes de Compose son independientes de la base portátil; no se importan datos automáticamente entre instalaciones.

MySQL no publica ningún puerto. Solo Python habla con MySQL; React usa `/api`. La imagen sirve recursos compilados locales. Los contenedores se comunican por una red interna. La aplicación se ejecuta como usuario sin privilegios. Los volúmenes conservan MySQL y adjuntos al recrear servicios; **no uses `docker compose down -v` para actualizar**.

### Trabajadores en la red local

En `.env`, cambia `APP_HOST` de `127.0.0.1` a la IP privada fija del equipo servidor (ejemplo `192.168.1.20`), conserva `APP_PORT=8080` y ejecuta:

```sh
docker compose up -d
```

Permite ese puerto solo desde la subred de trabajadores en el firewall. Los trabajadores abren `http://IP_PRIVADA:8080`. No abras puertos en el router ni publiques dominios o túneles a Internet. No necesitan MySQL, Python ni Node en sus computadoras.

HTTP local funciona con `COOKIE_SECURE=false`. Si la LAN necesita cifrado, añade terminación TLS local y cambia a `COOKIE_SECURE=true`; no habilites esa variable en una dirección HTTP. La zona de operación se configura con `APP_TIMEZONE`, inicialmente `America/Tegucigalpa`.

## Desarrollo sin Docker

Requisitos: Python 3.14, Node 24, MySQL 8.4. Crea una base UTF-8 y un usuario restringido a esa base. Copia `.env.example` a `.env` y configura host/puerto/credenciales, `UPLOAD_DIR=../uploads`, `STATIC_DIR=../frontend/dist`.

Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
npm.cmd --prefix frontend ci --ignore-scripts
npm.cmd --prefix frontend run build
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.cli create-admin
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Linux:

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock.txt
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run build
cd backend
../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m app.cli create-admin
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Para desarrollo de React, en otra terminal: `npm --prefix frontend run dev`. Vite reenvía `/api` a `127.0.0.1:8000` conservando el origen. Para uso normal utiliza la interfaz compilada servida por Python.

## Recorrido de trabajo

1. Crear cliente provisional (basta un nombre); agregar contactos y datos comerciales cuando se conozcan.
2. Crear caso y tomarlo desde el pool. Registrar notas internas con plantillas; resumir llamadas, visitas, acuerdos y comentarios en una sola bitacora; programar seguimientos. Cerrar atención retira el caso de acción inmediata, pero no cierra trabajo ni cobranza: «Cerrar atención retira el caso de pendientes inmediatos; el trabajo y sus cobros continúan».
3. Preparar cotización con partidas y vigencia. Registrar aceptación/rechazo de una versión. Generar orden reutiliza esa versión y conserva el expediente.
4. Generar orden activa el mismo expediente en Nuevo. Avanzar por Nuevo → Aprobación → Impresión → Taller → Listo para entregar. Aprobación es revisión operativa con el cliente, sin autorización interna separada.
5. Programar el inicio y compromiso de entrega de forma independiente; el evento conserva fechas anteriores y nuevas, usuario y hora. El avance normal no exige motivo; bloqueos y retrocesos sí.
6. Registrar cantidades entregadas por partida y evidencia. Una entrega parcial conserva pendientes; solo las cantidades completas muestran Entregado y detienen el tiempo activo.
7. Registrar anticipos/abonos; corregir mediante reversión o ajuste autorizado. La entrega permite, con permiso financiero, incluir un abono en la misma operación o liquidar el saldo mostrado; entregar nunca crea un pago. Los saldos siguen visibles con atención cerrada.

Si se anula un trabajo, el usuario registra el motivo y elige **Sin cargo** o un **importe final acordado** (total definitivo del trabajo, no cargo adicional). El ajuste conserva obligación y pagos anteriores. Si el total pagado supera el importe final, queda saldo a favor; una devolución requiere que un usuario con permiso financiero confirme fecha/importe/referencia después de realizarla. Una persona que puede detener producción pero no ajustar cobros deja la resolución en la bandeja **Resolución de anulación**. Las órdenes históricas anuladas también requieren revisión explícita.

Correo/WhatsApp muestran **No configurado**. Se guardan borradores y se registran interacciones manuales; no hay envíos simulados. Los borradores se guardan por caso/canal/usuario en MySQL; el navegador conserva un respaldo provisional durante la escritura. Los expedientes y saldos nunca dependen de localStorage.

## Actualización

En Windows local, respalda primero SQL y adjuntos, detén Comgraf con Ctrl+C en la terminal `start-local.ps1` y ejecuta desde la raíz:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\update-local.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

El actualizador comprueba la configuración, instala dependencias solo si cambiaron sus archivos de bloqueo (o con `-ForceDependencies`), compila React y aplica Alembic. Si algo falla, se detiene. Conserva `.env`, MySQL y los adjuntos; no borres `.runtime`.

`/api/version` identifica versión de producto, build y contrato HTTP. Builds distintos no implican incompatibilidad por sí solos. Las operaciones de escritura exigen contrato HTTP 3. Ante incompatibilidad, ejecuta la actualización anterior y reinicia Python. El HTML exige revalidación y los assets conservan nombres versionados.

Las migraciones `005_commercial_cancellation` y `006_cancelled_stage_snapshot` agregan trazabilidad y marcan anulaciones existentes para revisión; no cambian pagos, saldos ni etapas sin clasificar, y no se editan después de aplicadas. Las anulaciones antiguas requieren revisión explícita; no se ponen en cero automáticamente.

Con Docker, después del respaldo: `docker compose build app` y `docker compose up -d app`. El arranque aplica Alembic antes de servir solicitudes. Comprueba `docker compose ps`, `/api/health`, login, expediente y descarga de archivo. Para instalar cambios sin Docker, usa el script Windows indicado arriba o ejecuta manualmente los pasos de Desarrollo sin Docker. Crea revisiones Alembic nuevas; no edites migraciones aplicadas. `alembic check` detecta diferencias entre modelos y esquema.

## Respaldo y restauración de Compose

```sh
python scripts/backup.py backup
```

Detiene brevemente la aplicación para congelar altas de archivos, genera SQL con `mysqldump --single-transaction`, copia adjuntos, marca `COMPLETE` y reinicia la aplicación. Escribe SQL como bytes, sin tuberías de PowerShell que alteren codificación. Guarda `.env` por separado, protegido, junto con la versión de código correspondiente. Copia los respaldos fuera del disco del servidor.

Restauración explícita, **reemplaza los datos de la instalación**:

```sh
python scripts/backup.py restore backups/AAAAMMDD-HHMMSS --confirm-replace
```

Haz antes un respaldo del estado actual. Si hay un error, la aplicación permanece detenida para evitar trabajar sobre una restauración incompleta. Tras restaurar comprueba cuentas, número de casos, saldos y archivos. Nunca restaure una base nueva con código anterior incompatible.

Para MySQL portátil, detén Python (Ctrl+C) y utiliza:

```powershell
..\.venv\Scripts\python.exe scripts\portable_backup.py backup --app-stopped
..\.venv\Scripts\python.exe scripts\portable_backup.py restore backups\AAAAMMDD-HHMMSS --app-stopped --confirm-replace
```

El respaldo nativo incluye SQL y adjuntos. No copies el directorio activo de MySQL como único respaldo.

## Datos ficticios opcionales

En una instalación separada sin clientes ni casos:

```sh
docker compose exec app python -m app.cli demo
```

Exige escribir `DEMOSTRACION`, marca nombres con DEMO y no crea credenciales reutilizables. No se ejecuta al iniciar. Para pruebas automatizadas se usa exclusivamente una base terminada en `_test`.

## Pruebas

La base `comgraf_test` debe existir y el usuario configurado necesita permisos solo sobre las bases de desarrollo y prueba. **Los tests limpian las tablas de la base `_test`; nunca uses una base con datos que quieras conservar.**

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
..\.venv\Scripts\python.exe -m tests.seed_e2e
$env:MYSQL_DATABASE='comgraf_test'
$env:UPLOAD_DIR='../.runtime/test-uploads'
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

En otra terminal:

```powershell
cd frontend
node node_modules/playwright/cli.js install chromium
npm.cmd run test:e2e
```

Linux: sustituir rutas de `.venv\Scripts\python.exe` por `.venv/bin/python` y usar `MYSQL_DATABASE=comgraf_test UPLOAD_DIR=../.runtime/test-uploads` antes del comando uvicorn. Las credenciales E2E son aleatorias, locales, temporales y se guardan en `.runtime/e2e.json`, excluido de Git. Ejecuta pruebas backend antes del sembrado E2E, porque las pruebas backend limpian esa base.

## Documentación y límites

- [Arquitectura, permisos, cálculos y denominadores](docs/ARQUITECTURA.md).
- [Mapa del código, invariantes y guía de intervención](docs/MANTENIMIENTO.md).
- [Referencias inspeccionadas y procedencia](docs/REFERENCIAS.md).
- [Verificación y limitaciones reales](docs/VERIFICACION.md).

Control operativo de cobros; no reemplaza contabilidad ni un sistema fiscal. No integra correo/WhatsApp. No implementa SaaS, multitenencia ni API pública. No se ha publicado en Internet.

Las migraciones se aplican en orden con `alembic upgrade head`; la migración 002 normaliza únicamente equivalencias inequívocas de Contado/Crédito y agrega la instantánea de especificaciones. No altera importes, saldos, pagos ni fechas históricas.

Las nuevas operaciones se registran en HNL. Los importes históricos en USD se conservan, sin conversión automática; una cotización antigua en USD debe revisarse con precios HNL antes de generar una orden.

Las migraciones 003/004 incorporan el flujo de atención y producción descrito en [ARQUITECTURA](docs/ARQUITECTURA.md) y vuelven a abrir atenciones que ya tenían seguimientos vencidos. Antes de actualizar, respalda la base y adjuntos; aplica con `alembic upgrade head` (Compose lo hace al arrancar). Preparación y Listo para entregar se clasifican inequívocamente en Nuevo y Listo. Etapas intermedias ambiguas conservan el original en `legacy_stage`, quedan pendientes de clasificación manual y no reciben tiempos históricos inventados. Las migraciones no eliminan expedientes, entregas ni pagos. Sus downgrades se rechazan porque restaurar estados anteriores exigiría adivinar: para volver atrás restaura conjuntamente el respaldo de base y archivos.

Los trabajos que llegan a Listo para entregar reciben un seguimiento de recogida a siete dias. Al registrar el contacto puede programarse otro recordatorio semanal; una entrega parcial lo conserva y una entrega completa resuelve solo ese seguimiento. Sin anticipo, con anticipo y pagado se distinguen por los movimientos reales.

Cerrar atencion no completa el expediente. Completar caso es manual y solo se habilita cuando se resuelven entregas, cargos, devoluciones, tareas y comunicaciones pendientes; el expediente completo permanece en Historial y en el perfil del cliente. Reactivar conserva todos los movimientos y etapas. Las llamadas o visitas se resumen como notas internas; las entradas historicas siguen en la bitacora. Las plantillas solo aceleran la escritura y no crean tareas, pagos, entregas ni aprobaciones.
