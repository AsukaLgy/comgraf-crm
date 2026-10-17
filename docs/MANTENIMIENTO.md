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
| Expediente | `frontend/src/CasePage.jsx` | Acciones, bitácora, cotizaciones, estados de producción, archivos, entregas y saldo. |
| Productos y conversión | `frontend/src/ProductForm.jsx` | Partidas, especificaciones, cotizaciones y ampliación del mismo caso a producción. |
| Comunicación manual | `frontend/src/Composer.jsx` | Notas, interacciones y borradores locales/remotos; no envía mensajes externos. |
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
- La etapa de producción es la etapa principal de `Production.stage`. `Item.substage` es un dato heredado sin reglas, UI ni serialización activa.
- Los campos `Production.authorized_at`, `approval_evidence`, `approved_snapshot` y `art_version` conservan datos del flujo anterior. No deben volver a imponer una autorización. `approved_snapshot` sigue siendo una fuente de impresión histórica si no existe `specs_snapshot`.
- Todas las operaciones financieras nuevas son HNL. Los registros USD históricos no se convierten ni se mezclan con HNL.
- `Case.version` y la clave idempotente protegen operaciones frente a doble envío y concurrencia. No quitar ambos controles de una mutación.
- La bitácora refleja el evento real. No inventar aprobaciones, envíos externos, pagos ni entregas.
- La autorización del servidor es obligatoria aunque la interfaz oculte una acción. Las reglas críticas no se duplican solo como validación React.
- Al calcular saldos, no reinterpretar condiciones históricas de pago ni cambiar importes o vencimientos sin una acción auditada y una decisión comercial.

## Cómo cambiar una operación

1. Actualizar el esquema Pydantic y la regla del servicio. Añadir validación de relaciones y permisos en el backend.
2. Actualizar la ruta si cambia el contrato HTTP; conservar la gestión común de transacciones en `get_db`.
3. Actualizar el formulario, transformación de payload y estados de error correspondientes en frontend. Mantener entradas visibles al recibir error o conflicto.
4. Si cambia la persistencia, crear una migración nueva y reversible cuando sea seguro. No editar una migración aplicada, borrar la base, recrear IDs ni reescribir saldos históricos.
5. Añadir pruebas backend del contrato, permisos, historial y reintentos; ajustar o añadir un recorrido E2E para la interacción visible.
6. Ejecutar `alembic check`, pruebas backend, compilación frontend y Playwright contra una base cuyo nombre termine en `_test`.

## Convenciones de lectura

- Los encabezados `---- Dominio ----` agrupan funciones dentro de módulos. Si una sección crece, extraer un módulo del mismo dominio y mantener imports explícitos; evitar nuevos comodines.
- Mantener una función por transición de negocio y dejar en routers solo el contrato HTTP.
- Los nombres de las opciones visibles están centralizados en `frontend/src/domain.js`. Los esquemas Python son autoridad y deben actualizarse en el mismo cambio.
- Evitar lógica temporal basada en la zona del navegador. Usar la zona que entrega el servidor y las utilidades de `frontend/src/api.js`.
- `.env`, `.runtime`, adjuntos y `.vscode` son configuración/datos locales; no son parte de cambios de producto ni deben copiarse a Git.

## Límite de rendimiento conocido

`repository.cases_list()` construye resúmenes por caso y carga relaciones/cálculos dentro del ciclo. Es claro para el volumen local actual, pero el número de consultas crece con los casos listados; además, las bandejas aún no usan paginación. Si el volumen del taller crece, medir primero y después agrupar consultas de clientes, producción, tareas, entregas y ledger sin cambiar los filtros ni las reglas de visibilidad. No resolverlo ocultando conteos o limitando resultados silenciosamente.

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
