# Comgraf: decisiones y contrato operativo

## Aplicación modular

React 19 + JavaScript + Vite. FastAPI sirve los archivos compilados y `/api` desde el mismo origen. SQLAlchemy usa PyMySQL y exclusivamente MySQL/InnoDB. Alembic mantiene migraciones incrementales; revisiones 003/004 actualizan el flujo de producción y recuperan seguimientos vencidos heredados. Node solo compila la interfaz.

`backend/app/routers` contiene transporte HTTP; `schemas.py`, validación de entrada; `services.py`, transacciones y reglas; `repository.py`, consultas y representación según permisos; `models.py`, relaciones persistentes. Calendario, cobros y comunicaciones no son servicios independientes.

## Expediente único

`cases.id` es estable; número visible `CG-000001`; enlace `/cases/1`. Cliente y contacto son claves foráneas. Una solicitud puede tener varias órdenes; `productions.id` identifica cada OP y `productions.case_id` la vincula al expediente. La revisión 008 conserva como IDs las claves históricas de las órdenes, de modo que sus números OP y enlaces `/cases/{id}` existentes siguen resolviendo la orden original. Partidas, movimientos y seguimientos nuevos guardan además `production_id`; la solicitud conserva cliente, propietario, atención, ciclo de vida, notas y comunicación comunes.

Estados independientes:

- Atención: Abierto o Cerrado. El cierre no termina el trabajo ni elimina mensajes entrantes pendientes; se responde antes de cerrar.
- Orden de producción: NEW/Nuevo → APPROVAL/Aprobación → PRINTING/Impresión → WORKSHOP/Encuadernación → READY/Listo para entregar. Solo estos cinco códigos estables son etapas; programación es una fecha independiente.
- Entrega: Pendiente, Parcial, Completa; calculada por cantidades de cada partida.
- Cobro: Pendiente, Vencido, Pagado, A favor; calculado a partir de movimientos.

Cancelación es un evento excepcional con motivo y fecha. No elimina obligaciones, entregas ni pagos. Cerrar atención conserva producción, tareas futuras y cobranza; los vencimientos abiertos y mensajes entrantes pendientes requieren atención aunque el estado esté cerrado. Casos con procesos independientes se vinculan mediante `related_case_id`.

Las bandejas operativas tienen predicados compartidos entre consulta y contador: Sin asignar = activa y sin propietario; Mis solicitudes = activa y propiedad del usuario; Requieren acción = activa, visible y sin propietario, con atención abierta, mensaje entrante pendiente o seguimiento vencido; Requieren mi acción agrega propiedad del usuario. Trabajo futuro, saldo pendiente o producción activa no disparan contacto por sí solos. La carga de trabajo conserva sus criterios operativos separados.

Una producción se completa automáticamente al registrar la última entrega total solo si el libro de movimientos permite verificar saldo cero, hay obligación de la moneda de la orden y no quedan tareas, mensajes, bloqueos, cotizaciones pendientes ni resolución económica. Se vuelve a evaluar tras cada operación que puede resolver uno de esos pendientes. Consultas y cotizaciones sin producción usan finalización manual. `completions-preview` y `regularize-completions` permiten revisar y, con confirmación explícita, regularizar filas históricas equivalentes; la regularización agrega fecha actual y evento auditable sin rehacer movimientos ni cantidades.

Cada transición de atención nueva registra usuario, hora, estado anterior/nuevo y origen. La revisión 003 normalizó en lote estados activos anteriores; como esa revisión ya fue aplicada antes de esta corrección, el valor previo de esas filas no se puede recuperar del historial local. No se supone un estado original: las transiciones posteriores sí quedan trazadas.

## Concurrencia y reintentos

Operaciones críticas bloquean el caso con `SELECT ... FOR UPDATE`, comprueban versión y registran evento y clave idempotente en la misma transacción. La clave se asocia a usuario, acción y hash del contenido; reutilizarla para otra operación da conflicto. Las conversiones tienen además unicidad estructural por caso. Toma de casos bloquea el trabajador para hacer cumplir su capacidad incluso tomando casos distintos simultáneamente.

Se usa aislamiento READ COMMITTED para que la segunda transacción vea el resultado confirmado de la primera. Errores 409 conservan el formulario, actualizan el expediente y permiten revisar antes de guardar. No hay actualizaciones financieras destructivas: un pago se corrige mediante una reversión enlazada, única y con motivo.

## Cotizaciones y producción

Las cotizaciones guardan versiones inmutables en HNL y registran la decisión de cada versión. Una cotización histórica en USD permanece en USD y debe revisarse en una nueva versión HNL antes de convertirla; nunca se calcula una tasa implícita.

