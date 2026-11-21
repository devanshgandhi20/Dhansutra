"""PH2.5 shared ledger-writer integrity helpers.

Callers must keep these operations in one database transaction:
lock -> mutate -> flush -> validate -> commit.

On failure, the caller must roll back.
"""

from typing import Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from modules.finance.models import Account, AccountType
from modules.finance.services import FinanceService
from modules.finance.valuation import require_nonnegative_investment_value


def lock_owned_accounts(
    db: Session,
    user_id: int,
    account_ids: Iterable[int],
) -> list[Account]:
    """Lock each affected account in deterministic ascending ID order.

    populate_existing prevents stale ORM identity-map attributes
    from surviving a wait for another transaction's row lock.
    Must be called before mutating any of these account rows.
    """

    ids = set()

    for account_id in account_ids:
        if (
            not isinstance(account_id, int)
            or isinstance(account_id, bool)
            or account_id <= 0
        ):
            raise HTTPException(
                status_code=422,
                detail="Invalid account identifier",
            )

        ids.add(account_id)

    locked = []

    for account_id in sorted(ids):
        account = (
            db.query(Account)
            .filter(
                Account.id == account_id,
                Account.user_id == user_id,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )

        if account is None:
            raise HTTPException(
                status_code=404,
                detail="Account not found",
            )

        locked.append(account)

    return locked


def validate_postwrite_investments(
    db: Session,
    user_id: int,
    locked_accounts: list[Account],
) -> None:
    """Reject negative effective investment values.

    Caller must flush all proposed ledger mutations first.
    Must execute before commit while account locks are held.
    """

    investments = {
        account.id
        for account in locked_accounts
        if account.type == AccountType.INVESTMENT
    }

    if not investments:
        return

    summaries = {
        summary.id: summary
        for summary in FinanceService.calculate_account_balances(
            db, user_id
        )
    }

    for account_id in sorted(investments):
        summary = summaries.get(account_id)

        if (
            summary is None
            or summary.type != AccountType.INVESTMENT
        ):
            raise HTTPException(
                status_code=409,
                detail="Investment balance cannot be reconciled",
            )

        try:
            require_nonnegative_investment_value(
                summary.current_market_value
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Transaction would produce an invalid "
                    "effective investment value"
                ),
            ) from exc
