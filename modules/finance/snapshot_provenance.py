"""Pure PH2.5 valuation snapshot membership arithmetic; no database operations.

Membership records the transaction IDs and signed contributions included when
an investment valuation was captured. It is account-specific and frozen until
that investment is explicitly revalued.
"""
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")


def _amount(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid monetary amount") from exc
    if not number.is_finite():
        raise ValueError("Nonfinite monetary amount")
    return number


def _account_id(value):
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid account ID")
    return value


def transaction_contributions(kind, amount, *, account_id=None,
                              from_account_id=None, to_account_id=None):
    """Match FinanceService ledger semantics; transfer `account_id` is not outflow.

    For historical rows with a null transfer source, do not silently substitute
    account_id: the existing balance service does not count that outflow.
    """
    kind = getattr(kind, "value", kind)
    if kind not in ("INCOME", "EXPENSE", "TRANSFER"):
        raise ValueError("Unsupported transaction type")
    amount = _amount(amount)
    if amount < ZERO:
        raise ValueError("Transaction amount cannot be negative")
    account_id = _account_id(account_id)
    from_account_id = _account_id(from_account_id)
    to_account_id = _account_id(to_account_id)
    if kind in ("INCOME", "EXPENSE"):
        if account_id is None:
            return {}
        return {account_id: amount if kind == "INCOME" else -amount}
    result = {}
    if from_account_id is not None:
        result[from_account_id] = result.get(from_account_id, ZERO) - amount
    if to_account_id is not None:
        result[to_account_id] = result.get(to_account_id, ZERO) + amount
    return result


def capture_account_membership(account_id, transactions):
    """Capture current IDs/contributions; includes zero-value ledger rows.

    `transactions` is an owned, consistent ledger snapshot supplied by caller.
    No date filtering: capture is based on recorded state, not transaction date.
    """
    account_id = _account_id(account_id)
    if account_id is None:
        raise ValueError("Account required")
    included = {}
    for tx in transactions:
        tx_id = tx["id"]
        if type(tx_id) is not int or tx_id <= 0:
            raise ValueError("Invalid transaction ID")
        contributions = transaction_contributions(
            tx["type"], tx["amount"], account_id=tx.get("account_id"),
            from_account_id=tx.get("from_account_id"),
            to_account_id=tx.get("to_account_id"),
        )
        if account_id in contributions:
            if tx_id in included:
                raise ValueError("Duplicate transaction ID")
            included[tx_id] = contributions[account_id]
    return included


def rebase_included_anchor(anchor, *, included, before, after):
    """Rebase by CURRENT signed delta, never by original snapshot contribution.

    Caller determines membership from the frozen account-specific snapshot.
    For a deleted included transaction, after=0. For a new transaction,
    included=False. Keep original baseline membership immutable for audit.
    """
    if anchor is None:
        if included:
            raise ValueError("Included transaction requires an anchor")
        return None
    anchor = _amount(anchor)
    before = _amount(before)
    after = _amount(after)
    return anchor + (after - before) if included else anchor


def require_safe_account_reassignment(before, after, valued_investment_ids):
    """Temporarily forbid account-reference changes involving valued investments.

    The caller must reconcile both affected snapshots explicitly instead.
    Amount edits with unchanged account references remain permitted.
    """
    affected = set(before) | set(after)
    if set(before) != set(after) and (affected & set(valued_investment_ids)):
        raise ValueError("Valued investment reassignment needs reconciliation")
