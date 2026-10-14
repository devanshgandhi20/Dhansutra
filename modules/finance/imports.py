import csv
import io
import ipaddress
import json
import os
import socket
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, Response, status
from pydantic import BaseModel, Field
from modules.finance.automate import WebhookPayload, resolve_category
from sqlalchemy.orm import Session

from modules.auth.userverification import get_db, get_current_user
from modules.users.models import User
from modules.finance.models import Account, Category, FinancialGoal, Transaction, TransactionType

router = APIRouter(prefix="/api/finance/imports", tags=["Imports"])

ALLOWED_HOST = "script.google.com"
ALLOWED_PATH_PREFIX = "/macros/s/"
SYNC_SECRET_TOKEN = os.getenv("DHANSUTRA_SYNC_TOKEN", "choose_a_strong_random_secret_token_here")

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
def extract_source_id(item: dict) -> str:
    """Helper to reliably extract Gmail messageId across all script versions."""
    raw_id = item.get("source_id") or item.get("sourceId") or item.get("id")
    if raw_id:
        return str(raw_id).strip()
    # Deterministic fallback if messageId is somehow completely absent
    d = str(item.get("date", "")).strip()
    a = str(item.get("amount", "")).strip()
    desc = str(item.get("description", "")).strip().lower()
    return f"gen_{d}_{a}_{desc}"


@router.post("/fetch-external")
async def fetch_external_receipts(
    payload: SyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    safe_url = validate_external_script_url(payload.script_url)

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=False) as client:
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
    if not raw_items:
        return []

    # 1. Collect all candidate IDs (checking source_id, sourceId, and id)
    candidate_ids = [extract_source_id(item) for item in raw_items]

    # 2. Query DB for matches belonging to this user
    existing_ids = set()
    if candidate_ids:
        query = (
            db.query(Transaction.source_id)
            .filter(
                Transaction.user_id == current_user.id,
                Transaction.source_id.in_(candidate_ids),
            )
            .all()
        )
        existing_ids = {str(r[0]).strip() for r in query if r[0]}

    # 3. Stage ONLY unimported items
    staged = []
    for item in raw_items:
        sid = extract_source_id(item)
        if sid in existing_ids:
            continue  # Already saved in DB, skip!

        raw_type = str(item.get("type", "EXPENSE")).upper()
        tx_type = "INCOME" if any(k in raw_type for k in ["INC", "CREDIT"]) else "EXPENSE"

        raw_date = str(item.get("date", "")).strip()
        try:
            parsed_date = datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            parsed_date = date.today().isoformat()

        staged.append({
            "source_id": sid,
            "date": parsed_date,
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
    committed_count = 0
    committed_ids = []

    for item in payload.transactions:
        sid = str(item.source_id).strip() if item.source_id else None

        # Double check existence to prevent duplicates if source_id is present
        if sid:
            exists = db.query(Transaction.id).filter(
                Transaction.user_id == current_user.id,
                Transaction.source_id == sid,
            ).first()
            if exists:
                continue

        # Validate account ownership
        acc = db.query(Account).filter(
            Account.id == item.account_id,
            Account.user_id == current_user.id
        ).first()
        if not acc:
            continue

        clean_desc = (item.description or "").strip()
        if not clean_desc:
            continue

        raw_type = str(item.type).strip().upper()
        tx_type = TransactionType.INCOME if raw_type == "INCOME" else TransactionType.EXPENSE
        tx_amount = Decimal(str(item.amount))

        # Parse date safely (supports YYYY-MM-DD or ISO strings)
        date_str = str(item.date).split("T")[0]
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()

        tx = Transaction(
            user_id=current_user.id,
            account_id=acc.id,
            category_id=item.category_id,
            amount=tx_amount,
            date=parsed_date,
            description=clean_desc,
            type=tx_type,
            source="GMAIL",
            source_id=sid,
        )
        db.add(tx)

        # Update static current_balance on Account if maintained directly
        if hasattr(acc, "current_balance") and acc.current_balance is not None:
            if tx_type == TransactionType.EXPENSE:
                acc.current_balance -= tx_amount
            else:
                acc.current_balance += tx_amount

        committed_count += 1
        if sid:
            committed_ids.append(sid)

    db.commit()
    return {
        "committed_count": committed_count,
        "source_ids": committed_ids
    }

@router.post("/webhook-ingest")
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
            user_id=axis_account.user_id,
            type=item.type.upper(),
            amount=item.amount,
            date=item.date,
            description=item.description,
            account_id=axis_account.id,
            category_id=category_id,
            source_id=item.source_id,
            source="GMAIL",
            # If your schema supports a review flag, you can set it here:
            # needs_review=(category_id is None or is_uncategorized)
        )
        db.add(new_tx)

        added_count += 1

    db.commit()
    return {"status": "success", "imported": added_count}

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