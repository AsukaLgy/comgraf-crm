# Guía de mantenimiento de Comgraf

Esta guía indica dónde cambiar cada comportamiento y qué invariantes debe conservar una intervención futura. Los nombres de campos históricos que ya no participan en el flujo activo están marcados en los modelos; no deben eliminarse sin migración y revisión de respaldos.

## Mapa del código

| Área | Punto de entrada | Responsabilidad |
| --- | --- | --- |
| Interfaz, rutas y sesión | `frontend/src/App.jsx` | Shell, navegación, lectura de sesión y permisos visibles. |
| Vocabulario de formularios | `frontend/src/domain.js` | Opciones compartidas de etapas, estados, prioridades, productos y condición de pago. Espejo para la interfaz; el servidor valida. |
| Acceso HTTP y formato | `frontend/src/api.js` | Fetch, errores, moneda, cantidades, fechas y conversiones de zona horaria. |
| Componentes comunes | `frontend/src/components.jsx` | Paneles, campos, diálogos, formularios y operaciones idempotentes. |
| Bandejas | `frontend/src/Inbox.jsx` | Pool, filtros, vistas guardadas y búsqueda de expedientes. |
| Tabla compartida | `frontend/src/CaseTable.jsx` | Presentación de casos en bandejas e historial de cliente. |
| Alta y selección de cliente | `frontend/src/NewCase.jsx` | Nuevo caso, autocompletado y creación explícita de cliente/contacto. |
| Coordinación del expediente | `frontend/src/CasePage.jsx` | Carga, mutaciones y composición del expediente. Mantenerlo como coordinador y no concentrar presentación de todos los dominios aquí. |
| Atención y encabezado | `frontend/src/CaseAttention.jsx` | Abrir/cerrar atención, propietario e información de acción pendiente. |
| Actividad | `frontend/src/CaseActivity.jsx` | Canales manuales y bitácora. Las notas no abren atención; interacciones entrantes sí. |
| Etapas | `frontend/src/ProductionStages.jsx` | Barra de cinco etapas, clasificación histórica, programación, bloqueos y retrocesos. |
| Entrega y finanzas | `frontend/src/DeliveryFinance.jsx` | Registro parcial/completo y abono transaccional opcional, saldos y permisos financieros. |
| Anulación comercial | `frontend/src/CasePage.jsx`, `DeliveryFinance.jsx`; `backend/app/services.py`, `repository.py` | Detener producción conservando entregas y movimientos; ajustar cargo final, resolver devoluciones o dejar revisión financiera pendiente. |
| Ficha imprimible | `frontend/src/CasePrintSheet.jsx` | Salida de taller y especificaciones del expediente. |
| Productos y conversión | `frontend/src/ProductForm.jsx` | Partidas, especificaciones, cotizaciones y ampliación del mismo caso a producción. |
| Correo Yahoo | `frontend/src/Settings.jsx`, `Composer.jsx`; `backend/app/routers/mail.py`, `mail_transport.py`, `mail_service.py`, `mail_security.py`, `mail_worker.py` | Configuración cifrada, cliente IMAP/SMTP, asociación de hilos, bandeja de salida idempotente y trabajador coordinado por MySQL. |
| Comunicación manual | `frontend/src/Composer.jsx` | Notas y WhatsApp manuales; las notas internas nunca pasan a la bandeja de salida del correo. |
| Endpoints | `backend/app/routers/` | HTTP, permisos de entrada, dependencias de sesión y traducción a servicios. |
| Respuesta del expediente | `backend/app/routers/common.py` | Proyección común posterior a una mutación; evita que un router importe otro. |
| Contratos | `backend/app/schemas.py` | Validación Pydantic. Rechaza campos extra y normaliza entradas. |
| Reglas de negocio | `backend/app/services.py` | Transiciones, permisos de dominio, idempotencia, concurrencia y bitácora. |
| Consultas y proyecciones | `backend/app/repository.py` | Visibilidad, saldos, estados derivados, serialización y bandejas. |
| Persistencia | `backend/app/models.py` | Tablas, claves, índices y campos heredados. Cambios de esquema requieren Alembic. |
| Roles y autenticación | `backend/app/security.py`, `backend/app/routers/auth.py` | Hash de contraseñas, sesión HTTP y autorización de servidor. |
| Esquema de base | `backend/migrations/versions/` | Cambios incrementales. Las revisiones aplicadas son inmutables. |

