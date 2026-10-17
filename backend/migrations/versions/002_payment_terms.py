"""Normalize only clear legacy payment-term equivalents."""
from alembic import op
import sqlalchemy as sa

revision = '002_payment_terms'
down_revision = '001_roles'
branch_labels = None
depends_on = None


def upgrade():
    # Do not infer or rewrite balances, due dates, or ambiguous free-text terms.
    op.add_column('productions', sa.Column('specs_snapshot', sa.JSON(), nullable=True))
    op.execute("UPDATE productions SET payment_terms = 'Contado' WHERE LOWER(TRIM(payment_terms)) = 'contado'")
    op.execute("UPDATE productions SET payment_terms = 'Crédito' WHERE LOWER(TRIM(payment_terms)) IN ('credito', 'crédito')")


def downgrade():
    # Normalizing capitalization/spacing is intentionally retained on downgrade.
    op.drop_column('productions', 'specs_snapshot')
