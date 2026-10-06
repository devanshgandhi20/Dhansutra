import csv
import io
import ipaddress
import json
import socket
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from modules.auth.userverification import get_db, get_current_user
from modules.users.models import User
from modules.finance.models import Account, Category, FinancialGoal, Transaction, TransactionType

router = APIRouter(prefix="/api/finance/imports", tags=["Imports"])

ALLOWED_HOST = "script.google.com"
ALLOWED_PATH_PREFIX = "/macros/s/"

class SyncRequest(BaseModel):
    script_url: str

class ReceiptItem(BaseModel):
    source_id: str
    date: str
    amount: float
    description: str
    type: str
    account_id: Optional[int] = None
    category_id: Optional[int] = None

class CommitReceiptsRequest(BaseModel):
    transactions: List[ReceiptItem]


def validate_external_script_url(url_str: str) -> str:
    parsed = urlparse(url_str.strip())

    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Only HTTPS endpoints are allowed")

    if parsed.netloc.lower() != ALLOWED_HOST:
        raise HTTPException(status_code=400, detail="Endpoint must be hosted strictly on script.google.com")

    if not parsed.path.startswith(ALLOWED_PATH_PREFIX) or not parsed.path.endswith("/exec"):
        raise HTTPException(status_code=400, detail="Invalid Google Apps Script web app URL format")

    # Anti-SSRF: Prevent DNS rebinding to internal or private subnets
    try:
        ip_list = socket.gethostbyname_ex(parsed.hostname)[2]
        for ip in ip_list:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
                raise HTTPException(status_code=400, detail="Target resolves to a restricted network address")
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Could not resolve Apps Script host")

    return parsed.geturl()


# -------------------------------------------------------------
# APPS SCRIPT / GMAIL BACKEND PROXY (SECURE SSRF-HARDENED)
# -------------------------------------------------------------
@router.post("/fetch-external")
async def fetch_external_receipts(
    payload: SyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    safe_url = validate_external_script_url(payload.script_url)

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        try:
            resp = await client.get(safe_url)
            if resp.is_redirect:
                redirect_target = resp.headers.get("location", "")
                parsed_loc = urlparse(redirect_target)
                if not (parsed_loc.scheme == "https" and parsed_loc.netloc.endswith("googleusercontent.com")):
                    raise HTTPException(status_code=502, detail="Untrusted redirect target from Google Apps Script")
                resp = await client.get(redirect_target)

            resp.raise_for_status()

            if len(resp.content) > 1_048_576:
                raise HTTPException(status_code=413, detail="Response payload exceeds 1MB limit")

            data = resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Apps Script endpoint timed out")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch external receipts: {str(e)}")

    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=400, detail=f"Apps Script Error: {data['error']}")

    raw_items = data if isinstance(data, list) else data.get("transactions", [])

    incoming_source_ids = [
        str(item.get("sourceId") or item.get("id"))
        for item in raw_items
        if item.get("sourceId") or item.get("id")
    ]

    existing_ids = set()
    if incoming_source_ids:
        query = db.query(Transaction.source_id).filter(
            Transaction.user_id == current_user.id,
            Transaction.source_id.in_(incoming_source_ids),
        ).all()
        existing_ids = {r[0] for r in query}

    staged = []
    for item in raw_items:
        sid = str(item.get("sourceId") or item.get("id") or f"gen_{item.get('date')}_{item.get('amount')}_{item.get('description')}")
        if sid in existing_ids:
            continue

        raw_type = str(item.get("type", "EXPENSE")).upper()
        tx_type = "INCOME" if "INC" in raw_type or raw_type == "CREDIT" else "EXPENSE"

        staged.append({
            "source_id": sid,
            "date": item.get("date") or date.today().isoformat(),
            "amount": abs(float(item.get("amount", 0))),
            "description": (item.get("description") or "Bank Outflow").strip()[:255],
            "type": tx_type,
        })

    return staged


@router.post("/commit-receipts", status_code=status.HTTP_201_CREATED)
def commit_receipts(
    payload: CommitReceiptsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    created = []
    for item in payload.transactions:
        exists = db.query(Transaction.id).filter(
            Transaction.user_id == current_user.id,
            Transaction.source_id == item.source_id,
        ).first()
        if exists:
            continue

        tx_type = TransactionType.INCOME if item.type.upper() == "INCOME" else TransactionType.EXPENSE

        tx = Transaction(
            user_id=current_user.id,
            account_id=item.account_id,
            category_id=item.category_id,
            amount=Decimal(str(item.amount)),
            date=datetime.strptime(item.date, "%Y-%m-%d").date(),
            description=item.description,
            type=tx_type,
            source="GMAIL",
            source_id=item.source_id,
        )
        db.add(tx)
        created.append(item.source_id)

    db.commit()
    return {"committed_count": len(created), "source_ids": created}


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