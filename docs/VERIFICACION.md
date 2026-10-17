# Verificación de Comgraf

Comprobación local del 17 de octubre de 2026. Backend y E2E utilizan MySQL Community Server y la base separada `comgraf_test`; la base operativa `comgraf` no se utilizó para pruebas destructivas.

## Resultado de esta ronda

- `npm.cmd --prefix frontend run build`: correcta; Vite transformó 1817 módulos.
- `npm.cmd --prefix frontend run lint`: correcta con ESLint 9.
- `npm.cmd --prefix frontend run format:check`: correcta con Prettier.
- `..\.venv\Scripts\python.exe -m ruff check app tests`: correcta.
- `pytest tests -q -p no:cacheprovider`: 11 pruebas aprobadas sobre MySQL de pruebas. Cubren ciclo de atención, vencimientos, transacción de entrega+abono, permisos, idempotencia, clasificación histórica, paginación y contadores.
- `alembic check`: sin diferencias pendientes entre modelos y esquema.
- E2E con FastAPI y MySQL reales: 7 de 7 recorridos aprobados, incluyendo cierre directo, filtros y vistas guardadas, persistencia de borradores, recuperación de errores, autocompletado, conversión de cotización, producción, entrega parcial y posterior liquidación completa en el flujo combinado.
- Migraciones 003/004 aplicadas a comgraf y comgraf_test. La comprobación de alcance de datos previo a 004 encontró un expediente y ningún seguimiento vencido asociado a atención cerrada; 004 no cambió estados de casos. No se borraron ni reiniciaron las bases.

La suite backend vacía tablas únicamente en una base cuyo nombre termina en `_test`. Ejecutarla antes de `tests.seed_e2e`; nunca apuntarla a una base con datos que se deseen conservar. E2E también modifica exclusivamente esa base.

## Límites

- Docker no está instalado en este equipo; no se verificó `docker compose build/up`. La verificación se hizo con FastAPI y MySQL local reales.
- Correo y WhatsApp siguen sin proveedor externo y no envían mensajes.
- Las métricas que dependen de llamadas, visitas o mensajes externos dependen de registro manual.
- Este CRM es control operativo de cobros y no reemplaza contabilidad ni fiscalidad.
- Pruebas de reinicio de servicios/adjuntos y restauración de respaldo no se repitieron en esta ronda.
- La interfaz del equipo local escucha en `127.0.0.1`; el acceso LAN usa Compose y debe restringirse a la red privada.
- La revisión 003 normalizó los estados activos heredados a `Abierto` sin copiar el valor anterior a un evento. Para registros ya migrados no es posible reconstruir ese estado a partir de la base actual; la bitácora sí conserva el estado anterior/nuevo para cada cambio realizado desde esta versión.
- El pago combinado en entrega muestra el saldo real precargado al activar el control. Se puede confirmar ese importe para liquidar o sustituirlo por un abono parcial; la autorización financiera sigue validándose en servidor.
