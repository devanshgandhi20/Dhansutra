import csv
import hmac
import io
import ipaddress
import json
import logging
import math
import re
import socket
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.concurrency import run_in_threadpool

from core.config import INSECURE_SYNC_TOKEN, settings
from modules.auth.userverification import get_current_user, get_db
from modules.finance.automate import WebhookPayload, resolve_category
from modules.finance.models import (
    Account, AccountType, Budget, Category, FinancialGoal, GoalContribution, IgnoredSourceId,
    RecurringTransaction, Transaction, TransactionType,
)
from modules.users.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/finance/imports", tags=["Imports"])

ALLOWED_HOST = "script.google.com"
SCRIPT_PATH_RE = re.compile(r"^/macros/s/[A-Za-z0-9_-]+/exec$")
MAX_RESPONSE_BYTES = 1_048_576


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


# -------------------------------------------------------------
# APPS SCRIPT / GMAIL BACKEND PROXY (SSRF-HARDENED)
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


def assert_public_resolution(hostname: str) -> None:
    """Resolve both IPv4 and IPv6 records and block any non-public IP ranges."""
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid hostname")
    try:
        addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Could not resolve host: {hostname}")

    for entry in addr_info:
        ip_str = entry[4][0]
        ip_obj = ipaddress.ip_address(ip_str)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail="Target resolves to a restricted network address",
            )


def validate_external_script_url(url_str: str) -> str:
    parsed = urlparse((url_str or "").strip())

    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Only HTTPS endpoints are allowed")

    if (parsed.hostname or "").lower() != ALLOWED_HOST or parsed.port not in (None, 443):
        raise HTTPException(
            status_code=400,
            detail="Endpoint must be hosted strictly on script.google.com",
        )

    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Credentials in URL are not allowed")

    if not SCRIPT_PATH_RE.match(parsed.path or ""):
        raise HTTPException(
            status_code=400,
            detail="Invalid Google Apps Script web app URL format (expected /macros/s/<id>/exec)",
        )

    assert_public_resolution(parsed.hostname)
    return parsed.geturl()


def validate_redirect_url(redirect_url: str) -> str:
    parsed = urlparse((redirect_url or "").strip())
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https" or parsed.port not in (None, 443):
        raise HTTPException(status_code=502, detail="Redirect must use HTTPS on standard port")

    if not (host == "script.googleusercontent.com" or host.endswith(".googleusercontent.com")):
        raise HTTPException(
            status_code=502,
            detail="Untrusted redirect target from Google Apps Script",
        )

    assert_public_resolution(host)
    return parsed.geturl()


def _existing_source_ids(db: Session, user_id: int, candidate_ids: List[str]) -> set:
    if not candidate_ids:
        return set()
    rows = (
        db.query(Transaction.source_id)
        .filter(
            Transaction.user_id == user_id,
            Transaction.source_id.in_(candidate_ids),
        )
        .all()
    )
    # ids the user deleted earlier count as "already handled" too, so they are never staged again
    ignored = (
        db.query(IgnoredSourceId.source_id)
        .filter(
            IgnoredSourceId.user_id == user_id,
            IgnoredSourceId.source_id.in_(candidate_ids),
        )
        .all()
    )
    return {str(r[0]).strip() for r in rows if r[0]} | {str(r[0]).strip() for r in ignored if r[0]}


def _is_ignored(db: Session, user_id: int, source_id: str) -> bool:
    return db.query(IgnoredSourceId.id).filter(
        IgnoredSourceId.user_id == user_id,
        IgnoredSourceId.source_id == source_id,
    ).first() is not None


