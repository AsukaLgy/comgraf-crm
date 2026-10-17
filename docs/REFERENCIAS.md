# Referencias consultadas y procedencia

Implementación original de Comgraf en React y Python. No se copiaron componentes ni clases de Frappe, ni el frontend del prototipo. No requiere Bench, Redis, un sitio Frappe ni un backend Node.

## Prototipo local

Archivo disponible: `C:\Users\asuka\Desktop\Prototipo\CRM.rar`.
Se inspeccionó la copia extraída que ya existía en:

- `C:\Users\asuka\Desktop\Prototipo\CRM\CRM\public\index.html`
- `C:\Users\asuka\Desktop\Prototipo\CRM\CRM\public\app.js`

Patrones adaptados: consola compacta, cola y Mis casos, encabezado con acciones, bitácora central, resumen lateral, pestañas de comunicación y campos dinámicos de productos. Se separaron atención, producción, entregas y pago; se reemplazó el redibujado completo por componentes React y consultas con caché. Los canales no configurados no simulan envíos.

## Frappe CRM

Repositorio accesible: `C:\Claude-Proyect\crm-develop\crm-develop`.
Archivos inspeccionados:

- `AGENTS.md`, `LICENSE` (GNU AGPL v3).
- `crm/fcrm/doctype/crm_lead/crm_lead.py`: relaciones documentales, responsable, validaciones, respuesta y registro de cambios.
- `crm/api/assignment_rule.py`: organización de asignaciones.
- `frontend/src/data/document.js`: carga y actualización de documentos, permisos y estado de errores.
- `frontend/src/components/Activities/ActivityHeader.vue`: actividades separadas por tipo.

Se adoptaron patrones de organización, referencias y validación en servidor, no código sujeto a esa licencia. No se encontró el árbol completo de Frappe Framework (`frappe/model/document.py`) en la ruta indicada. No se afirma haberlo inspeccionado.

## Documentación técnica

- [MySQL: paquetes de Windows](https://dev.mysql.com/doc/refman/8.4/en/windows-choosing-package.html).
- [Descarga oficial MySQL 8.4](https://dev.mysql.com/downloads/mysql/8.4.html): paquete portátil usado en pruebas, 8.4.11; MD5 publicado y comprobado: `2e833921898a9a030ea6bfe81bd811bc`.
- [FastAPI: hashing de contraseñas](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/): se utilizó Argon2 mediante pwdlib; las sesiones de Comgraf son tokens opacos revocables almacenados como hash, no JWT.
- [SQLAlchemy: bloqueo para actualización](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.GenerativeSelect.with_for_update).

Las dependencias conservan sus licencias en sus distribuciones. MySQL portátil se mantiene en `.runtime`, fuera del código fuente y de la imagen de la aplicación. Compose utiliza la imagen oficial de MySQL.
