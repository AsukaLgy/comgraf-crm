# Verificación de Comgraf

Comprobación realizada localmente el 16 de octubre de 2026 con MySQL Community Server 8.4.11 y una base independiente `comgraf_test`. No se usaron datos E2E en la base operativa `comgraf`.

## Resultado

- `npm.cmd run build`: compilación Vite correcta (1809 módulos transformados).
- `python -m pytest -p no:cacheprovider tests -q`: 4 pruebas backend aprobadas contra MySQL; cubren reglas de negocio, permisos y concurrencia.
- `npm.cmd run test:e2e -- --reporter=list`: 6 recorridos de navegador aprobados contra FastAPI y MySQL reales. Incluyen consulta cerrada sin producción, notas/seguimiento, borradores, formulario conservado ante error, filtros al volver, búsqueda e historial de cliente, cotización aceptada, conversión del mismo expediente, aprobación de arte, etapas, bloqueo, retrabajo, entrega parcial, cierre de atención, anticipo y saldo.
- Alembic: base operativa en `001_roles (head)` y `alembic check` informó que no hay operaciones pendientes.
- `scripts/verify_restart.py`: reinicio real de MySQL y del servidor HTTP; diez expedientes conservaron su contenido y la sesión autenticada continuó válida. Se hizo una consulta de borradores determinista por identificador al detectar que MySQL podía devolverlos en distinto orden.
- Conversión protegida contra una segunda conversión; toma, conversión, pagos y movimientos asociados usan versión/clave idempotente y transacción para resolver reintentos y concurrencia.

Las pruebas de backend vacían las tablas de la base cuyo nombre termina en `_test`. Se deben ejecutar antes de `tests.seed_e2e`; nunca apuntarlas a una base con datos que se deseen conservar.

## Límites comprobados

- Docker no está instalado en este equipo, así que no pude verificar `docker compose build/up`. La aplicación y la base sí se ejecutaron con la instalación portable local de MySQL, fuera de Compose.
- Correo y WhatsApp tienen borradores y registro manual; no se conectan ni envían mensajes a proveedores.
- Las interacciones externas son manuales. Por eso los indicadores de primera respuesta humana, conversión y seguimiento dependen de los registros que capture el equipo. El sistema identifica el alcance operativo y no sustituye contabilidad ni facturación fiscal.
- No se creó un usuario administrador ni se configuró una contraseña reutilizable. Para iniciar una instalación vacía, seguir el comando interactivo `create-admin` del README.
- La interfaz local del equipo de desarrollo escucha en `127.0.0.1`. Para dar acceso a estaciones en la LAN, usar Compose y configurar en el `.env` una contraseña exclusiva de la instalación; mantener restringidos el equipo y la base de datos a la red local.
- La referencia extraída del prototipo está accesible. Estaba disponible el repositorio Frappe CRM, pero no una copia completa del código de Frappe Framework. Comgraf implementa patrones propios y no incorpora código Frappe.
