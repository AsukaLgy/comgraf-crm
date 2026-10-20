"""Add order QR identities, bindery confirmation cycles and release requests."""
from alembic import op
import sqlalchemy as sa
import uuid

revision = '009_binding_scan_groups'
down_revision = '008_multiple_production_orders'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('productions', sa.Column('scan_id', sa.String(36), nullable=True))
    bind = op.get_bind()
    for (production_id,) in bind.execute(sa.text('SELECT id FROM productions WHERE scan_id IS NULL')).all():
        bind.execute(sa.text('UPDATE productions SET scan_id = :scan_id WHERE id = :id'),
            {'scan_id': str(uuid.uuid4()), 'id': production_id})
    op.alter_column('productions', 'scan_id', existing_type=sa.String(36), nullable=False)
    op.create_index('ux_productions_scan_id', 'productions', ['scan_id'], unique=True)
    op.add_column('productions', sa.Column('workflow_cycle', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('productions', sa.Column('binding_confirmed_cycle', sa.Integer(), nullable=True))
    op.add_column('productions', sa.Column('binding_confirmed_at', sa.DateTime(), nullable=True))
    op.add_column('productions', sa.Column('binding_confirmed_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_productions_binding_confirmed_by', 'productions', 'users', ['binding_confirmed_by_id'], ['id'])

    op.create_table(
        'partial_release_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('case_id', sa.Integer(), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('production_id', sa.Integer(), sa.ForeignKey('productions.id'), nullable=False),
        sa.Column('production_cycle', sa.Integer(), nullable=False),
        sa.Column('requester_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('request_comment', sa.Text(), nullable=False),
        sa.Column('request_key', sa.String(80), nullable=False, unique=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='Pendiente'),
        sa.Column('approver_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('decision_comment', sa.Text(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_partial_release_requests_case_id', 'partial_release_requests', ['case_id'])
    op.create_index('ix_partial_release_requests_production_id', 'partial_release_requests', ['production_id'])
    op.create_index('ix_partial_release_status_case', 'partial_release_requests', ['status', 'case_id'])

    op.execute(sa.text("""
        UPDATE roles
        SET permissions = JSON_ARRAY_APPEND(permissions, '$', 'partial_release_approve')
        WHERE name = 'Coordinador'
          AND JSON_CONTAINS(permissions, JSON_QUOTE('partial_release_approve')) = 0
    """))
    op.execute(sa.text("""
        INSERT INTO roles(name, permissions)
        SELECT :name, JSON_ARRAY('binding_scan')
        WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :name)
    """).bindparams(name='Escáner de encuadernación'))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT COUNT(*) FROM partial_release_requests')).scalar_one():
        raise RuntimeError('Ya hay solicitudes de liberación auditadas; no se puede retirar el control de escaneo con seguridad.')
    if bind.execute(sa.text('SELECT COUNT(*) FROM productions WHERE binding_confirmed_at IS NOT NULL')).scalar_one():
        raise RuntimeError('Hay confirmaciones de encuadernación auditadas; no se puede retirar el control de escaneo con seguridad.')
    if bind.execute(sa.text("SELECT COUNT(*) FROM users WHERE JSON_CONTAINS(roles, JSON_QUOTE('Escáner de encuadernación'))")).scalar_one():
        raise RuntimeError('Hay cuentas de escaneo activas; retíralas antes de revertir la migración.')
    op.drop_index('ix_partial_release_status_case', table_name='partial_release_requests')
    op.drop_index('ix_partial_release_requests_production_id', table_name='partial_release_requests')
    op.drop_index('ix_partial_release_requests_case_id', table_name='partial_release_requests')
    op.drop_table('partial_release_requests')
    op.drop_constraint('fk_productions_binding_confirmed_by', 'productions', type_='foreignkey')
    op.drop_column('productions', 'binding_confirmed_by_id')
    op.drop_column('productions', 'binding_confirmed_at')
    op.drop_column('productions', 'binding_confirmed_cycle')
    op.drop_column('productions', 'workflow_cycle')
    op.drop_index('ux_productions_scan_id', table_name='productions')
    op.drop_column('productions', 'scan_id')
    op.execute(sa.text("""
        UPDATE roles
        SET permissions = JSON_REMOVE(permissions, JSON_UNQUOTE(JSON_SEARCH(permissions, 'one', 'partial_release_approve')))
        WHERE name = 'Coordinador'
          AND JSON_SEARCH(permissions, 'one', 'partial_release_approve') IS NOT NULL
    """))
    op.execute(sa.text("DELETE FROM roles WHERE name = 'Escáner de encuadernación'"))
