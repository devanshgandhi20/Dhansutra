
"""PH2.5: Pure ledger-anchored investment valuation calculations.

No SQLAlchemy, API, file-system, or application side effects.
"""

from decimal import Decimal, InvalidOperation


def as_decimal(value):
    if value is None or isinstance(value, bool):
        raise ValueError("A finite decimal value is required")

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid decimal value") from exc

    if not amount.is_finite():
        raise ValueError("Decimal value must be finite")

    return amount


def set_valuation_snapshot(market_value, ledger_balance):
    """Return a new snapshot and its current-ledger anchor.

    A NULL market value clears the snapshot and anchor.
    """

    if market_value is None:
        return None, None

    snapshot = as_decimal(market_value)
    ledger = as_decimal(ledger_balance)

    if snapshot < 0:
        raise ValueError("Market valuation cannot be negative")

    return snapshot, ledger


def effective_investment_value(
    ledger_balance,
    market_snapshot,
    ledger_anchor,
):
    """Calculate investment value without mutating either stored field.

    NULL/NULL: ledger-derived value.
    Legacy zero/NULL: ledger fallback, preserving old displays.
    Non-NULL snapshot/anchor: snapshot plus ledger difference.

    A positive snapshot without an anchor requires migration.
    """

    ledger = as_decimal(ledger_balance)

    if market_snapshot is None:
        if ledger_anchor is not None:
            raise ValueError("Ledger anchor without market snapshot")
        return ledger

    snapshot = as_decimal(market_snapshot)

    if snapshot < 0:
        raise ValueError("Market valuation cannot be negative")

    if ledger_anchor is None:
        if snapshot == 0:
            return ledger

        raise ValueError(
            "Positive market snapshot requires ledger anchor"
        )

    anchor = as_decimal(ledger_anchor)

    return snapshot + (ledger - anchor)


def require_nonnegative_investment_value(value):
    """A separate write-time policy guard for proposed outflows."""

    amount = as_decimal(value)

    if amount < 0:
        raise ValueError("Insufficient effective investment value")

    return amount
