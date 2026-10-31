"""PH2.4: optional income reporting period.

Revision ID: 7b4c2d8e9f01
Revises: 3fe241a2a2de

Adds nullable attribution metadata without rewriting
transaction dates, amounts, or financial balances.
"""

from alembic import op
import sqlalchemy as sa

revision = "7b4c2d8e9f01"
down_revision = "3fe241a2a2de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "income_period",
            sa.String(length=7),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_transactions_income_period_valid",
        "transactions",
        "income_period IS NULL OR (type = 'INCOME' AND income_period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_transactions_income_period_valid",
        "transactions",
        type_="check",
    )

    op.drop_column("transactions", "income_period")
