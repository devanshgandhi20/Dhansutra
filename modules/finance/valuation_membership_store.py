"""PH2.5: capture membership in the latest investment valuation snapshot.

Caller MUST hold the account row lock and commit in the same transaction.
This helper does not commit, rollback, or acquire locks.
"""

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from modules.finance.models import (
    Account,
    AccountType,
    Transaction,
    TransactionType,
    InvestmentValuationMember,
)
from modules.finance.snapshot_provenance import capture_account_membership


def replace_investment_valuation_members(
    db: Session,
    user_id: int,
    account: Account,
) -> int:
    """Atomically replace this account's latest snapshot membership.

    For a cleared snapshot, delete its membership. For a new explicit
    valuation (including zero), capture the transaction state CURRENTLY
    in the ledger, regardless of the transaction's backdated date.
    The caller must already hold a FOR UPDATE lock on this account,
    and must flush/commit or roll back the surrounding transaction.
    """
    if account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.type != AccountType.INVESTMENT:
        raise HTTPException(
            status_code=409,
            detail="Valuation membership requires an investment account",
        )

    if account.current_market_value is None:
        if account.valuation_ledger_anchor is not None:
            raise HTTPException(status_code=409, detail="Orphan valuation anchor")
        membership = {}
    else:
        if account.valuation_ledger_anchor is None:
            raise HTTPException(status_code=409, detail="Snapshot missing ledger anchor")

        ledger_rows = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == user_id,
                or_(
                    and_(
                        Transaction.type.in_((TransactionType.INCOME, TransactionType.EXPENSE)),
                        Transaction.account_id == account.id,
                    ),
                    and_(
                        Transaction.type == TransactionType.TRANSFER,
                        or_(
                            Transaction.from_account_id == account.id,
                            Transaction.to_account_id == account.id,
                        ),
                    ),
                ),
            )
            .order_by(Transaction.id.asc())
            .all()
        )
        try:
            membership = capture_account_membership(
                account.id,
                (
                    {
                        "id": tx.id,
                        "type": tx.type,
                        "amount": tx.amount,
                        "account_id": tx.account_id,
                        "from_account_id": tx.from_account_id,
                        "to_account_id": tx.to_account_id,
                    }
                    for tx in ledger_rows
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail="Cannot capture inconsistent investment ledger",
            ) from exc

    # Executed SQL DELETE happens before later INSERTs. In particular this
    # avoids uniqueness conflicts when revaluing the same transaction set.
    db.query(InvestmentValuationMember).filter(
        InvestmentValuationMember.account_id == account.id
    ).delete(synchronize_session="fetch")

    db.add_all(
        [
            InvestmentValuationMember(
                account_id=account.id,
                transaction_id=tx_id,
                baseline_contribution=amount,
            )
            for tx_id, amount in sorted(membership.items())
        ]
    )
    return len(membership)
