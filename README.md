# Comgraf · Comunicación Gráfica

CRM local para imprenta: una solicitud desde atención hasta producción, entrega y cobranza. Dentro de cada solicitud se genera su orden de producción. React/JavaScript, FastAPI, SQLAlchemy, Alembic y **MySQL real**. Interfaz en español, sin Frappe, Bench, CDN ni servicios cloud obligatorios.

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
sudo apt install mkcert libnss3-tools
mkcert -install
scripts/setup-https.sh localhost
docker compose up -d --build
docker compose exec app python -m app.cli create-admin
```

Abrir **https://localhost**. La URL HTTP local redirige a HTTPS. Si ya tienes `.env`, `configure.py` se niega a sobrescribirlo. Ajusta sus variables; Compose fuerza host `db`, puerto `3306` y rutas internas, por lo que puede utilizar las claves existentes. Los volúmenes de Compose son independientes de la base portátil; no se importan datos automáticamente entre instalaciones.

MySQL no publica ningún puerto. Solo Python habla con MySQL; React usa `/api`. La imagen sirve recursos compilados locales. Los contenedores se comunican por una red interna. La aplicación se ejecuta como usuario sin privilegios. Los volúmenes conservan MySQL y adjuntos al recrear servicios; **no uses `docker compose down -v` para actualizar**.

### HTTPS para toda la aplicación en la red local

El proxy Caddy protege toda la interfaz y API, no solo la pantalla del escáner. No abre una ruta pública ni requiere servicios de certificados externos. Reserva una IP privada fija para este equipo; en el ejemplo se usa `192.168.1.20`.

1. Instala `mkcert` y sus herramientas NSS siguiendo [la guía oficial](https://github.com/FiloSottile/mkcert#installation), instala su CA local con `mkcert -install` y genera el certificado:

   ```sh
   scripts/setup-https.sh 192.168.1.20
   ```

   En `.env`, establece `HTTPS_HOST=192.168.1.20`, `HTTPS_BIND=192.168.1.20`, `HTTP_BIND=192.168.1.20`, `HTTP_REDIRECT_PORT=8080` y `COOKIE_SECURE=true`. Conserva `HTTPS_PORT=443`. Usa la misma IP reservada en el certificado y en estos valores.
2. Permite TCP 443 y el puerto HTTP de redirección únicamente desde la subred de la imprenta. El puerto 443 sirve el CRM; el HTTP antiguo solo redirige a HTTPS. No abras puertos en el router ni publiques dominios o túneles a Internet. Reinicia con `docker compose up -d`.
3. Abre **`https://192.168.1.20`**. Los datos de acceso y la sesión viajan por HTTPS; `COOKIE_SECURE=true` evita enviar la cookie de sesión por HTTP.
4. Para cada teléfono o computadora que deba confiar en el certificado, transfiere `.runtime/camera-https/rootCA.crt` por un medio local confiable e instala ese archivo como certificado de CA. En Android suele estar en **Ajustes → Seguridad → Cifrado y credenciales → Instalar certificado de CA**; los nombres varían por fabricante. Android puede pedir un PIN o patrón. Comprueba que el navegador no muestre advertencias del certificado.

**No compartas `rootCA-key.pem`**: esa clave privada puede emitir certificados para cualquier sitio. La CA pública `rootCA.pem` sí se instala en los dispositivos. Sin confiar en esa CA, el navegador mostrará una advertencia y no habilitará la cámara. En dispositivos de escaneo, inicia sesión con cuentas individuales asignadas al rol **Escáner de encuadernación**.

La zona de operación se configura con `APP_TIMEZONE`, inicialmente `America/Tegucigalpa`. No se exponen MySQL ni el puerto interno de la aplicación a la LAN.

## Desarrollo sin Docker

