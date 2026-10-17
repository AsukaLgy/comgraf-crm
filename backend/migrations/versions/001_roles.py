"""Initial configurable roles."""
from alembic import op
import sqlalchemy as sa

revision = '001_roles'
down_revision = '58d1e221e63e'
branch_labels = None
depends_on = None


def upgrade():
    roles = sa.table('roles', sa.column('name', sa.String), sa.column('permissions', sa.JSON))
    op.bulk_insert(roles, [
        {'name': 'Administrador', 'permissions': ['*']},
        {'name': 'Coordinador', 'permissions': ['read_all', 'attention', 'assign', 'reopen', 'production', 'finance', 'adjust', 'clients', 'reports']},
        {'name': 'Atención', 'permissions': ['attention', 'clients']},
        {'name': 'Producción', 'permissions': ['production']},
        {'name': 'Cobranza', 'permissions': ['finance', 'reports']},
    ])


def downgrade():
    op.execute("DELETE FROM roles WHERE name IN ('Administrador', 'Coordinador', 'Atención', 'Producción', 'Cobranza')")
