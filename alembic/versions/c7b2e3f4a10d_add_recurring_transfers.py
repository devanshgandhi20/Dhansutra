"""add recurring transfers support

Revision ID: c7b2e3f4a10d
Revises: a89f2d1e4c3b
Create Date: 2026-10-07 19:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c7b2e3f4a10d'
down_revision = 'a89f2d1e4c3b'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Add to_account_id column to recurring_transactions
    op.add_column('recurring_transactions', sa.Column('to_account_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_recurring_to_account',
        'recurring_transactions',
        'accounts',
        ['to_account_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # 2. Make category_id nullable for recurring transfers
    op.alter_column('recurring_transactions', 'category_id',
               existing_type=sa.INTEGER(),
               nullable=True)

def downgrade() -> None:
    op.alter_column('recurring_transactions', 'category_id',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.drop_constraint('fk_recurring_to_account', 'recurring_transactions', type_='foreignkey')
    op.drop_column('recurring_transactions', 'to_account_id')