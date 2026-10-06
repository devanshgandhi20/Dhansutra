"""add source_id uniqueness and indexes

Revision ID: a89f2d1e4c3b
Revises:
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'a89f2d1e4c3b'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_index(
        'ix_transactions_user_source_id',
        'transactions',
        ['user_id', 'source_id'],
        unique=True,
        postgresql_where=sa.text('source_id IS NOT NULL')
    )
    op.create_index(
        'ix_transactions_user_date',
        'transactions',
        ['user_id', 'date']
    )

def downgrade():
    op.drop_index('ix_transactions_user_date', table_name='transactions')
    op.drop_index('ix_transactions_user_source_id', table_name='transactions')