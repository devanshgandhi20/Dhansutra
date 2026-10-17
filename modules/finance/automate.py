"""Helpers for automatic (Gmail / Apps Script) imports: payload models, SIP detection, auto-categorisation.

The old POST /webhook-sync endpoint that lived here was never mounted in main.py, but it
looked up accounts across ALL users and defaulted to a public secret token, so it has been
removed. The live endpoint is POST /api/finance/imports/webhook-ingest in imports.py.
"""
import re
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from modules.finance.models import Category, Transaction, TransactionType

# Built-in merchant keyword patterns for regular Indian transactions.
# Matched as whole words, so "ola" no longer fires on "Cola" and "uti" no longer fires on "Beauty".
_MERCHANT_PATTERNS = [
    (r"swiggy|zomato|eats|starbucks|mcdonalds|kfc|burger|domino|pizza", "Dining Out"),
    (r"blinkit|zepto|instamart|bigbasket|dmart|reliance fresh|nature basket", "Groceries"),
    (r"uber|ola|rapido|metro|petrol|fuel|hpcl|bpcl|ioc|irctc|redbus|roppen", "Fuel & Transit"),
    (r"bescom|tata power|airtel|jio|vi|billdesk|electricity|gas|water", "Utilities"),
    (r"netflix|spotify|prime|youtube|hotstar|apple|google play", "Subscriptions"),
    (r"rent|landlord|nobroker|nestaway", "House Rent"),
    (r"salary|wages|payroll|stipend", "Salary & Wages"),
]


def _word_regex(alternatives: str) -> "re.Pattern[str]":
    return re.compile(rf"\b(?:{alternatives})\b")


DEFAULT_MERCHANT_PATTERNS = [(_word_regex(p), name) for p, name in _MERCHANT_PATTERNS]

# Patterns detecting SIP / Mutual Fund NACH / ACH debits
SIP_TRANSFER_PATTERNS = _word_regex(
    r"ach-dr|nach|prudent|camspay|bse\s?star|mf|mutual\s?fund|uti|hdfc\s?amc|nippon|zerodha|groww"
)


class IncomingTx(BaseModel):
    type: str  # "EXPENSE" or "INCOME"
    amount: float
    description: str
    date: str
    source_id: Optional[str] = None


class WebhookPayload(BaseModel):
    transactions: List[IncomingTx]


def is_sip_transaction(description: str) -> bool:
    """Matches debit alerts against mutual fund ACH/NACH keywords."""
    return bool(SIP_TRANSFER_PATTERNS.search((description or "").lower()))


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_category(description: str, tx_type: str, user_id: int, db: Session) -> Optional[int]:
    """Pick a category id for an imported transaction.

    Only categories this user may use (their own + system ones) and of the right
    type (income vs expense) are considered. Previously the lookup ignored the
    user, so it could return ANOTHER user's category id.
    """
    wanted = TransactionType.INCOME if str(tx_type).upper() == "INCOME" else TransactionType.EXPENSE
    usable = or_(Category.user_id == user_id, Category.is_system == True)  # noqa: E712
    description = (description or "").strip()
    desc_clean = description.lower()

    def find_by_name(fragment: str) -> Optional[int]:
        cat = (
            db.query(Category)
            .filter(usable, Category.type == wanted, Category.name.ilike(f"%{_escape_like(fragment)}%", escape="\\"))
            .order_by(Category.user_id.is_(None))  # the user's own category before a system one
            .first()
        )
        return cat.id if cat else None

    # 1. Historical memory: how did the user categorise this merchant before?
    if description:
        historical = (
            db.query(Transaction)
            .join(Category, Transaction.category_id == Category.id)
            .filter(
                Transaction.user_id == user_id,
                Category.type == wanted,
                Transaction.description.ilike(f"%{_escape_like(description)}%", escape="\\"),
            )
            .order_by(Transaction.id.desc())
            .first()
        )
        if historical and historical.category_id:
            return historical.category_id

    # 2. Heuristic keyword matching
    for pattern, target_cat_name in DEFAULT_MERCHANT_PATTERNS:
        if pattern.search(desc_clean):
            matched = find_by_name(target_cat_name)
            if matched:
                return matched

    # 3. Fallback: "Uncategorized" of the right type, if the user has one
    return find_by_name("uncategorized")