## Invariantes de operación

- Un caso conserva su ID durante toda su vida; producción se añade al caso, no crea otro expediente.
- El responsable operativo único es `Case.owner_id`. `Case.collection_owner_id` y `Production.owner_id` son columnas históricas y solo se conservan para compatibilidad y visibilidad heredada.
- `Production.stage` usa NEW, APPROVAL, PRINTING, WORKSHOP, READY. Las transiciones permitidas están expresadas como mapas de códigos, no por posiciones de una lista. `NULL` junto con `classification_required` significa etapa heredada ambigua por clasificar; no es una sexta etapa.
- `Delivery` calcula cantidades acumuladas; el estado Completa solo procede cuando cada partida está entregada. La entrega completa detiene tiempo productivo sin borrar tiempos ni movimientos.
- Atención es solo `Abierto`/`Cerrado`; no usar el cierre como condición general de permiso o bloqueo. El predicado de acción inmediata es único en repositorio y servicio de recuperación.
- El propietario único es `Case.owner_id`. Columnas de responsable por área y datos de subetapa permanecen como históricos, sin reglas activas.
- Los campos `Production.authorized_at`, `approval_evidence`, `approved_snapshot` y `art_version` conservan datos del flujo anterior. No deben volver a imponer una autorización. `approved_snapshot` sigue siendo una fuente de impresión histórica si no existe `specs_snapshot`.
- Todas las operaciones financieras nuevas son HNL. Los registros USD históricos no se convierten ni se mezclan con HNL.
- `Case.version` y la clave idempotente protegen operaciones frente a doble envío y concurrencia. No quitar ambos controles de una mutación.
- La bitácora refleja el evento real. No inventar aprobaciones, envíos externos, pagos ni entregas.
- Una anulación conserva la obligación y movimientos anteriores; el ajuste de anulación cambia el cargo neto al total acordado. La devolución se registra como movimiento positivo solo cuando el usuario confirma que realmente la realizó.
- Detener producción requiere permiso `production`; una persona sin permisos `finance`/`adjust` puede anular y dejar revisión pendiente. Puede ver el estado neutral de revisión, pero no los importes ni acceder a la bandeja financiera.
- `cancellation_review_pending` impide presentar una obligación heredada o sin resolución como deuda exigible. Resolverla requiere permiso financiero y de ajuste; registrar devolución requiere permiso financiero e idempotencia.
- Las órdenes anuladas antes de la resolución comercial quedan para revisión por migración. No se les añade un ajuste ni se cambia el saldo automáticamente.
- La autorización del servidor es obligatoria aunque la interfaz oculte una acción. Las reglas críticas no se duplican solo como validación React.
- Al calcular saldos, no reinterpretar condiciones históricas de pago ni cambiar importes o vencimientos sin una acción auditada y una decisión comercial.

## Cómo cambiar una operación

1. Actualizar el esquema Pydantic y la regla del servicio. Añadir validación de relaciones y permisos en el backend.
2. Actualizar la ruta si cambia el contrato HTTP; conservar la gestión común de transacciones en `get_db`.
3. Actualizar el formulario, transformación de payload y estados de error correspondientes en frontend. Mantener entradas visibles al recibir error o conflicto.
4. Si cambia la persistencia, crear una migración nueva y reversible cuando sea seguro. No editar una migración aplicada, borrar la base, recrear IDs ni reescribir saldos históricos.
5. Añadir pruebas backend del contrato, permisos, historial y reintentos; ajustar o añadir un recorrido E2E para la interacción visible.
6. Ejecutar `alembic check`, `ruff check app tests`, pruebas backend, `npm run lint`, `npm run format:check`, compilación y Playwright contra una base cuyo nombre termine en `_test`.

