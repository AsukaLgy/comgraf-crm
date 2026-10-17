# Comgraf: decisiones y contrato operativo

## Aplicación modular

React 19 + JavaScript + Vite. FastAPI sirve los archivos compilados y `/api` desde el mismo origen. SQLAlchemy usa PyMySQL y exclusivamente MySQL/InnoDB. Alembic contiene un esquema inicial explícito y una migración de roles. Node solo compila la interfaz.

`backend/app/routers` contiene transporte HTTP; `schemas.py`, validación de entrada; `services.py`, transacciones y reglas; `repository.py`, consultas y representación según permisos; `models.py`, relaciones persistentes. Calendario, cobros y comunicaciones no son servicios independientes.

## Expediente único

`cases.id` es estable; número visible `CG-000001`; enlace `/cases/1`. Cliente y contacto son claves foráneas. `productions.case_id` es simultáneamente clave primaria y referencia al caso. Convertir nunca crea otro expediente ni modifica notas, cliente o responsable de atención.

Estados independientes:

- Atención: Nuevo, En atención, Esperando al cliente, Esperando gestión interna, Cerrado.
- Producción: Preparación, Programado, En producción, Calidad, Listo para entregar, Entregado.
- Entrega: Pendiente, Parcial, Completa; calculada por cantidades de cada partida.
- Cobro: Pendiente, Vencido, Pagado, A favor; calculado a partir de movimientos.

Cancelación es un evento excepcional con motivo y fecha. No elimina obligaciones, entregas ni pagos. Cerrar atención requiere motivo, resumen y completar sus tareas pendientes; no cierra producción ni cobranza. Casos con procesos independientes se vinculan mediante `related_case_id`.

## Concurrencia y reintentos

Operaciones críticas bloquean el caso con `SELECT ... FOR UPDATE`, comprueban versión y registran evento y clave idempotente en la misma transacción. La clave se asocia a usuario, acción y hash del contenido; reutilizarla para otra operación da conflicto. Las conversiones tienen además unicidad estructural por caso. Toma de casos bloquea el trabajador para hacer cumplir su capacidad incluso tomando casos distintos simultáneamente.

Se usa aislamiento READ COMMITTED para que la segunda transacción vea el resultado confirmado de la primera. Errores 409 conservan el formulario, actualizan el expediente y permiten revisar antes de guardar. No hay actualizaciones financieras destructivas: un pago se corrige mediante una reversión enlazada, única y con motivo.

## Cotizaciones y producción

Las cotizaciones guardan versiones inmutables en HNL y registran la decisión de cada versión. Una cotización histórica en USD permanece en USD y debe revisarse en una nueva versión HNL antes de convertirla; nunca se calcula una tasa implícita.

Generar una orden amplía el mismo expediente y la deja activa en Preparación. No hay autorización posterior ni requisitos de arte aprobado, anticipo o responsable de taller para avanzar. Se guarda una instantánea inicial de partidas en specs_snapshot; adjuntos y evidencia explícita siguen disponibles sin representar una aprobación ficticia. La producción usa solo etapas principales, validación secuencial, bloqueos y motivos para retroceder. Programar registra fechas anteriores y nuevas, usuario y hora sin exigir un motivo. Se conserva el compromiso original. Las columnas antiguas de autorización, subetapas y responsables por área se mantienen sin comportamiento activo para proteger expedientes existentes; no se eliminan datos históricos.

## Dinero y tiempo

Importes DECIMAL(14,2), cantidades DECIMAL(14,3); dimensiones decimales serializadas como cadenas en JSON validado. Total = suma de cantidad × precio unitario, redondeo decimal HALF_UP al total. Las operaciones nuevas usan exclusivamente HNL, incluidas cotizaciones, producción y movimientos financieros. Los registros históricos USD conservan su moneda y no admiten nuevos pagos o ajustes HNL; los pagos históricos no se convierten ni se alteran. No hay conversión de divisas ni mezcla de monedas en KPI.

Saldo = suma de movimientos con signo: obligación y reversión de pago positivos; anticipos/abonos negativos; ajustes firmados. Saldo vencido: cargos cuyo vencimiento es anterior al día local menos créditos disponibles, imputados primero a deuda vencida, limitado entre cero y saldo. Una reversión mantiene el vencimiento original. No hay interés, impuestos fiscales ni asientos contables.

Instantes UTC en MySQL; presentación y límites de día en `APP_TIMEZONE` (America/Tegucigalpa). Fechas solicitadas, compromisos y vencimientos son fechas de calendario. Las revisiones de programación guardan fecha anterior y nueva, usuario y hora. Se conserva el compromiso original para medir puntualidad.

## Acceso

| Rol inicial | Lectura | Acciones |
|---|---|---|
| Administrador | Todo | Todo, usuarios y roles |
| Coordinador | Todo | Asignación, reapertura, atención, taller, cobros, ajustes |
| Atención | Todos los casos y clientes, importes comerciales | Clientes; tomar; atender/cotizar/convertir sus casos |
| Producción | Casos en producción | Programación, etapas principales, archivos y entregas; sin saldos, precios ni eventos financieros |
| Cobranza | Casos en producción e importes | Anticipos, abonos e indicadores; sin ajustes ni producción |

Las capacidades de roles salvo Administrador son configurables. Varios roles acumulan permisos. La interfaz oculta acciones según permisos, pero cada endpoint vuelve a autorizarlas. Las asignaciones son responsabilidades operativas, no aislamiento entre empresas.

Contraseñas Argon2id. Sesiones aleatorias, almacenadas como SHA256, cookie HttpOnly/SameSite Strict, revocables al salir o cambiar contraseña. Cada petición mutadora exige cabecera propia y origen coincidente; no se habilita CORS. Adjuntos identificados por UUID, descargados con autenticación, sin ruta pública. PDF/PNG/JPEG/TXT con límite, extensión y firma/UTF-8 comprobadas; no hay antivirus integrado.

## Indicadores

La pantalla explica sus denominadores: conversión de casos sobre todos los casos; aceptación sobre versiones; cotizaciones convertidas sobre versiones aceptadas; puntualidad sobre entregas completas contra compromiso original. Tiempo de primera respuesta cuenta primera interacción manual saliente o visita, no nota interna ni asignación. Tiempo por etapa incluye permanencia y bloqueos; tiempo bloqueado también se reporta aparte. Retrabajo cuenta retrocesos de etapas principales con motivo.

## Canales futuros

`integrations.py` define contrato de proveedor y bandeja con unicidad proveedor/ID externo. Los eventos ambiguos permanecen sin clasificar; ningún teléfono determina automáticamente una asociación. No se exponen webhooks ni se habilitan envíos. Una integración futura necesita verificación de firma, asociación manual, permisos, pruebas y gestión de credenciales antes de activarse.

## Límites proporcionados al equipo local

Las bandejas se calculan sobre el conjunto visible y todavía no tienen paginación en servidor; para grandes volúmenes hay que paginar y agregar consultas. Las actualizaciones del expediente se hacen tras guardar o con Actualizar; los contadores se refrescan cada 30 segundos. No se implementó WebSocket, sincronización sin conexión, multitenencia, fiscalidad ni publicación en Internet.
