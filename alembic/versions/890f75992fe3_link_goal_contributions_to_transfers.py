"""link goal contributions to transfers

Revision ID: 890f75992fe3
Revises: b11ae76ed532
Create Date: 2026-10-25 18:22:47.754637

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '890f75992fe3'
down_revision: Union[str, None] = 'b11ae76ed532'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    op.add_column(
        "goal_contributions",
        sa.Column(
            "transaction_id",
            sa.Integer(),
            nullable=True
        )
    )

    op.create_foreign_key(
        "fk_goal_contributions_transaction_id",
        "goal_contributions",
        "transactions",
        ["transaction_id"],
        ["id"],
        ondelete="RESTRICT"
    )

    op.create_unique_constraint(
        "uq_goal_contributions_transaction_id",
        "goal_contributions",
        ["transaction_id"]
    )


def downgrade():
    op.drop_constraint(
        "uq_goal_contributions_transaction_id",
        "goal_contributions",
        type_="unique"
    )

    op.drop_constraint(
        "fk_goal_contributions_transaction_id",
        "goal_contributions",
        type_="foreignkey"
    )

    op.drop_column(
        "goal_contributions",
        "transaction_id"
    )