@router.post("/fetch-external")
async def fetch_external_receipts(
    payload: SyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # DNS lookups and DB queries are blocking calls: run them in the thread pool so
    # a slow lookup cannot freeze every other request on the event loop.
    safe_url = await run_in_threadpool(validate_external_script_url, payload.script_url)

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=False) as client:
        try:
            resp = await client.get(safe_url)
            if resp.is_redirect:
                redirect_target = await run_in_threadpool(validate_redirect_url, resp.headers.get("location", ""))
                resp = await client.get(redirect_target)

            resp.raise_for_status()

            if len(resp.content) > MAX_RESPONSE_BYTES:
                raise HTTPException(status_code=413, detail="Response payload exceeds 1MB limit")

            data = resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Apps Script endpoint timed out")
        except HTTPException:
            raise
        except Exception:
            logger.exception("Fetching receipts from Apps Script failed")
            raise HTTPException(status_code=502, detail="Failed to fetch external receipts")

    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=400, detail=f"Apps Script Error: {data['error']}")

    raw_items = data if isinstance(data, list) else (data.get("transactions", []) if isinstance(data, dict) else [])
    if not isinstance(raw_items, list) or not raw_items:
        return []

    # 1. Collect all non-empty candidate IDs
    candidate_ids = list({
        str(sid).strip()
        for item in raw_items
        if isinstance(item, dict) and (sid := extract_source_id(item))
    })

    # 2. Query DB for matches belonging to this user
    existing_ids = await run_in_threadpool(_existing_source_ids, db, current_user.id, candidate_ids)

    # 3. Stage ONLY valid, unimported items
    staged = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        sid_raw = extract_source_id(item)
        sid = str(sid_raw).strip() if sid_raw else None
        if sid and sid in existing_ids:
            continue  # Already in DB or earlier in this same batch

        try:
            amount = abs(float(item.get("amount", 0)))
        except (ValueError, TypeError):
            continue
        if not math.isfinite(amount) or amount <= 0:
            continue

        raw_type = str(item.get("type", "EXPENSE")).upper()
        tx_type = "INCOME" if any(k in raw_type for k in ["INC", "CREDIT"]) else "EXPENSE"

        raw_date = str(item.get("date", "")).strip()
        try:
            parsed_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date().isoformat()
        except (ValueError, TypeError):
            parsed_date = date.today().isoformat()

        if sid:
            existing_ids.add(sid)

        staged.append({
            "source_id": sid,
            "date": parsed_date,
            "amount": round(amount, 2),
            "description": (str(item.get("description") or "Bank Outflow")).strip()[:255],
            "type": tx_type,
        })

    return staged