## Convenciones de lectura

- Los encabezados `---- Dominio ----` agrupan funciones dentro de módulos. Si una sección crece, extraer un módulo del mismo dominio y mantener imports explícitos; evitar nuevos comodines.
- Mantener una función por transición de negocio y dejar en routers solo el contrato HTTP.
- Los nombres de las opciones visibles están centralizados en `frontend/src/domain.js`. Los esquemas Python son autoridad y deben actualizarse en el mismo cambio.
- Evitar lógica temporal basada en la zona del navegador. Usar la zona que entrega el servidor y las utilidades de `frontend/src/api.js`.
- `.env`, `.runtime`, adjuntos y `.vscode` son configuración/datos locales; no son parte de cambios de producto ni deben copiarse a Git.
- Antes de una actualización local, detener Python y ejecutar `scripts/update-local.ps1`; compila frontend y aplica migraciones sin limpiar MySQL ni adjuntos. Después reiniciar con `scripts/start-local.ps1`. `/api/version` diferencia producto, build y contrato HTTP.

## Consultas de bandeja

`repository.case_query()` es la fuente común de predicados para filas y contadores. Los filtros, orden, desempate por ID y paginación se aplican en MySQL; las bandejas usan una proyección ligera sin bitácoras, adjuntos o partidas completas. Al agregar un filtro, actualizar tanto el query compartido como las pruebas de igualdad lista/contador. Verificar índices con las consultas reales antes de añadirlos; no ocultar un problema de rendimiento con caché.

## Estilo y herramientas

Desde la raíz: `npm.cmd --prefix frontend run lint`, `format:check`, `build`. En backend: `..\.venv\Scripts\python.exe -m ruff check app tests`, Alembic y pytest. La configuración está en `frontend/eslint.config.js`, `.prettierrc.json` y `backend/pyproject.toml`; la lista de paquetes de desarrollo vive en `backend/requirements-dev.txt`.

## Verificación local

Desde `backend`:

```powershell
..\.venv\Scripts\python.exe -m alembic check
..\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

Desde la raíz:

```powershell
npm.cmd --prefix frontend run build
```

Para E2E, seguir el procedimiento de `README.md`: sembrar credenciales aleatorias en `comgraf_test`, iniciar la app apuntando exclusivamente a esa base y ejecutar Playwright. La suite limpia datos de `_test`.

## Completar casos y seguimientos de recogida

`frontend/src/CaseAttention.jsx` presenta Completar/Reactivar y `frontend/src/CasePage.jsx` coordina las acciones y muestra `completion_blockers`. `backend/app/services.py` (`complete_case`, `reactivate_case`, `schedule_pickup_followup`, `complete_task`) contiene las transacciones; `backend/app/repository.py` calcula bloqueos, ciclo y proyecciones de bandeja. No dupliques las reglas en React.

Las tareas de recogida usan `Task.category == PICKUP`; el vencimiento inicial es siete dias despues de entrar a READY. No crees una segunda tarea abierta al reintentar o volver a guardar READY. Una entrega completa resuelve unicamente tareas PICKUP. Al registrar contacto se puede marcar la tarea y, explicitamente, programar el siguiente control semanal.

`frontend/src/Composer.jsx` conserva borradores antiguos de llamada/visita, pero ofrece como canales activos Notas, Correo y WhatsApp. Las plantillas solo escriben texto editable; no registran pagos, entregas, aprobaciones ni seguimientos. Las referencias estructuradas de conversion se muestran en `CaseActivity.jsx`; conserva sus permisos y el destino dentro del expediente.

La migracion `007_case_completion_pickup` agrega ciclo de vida y tipo de tarea sin completar casos historicos. Aplicala con `alembic upgrade head`; no edites revisiones ya aplicadas. No reviertas automaticamente esta revision porque las tareas PICKUP y cambios de nulabilidad pueden contener actividad real.
