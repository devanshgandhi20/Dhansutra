"""PH2.5: per-account latest investment valuation membership.

Revision ID: b4f8c2a7d910
Revises: a0d91bc6e275

Draft only. Apply first the parent anchor migration, then this revision,
with a dedicated staging rehearsal and backup. No live execution here.
"""
from alembic import op
import sqlalchemy as sa

revision = "b4f8c2a7d910"
down_revision = "a0d91bc6e275"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "investment_valuation_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("baseline_contribution", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("account_id", "transaction_id", name="uq_ivm_account_transaction"),
    )
    op.create_index(
        "ix_ivm_transaction_id",
        "investment_valuation_members",
        ["transaction_id"],
        unique=False,
    )

    # Parent revision anchors positive legacy investment valuations at the
    # then-current ledger. This backfill captures exactly the contributions
    # reflected in those ledger anchors; zero/NULL unanchored legacy accounts
    # deliberately acquire no membership.
    #
    # Membership is per account/transaction and includes zero contributions.
    # No transaction FK: a deleted row must leave a membership tombstone.
    op.execute(sa.text("""
        INSERT INTO investment_valuation_members
            (account_id, transaction_id, baseline_contribution)
        SELECT
            contributions.account_id,
            contributions.transaction_id,
            SUM(contributions.signed_amount) AS baseline_contribution
        FROM (
            SELECT t.user_id, t.account_id, t.id AS transaction_id,
                   CASE WHEN t.type = 'INCOME' THEN t.amount ELSE -t.amount END
                       AS signed_amount
            FROM transactions AS t
            WHERE t.type IN ('INCOME', 'EXPENSE')
              AND t.account_id IS NOT NULL

            UNION ALL

            SELECT t.user_id, t.from_account_id AS account_id,
                   t.id AS transaction_id, -t.amount AS signed_amount
            FROM transactions AS t
            WHERE t.type = 'TRANSFER'
              AND t.from_account_id IS NOT NULL

            UNION ALL

            SELECT t.user_id, t.to_account_id AS account_id,
                   t.id AS transaction_id, t.amount AS signed_amount
            FROM transactions AS t
            WHERE t.type = 'TRANSFER'
              AND t.to_account_id IS NOT NULL
        ) AS contributions
        JOIN accounts AS a
          ON a.id = contributions.account_id
         AND a.user_id = contributions.user_id
        WHERE a.type = 'INVESTMENT'
          AND a.current_market_value IS NOT NULL
          AND a.valuation_ledger_anchor IS NOT NULL
        GROUP BY contributions.account_id, contributions.transaction_id
    """))


def downgrade():
    op.drop_index("ix_ivm_transaction_id", table_name="investment_valuation_members")
    op.drop_table("investment_valuation_members")
