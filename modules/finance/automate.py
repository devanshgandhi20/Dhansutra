import os
import re
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Adjust imports according to your models / database session
from core.database import SessionLocal
from modules.finance.models import Transaction, Account, Category, TransactionType

router = APIRouter()

SYNC_SECRET_TOKEN = os.getenv("DHANSUTRA_SYNC_TOKEN", "choose_a_strong_random_secret_token_here")

# Built-in merchant keyword patterns for regular Indian transactions
DEFAULT_MERCHANT_PATTERNS = [
    (r"swiggy|zomato|eats|starbucks|mcdonalds|kfc|burger|domino|pizza", "Dining Out"),
    (r"blinkit|zepto|instamart|bigbasket|dmart|reliance fresh|nature basket", "Groceries"),
    (r"uber|ola|rapido|metro|petrol|fuel|hpcl|bpcl|ioc|irctc|redbus|roppen", "Fuel & Transit"),
    (r"bescom|tata power|airtel|jio|vi |billdesk|electricity|gas|water", "Utilities"),
    (r"netflix|spotify|prime|youtube|hotstar|apple|google play", "Subscriptions"),
    (r"rent|landlord|nobroker|nestaway", "House Rent"),
    (r"salary|wages|payroll|stipend", "Salary & Wages"),
]

# Patterns detecting SIP / Mutual Fund NACH / ACH debits
SIP_TRANSFER_PATTERNS = r"ach-dr|nach|prudent|camspay|bse\s?star|mf|mutual\s?fund|uti|hdfc\s?amc|nippon|zerodha|groww"


class IncomingTx(BaseModel):
    type: str  # "EXPENSE" or "INCOME"
    amount: float
    description: str
    date: str
    source_id: Optional[str] = None


class WebhookPayload(BaseModel):
    transactions: List[IncomingTx]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_sip_transaction(description: str) -> bool:
    """Matches debit alerts against mutual fund ACH/NACH keywords."""
    return bool(re.search(SIP_TRANSFER_PATTERNS, description.lower()))


def resolve_category(description: str, tx_type: str, user_id: int, db: Session) -> Optional[int]:
    desc_clean = description.lower()

    # 1. Historical Memory Check: Did the user categorize this merchant before?
    historical = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.description.ilike(f"%{description.strip()}%"),
            Transaction.category_id.isnot(None)
        )
        .order_by(Transaction.id.desc())
        .first()
    )
    if historical and historical.category_id:
        return historical.category_id

    # 2. Heuristic Pattern Matching
    for pattern, target_cat_name in DEFAULT_MERCHANT_PATTERNS:
        if re.search(pattern, desc_clean):
            matched_cat = (
                db.query(Category)
                .filter(Category.name.ilike(f"%{target_cat_name}%"))
                .first()
            )
            if matched_cat:
                return matched_cat.id

    # 3. Fallback: Default to "Uncategorized"
    fallback_cat = (
        db.query(Category)
        .filter(Category.name.ilike("%uncategorized%"))
        .first()
    )
    return fallback_cat.id if fallback_cat else None


@router.post("/webhook-sync", status_code=status.HTTP_201_CREATED)
def sync_incoming_transactions(
    payload: WebhookPayload,
    x_sync_token: str = Header(...),
    db: Session = Depends(get_db),
    # If using authentication dependency: current_user: User = Depends(get_current_user)
):
    if x_sync_token != SYNC_SECRET_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid sync token")

    # Target default accounts
    axis_account = db.query(Account).filter(Account.name.ilike("%axis%")).first()
    if not axis_account:
        axis_account = db.query(Account).filter(Account.type == "BANK").first()

    mf_account = db.query(Account).filter(
        (Account.name.ilike("%mutual%")) | (Account.type == "INVESTMENT")
    ).first()

    processed_count = 0

    for item in payload.transactions:
        sid = str(item.source_id).strip() if item.source_id else None

        # Idempotency check: skip existing imports
        if sid:
            exists = db.query(Transaction.id).filter(Transaction.source_id == sid).first()
            if exists:
                continue

        parsed_date = datetime.strptime(item.date.split("T")[0], "%Y-%m-%d").date()
        tx_amount = Decimal(str(item.amount))
        desc = item.description.strip()

        # Check if transaction is an ACH/NACH Mutual Fund debit
        if is_sip_transaction(desc) and axis_account and mf_account:
            # Save as an inter-account TRANSFER: Axis -> Mutual Funds
            transfer_tx = Transaction(
                user_id=axis_account.user_id,
                account_id=axis_account.id,       # Originating account
                from_account_id=axis_account.id,
                to_account_id=mf_account.id,
                amount=tx_amount,
                date=parsed_date,
                description=f"SIP Transfer - {desc}",
                type=TransactionType.TRANSFER,
                category_id=None,
                source="GMAIL",
                source_id=sid,
            )
            db.add(transfer_tx)

            # Reconcile account balances directly
            if hasattr(axis_account, "current_balance") and axis_account.current_balance is not None:
                axis_account.current_balance -= tx_amount

            if hasattr(mf_account, "current_balance") and mf_account.current_balance is not None:
                mf_account.current_balance += tx_amount

        else:
            # Fallback to standard expense/income categorization
            account = axis_account or db.query(Account).first()
            if not account:
                continue

            raw_type = item.type.upper()
            tx_type = TransactionType.INCOME if raw_type == "INCOME" else TransactionType.EXPENSE
            cat_id = resolve_category(desc, raw_type, account.user_id, db)

            standard_tx = Transaction(
                user_id=account.user_id,
                account_id=account.id,
                category_id=cat_id,
                amount=tx_amount,
                date=parsed_date,
                description=desc,
                type=tx_type,
                source="GMAIL",
                source_id=sid,
            )
            db.add(standard_tx)

            if hasattr(account, "current_balance") and account.current_balance is not None:
                if tx_type == TransactionType.EXPENSE:
                    account.current_balance -= tx_amount
                else:
                    account.current_balance += tx_amount

        processed_count += 1

    db.commit()
    return {"status": "success", "processed_count": processed_count}