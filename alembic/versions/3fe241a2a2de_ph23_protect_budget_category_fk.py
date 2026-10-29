"""ph23_protect_budget_category_fk

Revision ID: 3fe241a2a2de
Revises: e92af291fb7e
Create Date: 2026-10-29 08:10:01.896078

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3fe241a2a2de'
down_revision: Union[str, None] = 'e92af291fb7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Replace the existing cascading foreign key.
    op.drop_constraint(
        "budgets_category_id_fkey",
        "budgets",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "budgets_category_id_fkey",
        "budgets",
        "categories",
        ["category_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Restore the original PH2.2 foreign key.
    op.drop_constraint(
        "budgets_category_id_fkey",
        "budgets",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "budgets_category_id_fkey",
        "budgets",
        "categories",
        ["category_id"],
        ["id"],
        ondelete="CASCADE",
    )