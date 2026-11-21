
"""PH2.5: reconcile edits/deletes with the most recent valuation membership.

The calling route must hold sorted row locks for ALL old/new affected accounts
and the transaction row, and must own the current transaction state. The
caller must mutate the ledger, flush, validate resulting investment balances,
and commit; on any failure it must roll back. This helper never commits,
rolls back, flushes, locks rows, or removes membership tombstones.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from modules.finance.models import (
    Account,
    AccountType,
    InvestmentValuationMember,
)
from modules.finance.snapshot_provenance import (
    rebase_included_anchor,
    require_safe_account_reassignment,
    transaction_contributions,
)


def reconcile_valuation_transaction_change(
    db: Session,
    user_id: int,
    transaction_id: int,
    before: dict | None,
    after: dict | None,
    locked_accounts: list[Account],
) -> dict[int, object]:
    """Rebase only frozen members by their latest signed-ledger delta.

    before/after are immutable copies of the transaction state. A deleted
    transaction has after=None. A newly inserted transaction has before=None.
    No comparison is made using the transaction's calendar date or created_at.
    Membership baseline_contribution is never overwritten during corrections.
    """
    if type(transaction_id) is not int or transaction_id <= 0:
        raise HTTPException(status_code=422, detail="Invalid transaction ID")
    if before is None and after is None:
        raise HTTPException(status_code=422, detail="Missing transaction change")

    for state in (before, after):
        if state is None:
            continue
        if state.get("id") != transaction_id:
            raise HTTPException(status_code=409, detail="Transaction state ID changed")
        if state.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="Transaction not found")

    def signed(state):
        if state is None:
            return {}
        return transaction_contributions(
            state["type"],
            state["amount"],
            account_id=state.get("account_id"),
            from_account_id=state.get("from_account_id"),
            to_account_id=state.get("to_account_id"),
        )

    try:
        previous = signed(before)
        updated = signed(after)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="Invalid transaction contribution") from exc

    involved = set(previous) | set(updated)
    locked = {account.id: account for account in locked_accounts}

    if len(locked) != len(locked_accounts) or not involved.issubset(locked):
        raise HTTPException(status_code=409, detail="Affected accounts must all be locked")

    for account in locked_accounts:
        if account.user_id != user_id:
            raise HTTPException(status_code=404, detail="Account not found")

    valued = set()
    for account_id in involved:
        account = locked[account_id]
        if account.type != AccountType.INVESTMENT:
            continue
        market = account.current_market_value
        anchor = account.valuation_ledger_anchor
        if market is None and anchor is not None:
            raise HTTPException(status_code=409, detail="Orphan investment anchor")
        if market is not None and anchor is None and market != 0:
            raise HTTPException(status_code=409, detail="Investment snapshot missing anchor")
        if market is not None and anchor is not None:
            valued.add(account_id)

    # Creating or deleting a transaction changes the affected-account set
    # by definition; only an EDIT that moves contributions is reassignment.
    if before is not None and after is not None:
        try:
            require_safe_account_reassignment(previous, updated, valued)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail="Account reassignment requires valuation reconciliation",
            ) from exc

    rebased = {}
    for account_id in sorted(involved & valued):
        account = locked[account_id]
        membership = (
            db.query(InvestmentValuationMember)
            .filter(
                InvestmentValuationMember.account_id == account_id,
                InvestmentValuationMember.transaction_id == transaction_id,
            )
            .one_or_none()
        )
        if membership is None:
            continue
        if before is None:
            raise HTTPException(
                status_code=409,
                detail="New transaction conflicts with a historical snapshot tombstone",
            )
        try:
            new_anchor = rebase_included_anchor(
                account.valuation_ledger_anchor,
                included=True,
                before=previous.get(account_id, 0),
                after=updated.get(account_id, 0),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Invalid historical rebase") from exc
        account.valuation_ledger_anchor = new_anchor
        rebased[account_id] = new_anchor

    return rebased

