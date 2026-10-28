"""ph22_goal_reversal_archive

Revision ID: e92af291fb7e
Revises: 890f75992fe3
Create Date: 2026-10-27 21:28:35.588685

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e92af291fb7e'
down_revision: Union[str, None] = '890f75992fe3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # PH2.2: Allow goals to be archived without deleting
    # their contribution and transaction history.
    op.add_column(
        "financial_goals",
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )

    # Track the compensating transaction for a full
    # reversal of an existing goal contribution.
    op.add_column(
        "goal_contributions",
        sa.Column(
            "reversal_transaction_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    # Record when the contribution was reversed.
    op.add_column(
        "goal_contributions",
        sa.Column("reversed_at", sa.DateTime(), nullable=True),
    )

    # A reversal transaction must reference an existing
    # ledger transaction and cannot be deleted while linked.
    op.create_foreign_key(
        "fk_goal_contributions_reversal_transaction_id",
        "goal_contributions",
        "transactions",
        ["reversal_transaction_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Prevent the same reversal transaction from being
    # assigned to multiple contributions.
    op.create_unique_constraint(
        "uq_goal_contributions_reversal_transaction_id",
        "goal_contributions",
        ["reversal_transaction_id"],
    )


def downgrade() -> None:
    # Refuse downgrades that would erase PH2.2 audit history.
    bind = op.get_bind()

    has_history = bind.execute(
        sa.text(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM financial_goals
                    WHERE closed_at IS NOT NULL
                )
                OR EXISTS (
                    SELECT 1
                    FROM goal_contributions
                    WHERE reversal_transaction_id IS NOT NULL
                       OR reversed_at IS NOT NULL
                )
            """
        )
    ).scalar_one()

    if has_history:
        raise RuntimeError(
            "PH2.2 downgrade refused: archived goals or "
            "reversed contributions exist. "
            "Financial audit history must be preserved."
        )

    # Remove constraints before dropping their column.
    op.drop_constraint(
        "uq_goal_contributions_reversal_transaction_id",
        "goal_contributions",
        type_="unique",
    )

    op.drop_constraint(
        "fk_goal_contributions_reversal_transaction_id",
        "goal_contributions",
        type_="foreignkey",
    )

    op.drop_column(
        "goal_contributions",
        "reversed_at",
    )

    op.drop_column(
        "goal_contributions",
        "reversal_transaction_id",
    )

    op.drop_column(
        "financial_goals",
        "closed_at",
    )