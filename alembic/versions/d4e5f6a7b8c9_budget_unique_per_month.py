"""one budget per user, category and month

Revision ID: d4e5f6a7b8c9
Revises: c7b2e3f4a10d
Create Date: 2026-10-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c7b2e3f4a10d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the most recently saved budget when duplicates already exist, otherwise the constraint cannot be created.
    op.execute(
        """
        DELETE FROM budgets b
        USING budgets newer
        WHERE b.user_id = newer.user_id
          AND b.category_id = newer.category_id
          AND b.month = newer.month
          AND b.id < newer.id
        """
    )
    op.create_unique_constraint(
        'uq_budgets_user_category_month',
        'budgets',
        ['user_id', 'category_id', 'month'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_budgets_user_category_month', 'budgets', type_='unique')
