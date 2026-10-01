import csv
import io
import json
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import SessionLocal
from modules.auth.userverification import get_current_user
from modules.finance.models import Account, Category, FinancialGoal, Transaction, TransactionType
from modules.finance.schemas import TransactionResponse
from modules.users.models import User

router = APIRouter(prefix="/api/finance/imports", tags=["Imports & Exports"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class StagedReceipt(BaseModel):
    source_id: str
    date: date
    amount: Decimal = Field(..., gt=0)
    description: str
    type: Optional[str] = None
    account_id: Optional[int] = None
    category_id: Optional[int] = None


class CommitStagedReceipts(BaseModel):
    receipts: List[StagedReceipt]


# -------------------------------------------------------------
# GMAIL / APPS SCRIPT RECEIPT SYNC (STAGED)
# -------------------------------------------------------------
@router.post("/stage-receipts", response_model=List[StagedReceipt])
def check_staged_receipts(
    incoming: List[StagedReceipt],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Receives parsed transactions from Gmail/Apps Script and returns only
    those that do not already exist in the database (preventing duplicates).
    """
    existing_ids = {
        row[0]
        for row in db.query(Transaction.source_id)
        .filter(Transaction.user_id == current_user.id, Transaction.source_id.isnot(None))
        .all()
    }

    return [item for item in incoming if item.source_id not in existing_ids]


@router.post("/commit-receipts", response_model=List[TransactionResponse], status_code=status.HTTP_201_CREATED)
def commit_receipts(
    payload: CommitStagedReceipts,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Commits approved receipts into real ledger transactions.
    """
    created = []
    for item in payload.receipts:
        # Final duplicate guard
        exists = (
            db.query(Transaction)
            .filter(Transaction.user_id == current_user.id, Transaction.source_id == item.source_id)
            .first()
        )
        if exists:
            continue

        # Convert the incoming string safely to the TransactionType enum
        raw_type = (item.type or "").strip().upper()
        try:
            tx_type = TransactionType[raw_type]
        except (KeyError, ValueError):
            tx_type = TransactionType.EXPENSE

        tx = Transaction(
            user_id=current_user.id,
            type=tx_type,
            amount=item.amount,
            date=item.date,
            description=item.description,
            account_id=item.account_id,
            category_id=item.category_id,
            source="GMAIL",
            source_id=item.source_id,
        )
        db.add(tx)
        created.append(tx)

    db.commit()
    for tx in created:
        db.refresh(tx)
    return created


# -------------------------------------------------------------
# BACKUP EXPORT (JSON & CSV)
# -------------------------------------------------------------
@router.get("/export/json")
def export_json(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = db.query(Account).filter(Account.user_id == current_user.id).all()
    transactions = db.query(Transaction).filter(Transaction.user_id == current_user.id).all()
    categories = db.query(Category).filter(Category.user_id == current_user.id).all()
    goals = db.query(FinancialGoal).filter(FinancialGoal.user_id == current_user.id).all()

    archive = {
        "export_date": datetime.utcnow().isoformat(),
        "accounts": [
            {
                "id": a.id,
                "name": a.name,
                "type": a.type.value,
                "starting_balance": float(a.starting_balance),
                "current_market_value": float(a.current_market_value) if a.current_market_value else None,
                "anchor_date": a.anchor_date.isoformat() if a.anchor_date else None,
            }
            for a in accounts
        ],
        "categories": [
            {"id": c.id, "name": c.name, "type": c.type.value, "icon": c.icon}
            for c in categories
        ],
        "transactions": [
            {
                "id": t.id,
                "type": t.type.value,
                "amount": float(t.amount),
                "date": t.date.isoformat(),
                "description": t.description,
                "category_id": t.category_id,
                "account_id": t.account_id,
                "from_account_id": t.from_account_id,
                "to_account_id": t.to_account_id,
                "source": t.source,
                "source_id": t.source_id,
            }
            for t in transactions
        ],
        "goals": [
            {
                "id": g.id,
                "name": g.name,
                "target_amount": float(g.target_amount),
                "saved_amount": float(g.saved_amount),
                "target_date": g.target_date.isoformat() if g.target_date else None,
            }
            for g in goals
        ],
    }

    return Response(
        content=json.dumps(archive, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=dhansutra_backup_{date.today()}.json"},
    )


@router.get("/export/csv")
def export_csv(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transactions = (
        db.query(Transaction)
        .filter(Transaction.user_id == current_user.id)
        .order_by(Transaction.date.desc())
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Date", "Type", "Amount", "Description", "Category", "Account", "Source"])

    for t in transactions:
        cat_name = t.category.name if t.category else ""
        acc_name = t.account.name if t.account else ""
        writer.writerow([t.id, t.date, t.type.value, t.amount, t.description, cat_name, acc_name, t.source])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=dhansutra_transactions_{date.today()}.csv"},
    )