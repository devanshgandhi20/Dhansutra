import os
import re
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
# Adjust imports according to your models / database session
from core.database import SessionLocal
from modules.finance.models import Transaction, Account, Category

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