def _positive_amount(value) -> Optional[Decimal]:
    """Decimal(str(value)) if it is a finite number greater than zero, else None."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return None
        amount = amount.quantize(Decimal("0.01"))  # the column holds 2 decimal places
    except (InvalidOperation, ValueError):
        return None
    return amount if amount > 0 else None


@router.post("/commit-receipts", status_code=status.HTTP_201_CREATED)
def commit_receipts(
    payload: CommitReceiptsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Every category id sent must be one this user may use (checked up front, nothing is saved if not).
    category_ids = {i.category_id for i in payload.transactions if i.category_id}
    if category_ids:
        usable = {
            row[0]
            for row in db.query(Category.id).filter(
                Category.id.in_(category_ids),
                or_(Category.user_id == current_user.id, Category.is_system == True),  # noqa: E712
            ).all()
        }
        if category_ids - usable:
            raise HTTPException(status_code=404, detail="Selected category not found")

    accounts: Dict[int, Optional[Account]] = {}
    seen_ids = set()
    committed_count = 0
    skipped_count = 0
    committed_ids = []

    for item in payload.transactions:
        sid = str(item.source_id).strip() if item.source_id else None

        # Duplicate protection. `seen_ids` catches the same id twice in ONE request; the
        # session does not autoflush, so the database lookup alone could not see those.
        if sid:
            if sid in seen_ids:
                skipped_count += 1
                continue
            exists = db.query(Transaction.id).filter(
                Transaction.user_id == current_user.id,
                Transaction.source_id == sid,
            ).first()
            if exists or _is_ignored(db, current_user.id, sid):
                skipped_count += 1
                continue

        # Validate account ownership (each account looked up once)
        if item.account_id not in accounts:
            accounts[item.account_id] = db.query(Account).filter(
                Account.id == item.account_id,
                Account.user_id == current_user.id
            ).first() if item.account_id else None
        acc = accounts[item.account_id]
        if not acc:
            skipped_count += 1
            continue
        # PH2.1: Imported transactions must not change
        # Goal & Reserves balances independently of goals.
        if acc.type == AccountType.GOAL_RESERVE:
            skipped_count += 1
            continue

        clean_desc = (item.description or "").strip()[:255]
        if not clean_desc:
            skipped_count += 1
            continue

        raw_type = str(item.type).strip().upper()
        tx_type = TransactionType.INCOME if raw_type == "INCOME" else TransactionType.EXPENSE

        tx_amount = _positive_amount(item.amount)
        if tx_amount is None:
            skipped_count += 1
            continue

        # Parse date safely (supports YYYY-MM-DD or ISO strings); one bad row must not fail the whole batch
        try:
            parsed_date = datetime.strptime(str(item.date).split("T")[0], "%Y-%m-%d").date()
        except ValueError:
            skipped_count += 1
            continue

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
        # Account balances are derived from the ledger, so adding the row is all that is needed.
        try:
            with db.begin_nested():  # savepoint: a duplicate only drops this row
                db.add(tx)
        except IntegrityError:
            skipped_count += 1
            continue

        committed_count += 1
        if sid:
            seen_ids.add(sid)
            committed_ids.append(sid)

    db.commit()
    return {
        "committed_count": committed_count,
        "skipped_count": skipped_count,
        "source_ids": committed_ids
    }


def _resolve_sync_user(db: Session) -> User:
    """The webhook has no login, so it must know which user the mails belong to."""
    if settings.SYNC_USER_ID is not None:
        user = db.query(User).filter(User.id == settings.SYNC_USER_ID).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=400, detail="DHANSUTRA_SYNC_USER_ID does not match an active user")
        return user
    users = db.query(User).filter(User.is_active == True).limit(2).all()  # noqa: E712
    if len(users) == 1:
        return users[0]
    raise HTTPException(
        status_code=400,
        detail="Set DHANSUTRA_SYNC_USER_ID so the webhook knows which user the transactions belong to",
    )


@router.post("/webhook-ingest")
def webhook_ingest_transactions(
    payload: WebhookPayload,
    x_sync_token: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    # Refuse to run with the public placeholder token from the source code
    if settings.SYNC_TOKEN == INSECURE_SYNC_TOKEN:
        raise HTTPException(status_code=503, detail="Webhook is not configured: set DHANSUTRA_SYNC_TOKEN")

    # Verify authentication token (constant-time comparison)
    if not x_sync_token or not hmac.compare_digest(x_sync_token.encode(), settings.SYNC_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized webhook source")

    sync_user = _resolve_sync_user(db)

    # PH2.1: Gmail transactions must use a BANK account only.
    # Prefer the user's Axis Bank account, otherwise use
    # another BANK account belonging to the same user.
    axis_account = (
        db.query(Account)
        .filter(
            Account.user_id == sync_user.id,
            Account.type == AccountType.BANK,
            Account.name.ilike("%axis%"),
        )
        .first()
    )

    if not axis_account:
        axis_account = (
            db.query(Account)
            .filter(
                Account.user_id == sync_user.id,
                Account.type == AccountType.BANK,
            )
            .first()
        )
    if not axis_account:
        raise HTTPException(status_code=400, detail="No active bank account found to credit/debit")

    added_count = 0
    seen_ids = set()
    for item in payload.transactions:
        sid = item.source_id.strip() if item.source_id else None

        # Deduplication: check if source_id (Gmail messageId) was already recorded for this user
        if sid:
            if sid in seen_ids:
                continue
            existing = (
                db.query(Transaction.id)
                .filter(Transaction.user_id == sync_user.id, Transaction.source_id == sid)
                .first()
            )
            if existing or _is_ignored(db, sync_user.id, sid):
                continue

        amount = _positive_amount(abs(item.amount))
        if amount is None:
            continue
        try:
            parsed_date = datetime.strptime(item.date.split("T")[0], "%Y-%m-%d").date()
        except ValueError:
            continue

        tx_type = TransactionType.INCOME if item.type.strip().upper() == "INCOME" else TransactionType.EXPENSE
        description = item.description.strip()[:255]

        # Auto-resolve category. (The old call left out the user id argument, so every
        # request to this endpoint crashed with a TypeError.)
        category_id = resolve_category(description, tx_type.value, sync_user.id, db)

        new_tx = Transaction(
            user_id=sync_user.id,
            type=tx_type,
            amount=amount,
            date=parsed_date,
            description=description,
            account_id=axis_account.id,
            category_id=category_id,
            source_id=sid,
            source="GMAIL",
        )
        try:
            with db.begin_nested():
                db.add(new_tx)
        except IntegrityError:
            continue

        if sid:
            seen_ids.add(sid)
        added_count += 1

    db.commit()
    return {"status": "success", "imported": added_count}


# -------------------------------------------------------------
# BACKUP EXPORT (JSON & CSV)
# -------------------------------------------------------------
def _csv_safe(value):
    """Stop spreadsheet formula injection: a bank-alert text like '=HYPERLINK(...)' would
    otherwise execute when the CSV is opened in Excel / Sheets."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/export/json")
