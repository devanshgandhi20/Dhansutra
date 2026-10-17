"""remember deleted Gmail source ids so they are never re-imported

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-17 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ignored_source_ids',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'source_id', name='uq_ignored_source_ids_user_source'),
    )
    op.create_index(op.f('ix_ignored_source_ids_id'), 'ignored_source_ids', ['id'], unique=False)
    op.create_index(op.f('ix_ignored_source_ids_user_id'), 'ignored_source_ids', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ignored_source_ids_user_id'), table_name='ignored_source_ids')
    op.drop_index(op.f('ix_ignored_source_ids_id'), table_name='ignored_source_ids')
    op.drop_table('ignored_source_ids')
