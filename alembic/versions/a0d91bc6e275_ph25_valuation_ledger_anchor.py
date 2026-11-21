"""PH2.5: Anchor investment market valuations to their ledger.

Revision ID: a0d91bc6e275
Revises: 7b4c2d8e9f01

Existing positive investment market valuations retain their
effective values. Legacy NULL and zero valuations are untouched.
"""

from alembic import op
import sqlalchemy as sa


revision = "a0d91bc6e275"
down_revision = "7b4c2d8e9f01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "valuation_ledger_anchor",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
    )

    # Match the ledger formula in FinanceService:
    # starting balance + income - expense
    # + transfers in - transfers out.
    #
    # Scope movements by BOTH account ID and owner.
    # Only positive legacy valuations receive an anchor.
    # A zero legacy valuation remains unanchored.

    op.execute("""
        WITH movements AS (
            SELECT
                user_id,
                account_id,
                CASE
                    WHEN type = 'INCOME' THEN amount
                    ELSE -amount
                END AS delta
            FROM transactions
            WHERE type IN ('INCOME', 'EXPENSE')
              AND account_id IS NOT NULL

            UNION ALL

            SELECT
                user_id,
                to_account_id AS account_id,
                amount AS delta
            FROM transactions
            WHERE type = 'TRANSFER'
              AND to_account_id IS NOT NULL

            UNION ALL

            SELECT
                user_id,
                from_account_id AS account_id,
                -amount AS delta
            FROM transactions
            WHERE type = 'TRANSFER'
              AND from_account_id IS NOT NULL
        )
        UPDATE accounts AS a
        SET valuation_ledger_anchor =
            a.starting_balance
            + COALESCE(
                (
                    SELECT SUM(m.delta)
                    FROM movements AS m
                    WHERE m.account_id = a.id
                      AND m.user_id = a.user_id
                ),
                0
            )
        WHERE a.type = 'INVESTMENT'
          AND a.current_market_value > 0
    """)


def downgrade() -> None:
    op.drop_column(
        "accounts",
        "valuation_ledger_anchor",
    )