def export_json(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = db.query(Account).filter(Account.user_id == current_user.id).all()
    transactions = db.query(Transaction).filter(Transaction.user_id == current_user.id).all()
    # own categories + the system ones that transactions may point at
    categories = db.query(Category).filter(
        or_(Category.user_id == current_user.id, Category.is_system == True)  # noqa: E712
    ).all()
    goals = db.query(FinancialGoal).filter(FinancialGoal.user_id == current_user.id).all()
    budgets = db.query(Budget).filter(Budget.user_id == current_user.id).all()
    recurring = db.query(RecurringTransaction).filter(RecurringTransaction.user_id == current_user.id).all()
    contributions = (
        db.query(GoalContribution)
        .join(FinancialGoal, GoalContribution.goal_id == FinancialGoal.id)
        .filter(FinancialGoal.user_id == current_user.id)
        .all()
    )

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
            {"id": c.id, "name": c.name, "type": c.type.value, "icon": c.icon, "is_system": c.is_system}
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
        # The download is labelled "Full copy of your data"; these three were missing from it.
        "goal_contributions": [
            {
                "id": c.id,
                "goal_id": c.goal_id,
                "account_id": c.account_id,
                "amount": float(c.amount),
                "date": c.date.isoformat(),
                "note": c.note,
            }
            for c in contributions
        ],
        "budgets": [
            {
                "id": b.id,
                "category_id": b.category_id,
                "month": b.month,
                "limit_amount": float(b.limit_amount),
            }
            for b in budgets
        ],
        "recurring_transactions": [
            {
                "id": r.id,
                "description": r.description,
                "amount": float(r.amount),
                "type": r.type.value,
                "account_id": r.account_id,
                "to_account_id": r.to_account_id,
                "category_id": r.category_id,
                "frequency": r.frequency.value,
                "next_date": r.next_date.isoformat(),
            }
            for r in recurring
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
        .options(joinedload(Transaction.category), joinedload(Transaction.account))
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
        writer.writerow([
            t.id, t.date, t.type.value, t.amount,
            _csv_safe(t.description), _csv_safe(cat_name), _csv_safe(acc_name), _csv_safe(t.source),
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=dhansutra_transactions_{date.today()}.csv"},
    )