Requisitos: Python 3.14, Node 24, MySQL 8.4. Crea una base UTF-8 y un usuario restringido a esa base. Copia `.env.example` a `.env` y configura host/puerto/credenciales, `UPLOAD_DIR=../uploads`, `STATIC_DIR=../frontend/dist`. Como el servidor de desarrollo se abre por HTTP, establece `COOKIE_SECURE=false` solo en ese entorno; Docker conserva `true` detrás del proxy HTTPS.

Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
npm.cmd --prefix frontend ci --ignore-scripts
npm.cmd --prefix frontend run build
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.cli create-admin
$env:APP_HOST = '127.0.0.1'; $env:APP_WORKERS = '1'
..\.venv\Scripts\python.exe -m app.start
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
APP_HOST=127.0.0.1 APP_WORKERS=1 ../.venv/bin/python -m app.start
```

Para desarrollo de React, en otra terminal: `npm --prefix frontend run dev`. Vite reenvía `/api` a `127.0.0.1:8000` conservando el origen. Para uso normal utiliza la interfaz compilada servida por Python.

## Recorrido de trabajo

1. Crear cliente provisional (basta un nombre); agregar contactos y datos comerciales cuando se conozcan.
2. Crear una solicitud y tomarla desde el pool. Registrar notas internas con plantillas; resumir llamadas, visitas, acuerdos y comentarios en una sola bitácora; programar seguimientos. Cerrar atención retira la solicitud de acción inmediata, pero no cierra el trabajo ni la cobranza.
3. Preparar cotización con partidas y vigencia. Registrar aceptación/rechazo de una versión. Generar una orden de producción reutiliza esa versión y conserva la solicitud.
4. La orden recorre Nuevo → Aprobación → Impresión → Encuadernación → Listo para entregar. Aprobación es revisión operativa con el cliente, sin autorización interna separada.
5. Programar el inicio y compromiso de entrega de forma independiente; el evento conserva fechas anteriores y nuevas, usuario y hora. El avance normal no exige motivo; bloqueos y retrocesos sí.
6. Registrar cantidades entregadas por partida y evidencia. Una entrega parcial conserva pendientes; solo las cantidades completas muestran Entregado y detienen el tiempo activo.
7. Registrar anticipos/abonos; corregir mediante reversión o ajuste autorizado. La entrega permite, con permiso financiero, incluir un abono en la misma operación o liquidar el saldo mostrado; entregar nunca crea un pago. Los saldos siguen visibles con atención cerrada.

Si se anula un trabajo, el usuario registra el motivo y elige **Sin cargo** o un **importe final acordado** (total definitivo del trabajo, no cargo adicional). El ajuste conserva obligación y pagos anteriores. Si el total pagado supera el importe final, queda saldo a favor; una devolución requiere que un usuario con permiso financiero confirme fecha/importe/referencia después de realizarla. Una persona que puede detener producción pero no ajustar cobros deja la resolución en la bandeja **Resolución de anulación**. Las órdenes históricas anuladas también requieren revisión explícita.

En **Configuración → Correo de la empresa**, un administrador conecta Yahoo con la dirección y una contraseña de aplicación. Comgraf comprueba IMAP y SMTP por separado. El trabajador consulta INBOX aproximadamente cada minuto, importa solo los mensajes posteriores a la activación y no marca, mueve ni borra los mensajes de Yahoo. Los correos nuevos se vinculan por hilo o remitente inequívoco; los casos ambiguos y las respuestas a solicitudes completadas esperan clasificación manual.

Desde una solicitud se pueden guardar borradores, elegir destinatario y adjuntos del expediente, y enviar correo. El CRM muestra estados de cola, aceptación SMTP, error o resultado incierto. “Aceptado por Yahoo” no significa entregado ni leído. Los reintentos con resultado incierto requieren revisar el buzón y confirmarlos manualmente. WhatsApp sigue siendo manual. Los borradores se guardan por solicitud/canal/usuario en MySQL y el navegador conserva un respaldo provisional de la escritura.

Las migraciones aditivas `010_yahoo_mail` y `011_mail_retry_idempotency` agregan configuración cifrada, progreso IMAP, mensajes, adjuntos y salida idempotente; no reconstruyen la base. Antes de actualizar, respalda MySQL y adjuntos. En Compose, `app.start` aplica las migraciones y levanta un trabajador coordinado por bloqueo MySQL para evitar sincronizadores duplicados. La integración necesita una conexión saliente a Yahoo; el CRM local sigue abriendo si Internet no está disponible. La conexión real se valida al configurar el buzón desde la pantalla de administración.

## Actualización

En Windows local, respalda primero SQL y adjuntos, detén Comgraf con Ctrl+C en la terminal `start-local.ps1` y ejecuta desde la raíz:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\update-local.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

El actualizador comprueba la configuración, instala dependencias solo si cambiaron sus archivos de bloqueo (o con `-ForceDependencies`), compila React y aplica Alembic. Si algo falla, se detiene. Conserva `.env`, MySQL y los adjuntos; no borres `.runtime`.

`/api/version` identifica versión de producto, build y contrato HTTP. Builds distintos no implican incompatibilidad por sí solos. Las operaciones de escritura exigen contrato HTTP 4. Ante incompatibilidad, ejecuta la actualización anterior y reinicia Python. El HTML exige revalidación y los assets conservan nombres versionados.

Las migraciones `005_commercial_cancellation` y `006_cancelled_stage_snapshot` agregan trazabilidad y marcan anulaciones existentes para revisión; no cambian pagos, saldos ni etapas sin clasificar, y no se editan después de aplicadas. Las anulaciones antiguas requieren revisión explícita; no se ponen en cero automáticamente.

Con Docker, después del respaldo: `docker compose build app` y `docker compose up -d app`. El arranque aplica Alembic antes de servir solicitudes e inicia el trabajador Yahoo separado del servidor web. Comprueba `docker compose ps`, `/api/health`, login, expediente y descarga de archivo. Para instalar cambios sin Docker, usa el script Windows indicado arriba o ejecuta manualmente los pasos de Desarrollo sin Docker. Crea revisiones Alembic nuevas; no edites migraciones aplicadas. `alembic check` detecta diferencias entre modelos y esquema.

## Respaldo y restauración de Compose

```sh
python scripts/backup.py backup
```

Detiene brevemente la aplicación para congelar altas de archivos, genera SQL con `mysqldump --single-transaction`, copia adjuntos, marca `COMPLETE` y reinicia la aplicación. Escribe SQL como bytes, sin tuberías de PowerShell que alteren codificación. Guarda `.env` por separado, protegido, junto con la versión de código correspondiente. Copia los respaldos fuera del disco del servidor.

La contraseña Yahoo se cifra con una clave Fernet persistente que queda fuera de MySQL y Git. Para restaurar esos mensajes y poder descifrar la cuenta, respalda **también** la clave; el script SQL/adjuntos no la incluye. En Compose, cópiala con `docker compose cp --archive app:/data/mail-secrets/fernet.key /ruta/privada/fernet.key` y guárdala cifrada en un medio distinto al servidor. En la instalación local se encuentra en `.runtime/mail-secrets/fernet.key`. Restaura la clave en el mismo directorio antes de iniciar Comgraf con la base respaldada. No la regeneres ni la compartas junto a una copia accesible de la base.

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

## Regularización controlada de solicitudes históricas

Después de actualizar la aplicación, primero genera la vista previa:

```sh
docker compose exec app python -m app.cli completions-preview
```

La lista incluye únicamente solicitudes activas con atención abierta, orden entregada por completo, obligación y saldo neto verificables, y sin tareas, mensajes, bloqueos ni resoluciones económicas pendientes. Revisa cada número y cliente antes de ejecutar:

```sh
docker compose exec app python -m app.cli regularize-completions --confirm REGULARIZAR-COMPLETADAS
```

El comando vuelve a comprobar la elegibilidad dentro de la transacción, registra una finalización con fecha actual y el origen `historical_regularization`, y no cambia entregas, movimientos, propietario ni fechas históricas. Repetirlo no duplica eventos. No hay migración de base de datos.

## Documentación y límites

- [Arquitectura, permisos, cálculos y denominadores](docs/ARQUITECTURA.md).
- [Mapa del código, invariantes y guía de intervención](docs/MANTENIMIENTO.md).
- [Referencias inspeccionadas y procedencia](docs/REFERENCIAS.md).
- [Verificación y limitaciones reales](docs/VERIFICACION.md).

Control operativo de cobros; no reemplaza contabilidad ni un sistema fiscal. Integra recepción y envío de correo Yahoo; WhatsApp permanece manual. No implementa SaaS, multitenencia ni API pública. No se ha publicado en Internet.

Las migraciones se aplican en orden con `alembic upgrade head`; la migración 002 normaliza únicamente equivalencias inequívocas de Contado/Crédito y agrega la instantánea de especificaciones. No altera importes, saldos, pagos ni fechas históricas.

Las nuevas operaciones se registran en HNL. Los importes históricos en USD se conservan, sin conversión automática; una cotización antigua en USD debe revisarse con precios HNL antes de generar una orden.

Las migraciones 003/004 incorporan el flujo de atención y producción descrito en [ARQUITECTURA](docs/ARQUITECTURA.md) y vuelven a abrir atenciones que ya tenían seguimientos vencidos. Antes de actualizar, respalda la base y adjuntos; aplica con `alembic upgrade head` (Compose lo hace al arrancar). Preparación y Listo para entregar se clasifican inequívocamente en Nuevo y Listo. Etapas intermedias ambiguas conservan el original en `legacy_stage`, quedan pendientes de clasificación manual y no reciben tiempos históricos inventados. Las migraciones no eliminan expedientes, entregas ni pagos. Sus downgrades se rechazan porque restaurar estados anteriores exigiría adivinar: para volver atrás restaura conjuntamente el respaldo de base y archivos.

La migración 008 convierte la relación de órdenes a solicitudes en uno-a-varios y añade el vínculo explícito de partidas, movimientos y seguimientos a la orden. Conserva la clave histórica como ID de OP y no reasigna etapas ni movimientos. Respalda base y adjuntos antes de actualizar; Compose aplica `alembic upgrade head` al iniciar. Verifica `/api/health`, una solicitud histórica y una orden nueva antes de habilitar el uso normal. El downgrade se rechaza porque volver a una sola orden por solicitud descartaría las órdenes agregadas.

Los trabajos que llegan a Listo para entregar reciben un seguimiento de recogida a siete dias. Al registrar el contacto puede programarse otro recordatorio semanal; una entrega parcial lo conserva y una entrega completa resuelve solo ese seguimiento. Sin anticipo, con anticipo y pagado se distinguen por los movimientos reales.

Cerrar atención no completa la solicitud. Las consultas y cotizaciones sin producción se completan manualmente. Una solicitud con producción se completa al resolver la entrega, el saldo y las demás gestiones pendientes; después sigue disponible en el Historial y en el perfil del cliente. Reactivarla conserva todos los movimientos y etapas. Las llamadas o visitas se resumen como notas internas; las entradas históricas siguen en la bitácora. Las plantillas solo aceleran la escritura y no crean tareas, pagos, entregas ni aprobaciones.
