import os
import re
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
# Adjust imports according to your models / database session
from database import get_db
from models import Transaction, Account, Category

router = APIRouter()

SYNC_SECRET_TOKEN = os.getenv("DHANSUTRA_SYNC_TOKEN", "choose_a_strong_random_secret_token_here")

# Built-in merchant keyword patterns for Indian transactions
DEFAULT_MERCHANT_PATTERNS = [
    (r"swiggy|zomato|eats|starbucks|mcdonalds|kfc|burger|domino|pizza", "Dining Out"),
    (r"blinkit|zepto|instamart|bigbasket|dmart|reliance fresh|nature basket", "Groceries"),
    (r"uber|ola|rapido|metro|petrol|fuel|hpcl|bpcl|ioc|irctc|redbus", "Fuel & Transit"),
    (r"bescom|tata power|airtel|jio|vi |billdesk|electricity|gas|water", "Utilities"),
    (r"netflix|spotify|prime|youtube|hotstar|apple|google play", "Subscriptions"),
    (r"rent|landlord|nobroker|nestaway", "House Rent"),
    (r"salary|wages|payroll|stipend", "Salary & Wages"),
]

class IncomingTx(BaseModel):
    type: str # "EXPENSE" or "INCOME"
    amount: float
    description: str
    date: str
    source_id: str

class WebhookPayload(BaseModel):
    transactions: List[IncomingTx]

def resolve_category(description: str, tx_type: str, db: Session) -> Optional[int]:
    desc_clean = description.lower()
    
    # 1. Historical Memory Check: Did the user categorize this merchant before?
    historical = (
        db.query(Transaction)
        .filter(
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

    # 3. Fallback: Default to "Uncategorized" or a generic bucket
    fallback_cat = (
        db.query(Category)
        .filter(Category.name.ilike("%uncategorized%"))
        .first()
    )
    return fallback_cat.id if fallback_cat else None

@router.post("/api/finance/imports/webhook-ingest")
def webhook_ingest_transactions(
    payload: WebhookPayload,
    x_sync_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    # Verify authentication token
    if not x_sync_token or x_sync_token != SYNC_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized webhook source")

    # Resolve primary Axis Bank account (matching last 4 0237 or named Axis)
    axis_account = (
        db.query(Account)
        .filter(Account.name.ilike("%axis%"))
        .first()
    )
    if not axis_account:
        axis_account = db.query(Account).filter(Account.type == "BANK").first()
    if not axis_account:
        raise HTTPException(status_code=400, detail="No active bank account found to credit/debit")

    added_count = 0
    for item in payload.transactions:
        # Deduplication: check if source_id (Gmail messageId) was already recorded
        existing = (
            db.query(Transaction)
            .filter(Transaction.source_id == item.source_id)
            .first()
        )
        if existing:
            continue

        # Auto-resolve category
        category_id = resolve_category(item.description, item.type, db)

        new_tx = Transaction(
            type=item.type.upper(),
            amount=item.amount,
            date=item.date,
            description=item.description,
            account_id=axis_account.id,
            category_id=category_id,
            source_id=item.source_id,
            # If your schema supports a review flag, you can set it here:
            # needs_review=(category_id is None or is_uncategorized)
        )
        db.add(new_tx)

        # Update running account balance
        if item.type.upper() == "EXPENSE":
            axis_account.current_balance = (axis_account.current_balance or 0) - item.amount
        elif item.type.upper() == "INCOME":
            axis_account.current_balance = (axis_account.current_balance or 0) + item.amount

        added_count += 1

    db.commit()
    return {"status": "success", "imported": added_count}