Cada orden contiene una o varias partidas y una etapa/agenda propias. Generar una orden nueva la deja directamente en Aprobación, registra quién la creó y conserva una instantánea de productos; no registra una transición desde Nuevo ni evidencia de aprobación. La interfaz permite mantenerla en Aprobación o iniciar Impresión como segunda operación idempotente y auditable. Aprobación es una etapa actual, no una aprobación del cliente. No hay requisito de anticipo, arte adjunto o responsable por área para avanzar. El avance normal usa transiciones explícitas consecutivas; los retrocesos y bloqueos exigen motivo. La programación conserva sus fechas anterior/nueva, usuario y hora, sin razón obligatoria; el compromiso original permanece. Las órdenes antiguas en Nuevo o con etapa ambigua conservan sus datos; seleccionar una etapa histórica requiere clasificación explícita.

El calendario distingue eventos (entrega comprometida, inicio programado y seguimiento) de la etapa actual de una orden. Las OP entregadas por completo o anuladas salen de eventos pendientes. La bandeja de producción y Listos para entregar cuentan y muestran órdenes; los filtros generales de etapa sobre solicitudes usan existencia de una OP coincidente y no duplican el expediente. La ficha imprimible y cada evento de producción llevan la OP seleccionada.

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

Las capacidades de roles salvo Administrador son configurables. Varios roles acumulan permisos. La interfaz oculta acciones según permisos, pero cada endpoint vuelve a autorizarlas. Las asignaciones son responsabilidades operativas, no aislamiento entre empresas. El límite de capacidad utiliza expedientes asignados pendientes (atención abierta, tarea pendiente, producción no entregada/no anulada o saldo por gestionar) una sola vez; el indicador de atención inmediata cuenta por separado los casos que requieren acción.

Contraseñas Argon2id. Sesiones aleatorias, almacenadas como SHA256, cookie HttpOnly/SameSite Strict, revocables al salir o cambiar contraseña. Cada petición mutadora exige cabecera propia y origen coincidente; no se habilita CORS. Adjuntos identificados por UUID, descargados con autenticación, sin ruta pública. PDF/PNG/JPEG/TXT con límite, extensión y firma/UTF-8 comprobadas; no hay antivirus integrado.

## Indicadores

La pantalla explica sus denominadores: conversión de solicitudes a órdenes de producción sobre todas las solicitudes; aceptación sobre versiones; cotizaciones convertidas sobre versiones aceptadas; puntualidad sobre entregas completas contra compromiso original. Tiempo de primera respuesta cuenta primera interacción manual saliente o visita, no nota interna ni asignación. Tiempo por etapa incluye permanencia y bloqueos; tiempo bloqueado también se reporta aparte. El indicador Reclamos conserva el conteo de retrocesos de producción registrados con motivo.

## Canales futuros

`integrations.py` define contrato de proveedor y bandeja con unicidad proveedor/ID externo. Los eventos ambiguos permanecen sin clasificar; ningún teléfono determina automáticamente una asociación. No se exponen webhooks ni se habilitan envíos. Una integración futura necesita verificación de firma, asociación manual, permisos, pruebas y gestión de credenciales antes de activarse.

## Bandejas y vencimientos

`repository.py` construye filtros compartidos para página y contador, aplica orden con desempate por ID y pagina en MySQL. Usa agregaciones y expresiones correlacionadas para saldos y existencia de tareas; las filas no cargan bitácora, adjuntos ni especificaciones. Índices de tareas vencidas, vencimientos de ledger, etapa de producción y fechas de entrega respaldan los filtros. La recuperación de seguimientos se ejecuta al iniciar y cada 60 segundos en el proceso FastAPI; un cierre concurrente usa versión para no ocultar una interacción nueva.

No se implementó WebSocket, sincronización sin conexión, multitenencia, fiscalidad ni publicación en Internet.

## Ciclo de vida y recogida

El estado de atencion y el ciclo de vida son independientes. Un caso puede seguir Activo con atencion Cerrada; Completar caso es una accion manual y atomica, y solo procede cuando no quedan trabajos sin entregar, saldos, devoluciones, revisiones de anulacion, tareas abiertas ni interacciones entrantes pendientes. El servidor devuelve los pendientes concretos y no los resuelve por su cuenta. Reactivar conserva produccion, pagos, entregas y eventos anteriores. Los registros previos quedan Activos en la migracion 007, sin archivado por inferencia.

Al entrar en Listo para entregar se crea una unica tarea PICKUP a siete dias, asignada al propietario principal si existe. La tarea se puede reprogramar; al resolver un contacto se puede crear el siguiente recordatorio semanal. Entrega parcial mantiene la tarea y la entrega completa resuelve solo las tareas PICKUP. La situacion de pago se calcula desde el ledger y nunca se deduce de Contado ni del tiempo que lleva sin recogerse.

Las notas, incluidos los resumenes de llamada o visita, son internas. Correo y WhatsApp permanecen sin configurar; su interfaz no envia mensajes. Las comunicaciones manuales historicas de llamada y visita siguen visibles. La conversion registra referencias estructuradas de OP y la version exacta COT; la interfaz enlaza ambas dentro del mismo caso y aplica sus permisos.

El contrato HTTP pasa a version 3 porque cambia el contrato de eventos y se agregan las acciones de ciclo de vida y seguimientos de recogida. Version de producto 1.2.0; actualiza frontend, aplica Alembic y reinicia FastAPI mediante el procedimiento de actualizacion.
