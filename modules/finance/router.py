from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_

from core.database import SessionLocal
from modules.users.models import User
from modules.auth.userverification import get_current_user
from modules.finance.models import Account, Category, Transaction, TransactionType
from modules.finance.schemas import (
    AccountCreate,
    AccountUpdate,
    AccountResponse,
    CategoryCreate,
    CategoryResponse,
    TransactionCreate,
    TransactionResponse,
    TransferCreate,
)

router = APIRouter(prefix="/api/finance", tags=["Finance"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------------------------------------------
# ACCOUNTS
# -------------------------------------------------------------
@router.get("/accounts", response_model=List[AccountResponse])
def get_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Account).filter(Account.user_id == current_user.id).all()


@router.post("/accounts", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(
    data: AccountCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = Account(**data.model_dump(), user_id=current_user.id)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int,
    data: AccountUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(Account)
        .filter(Account.id == account_id, Account.user_id == current_user.id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(account, key, value)

    db.commit()
    db.refresh(account)
    return account


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(Account)
        .filter(Account.id == account_id, Account.user_id == current_user.id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    db.delete(account)
    db.commit()
    return None


# -------------------------------------------------------------
# CATEGORIES (User + System defaults)
# -------------------------------------------------------------
@router.get("/categories", response_model=List[CategoryResponse])
def get_categories(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(Category)
        .filter(or_(Category.user_id == current_user.id, Category.is_system == True))
        .all()
    )


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    data: CategoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    category = Category(
        **data.model_dump(),
        user_id=current_user.id,
        is_system=False,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    category = (
        db.query(Category)
        .filter(Category.id == category_id, Category.user_id == current_user.id)
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Category not found or is a system category")

    db.delete(category)
    db.commit()
    return None


# -------------------------------------------------------------
# TRANSACTIONS & TRANSFERS
# -------------------------------------------------------------
@router.get("/transactions", response_model=List[TransactionResponse])
def get_transactions(
    account_id: Optional[int] = Query(None),
    type: Optional[TransactionType] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Transaction).filter(Transaction.user_id == current_user.id)

    if account_id:
        query = query.filter(
            or_(
                Transaction.account_id == account_id,
                Transaction.from_account_id == account_id,
                Transaction.to_account_id == account_id,
            )
        )
    if type:
        query = query.filter(Transaction.type == type)

    return query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()


@router.post("/transactions", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    data: TransactionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.type == TransactionType.TRANSFER:
        raise HTTPException(
            status_code=400,
            detail="Transfers must be created via /api/finance/transfers",
        )

    if data.account_id:
        account = (
            db.query(Account)
            .filter(Account.id == data.account_id, Account.user_id == current_user.id)
            .first()
        )
        if not account:
            raise HTTPException(status_code=404, detail="Selected account not found")

    if data.category_id:
        category = (
            db.query(Category)
            .filter(
                Category.id == data.category_id,
                or_(Category.user_id == current_user.id, Category.is_system == True),
            )
            .first()
        )
        if not category:
            raise HTTPException(status_code=404, detail="Selected category not found")

    # Duplicate check if source_id is provided (e.g., from external sync or receipts)
    if data.source_id:
        duplicate = (
            db.query(Transaction)
            .filter(Transaction.user_id == current_user.id, Transaction.source_id == data.source_id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Transaction with this source_id already exists")

    tx = Transaction(**data.model_dump(), user_id=current_user.id)
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


@router.post("/transfers", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transfer(
    data: TransferCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.from_account_id == data.to_account_id:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same account")

    from_acc = (
        db.query(Account)
        .filter(Account.id == data.from_account_id, Account.user_id == current_user.id)
        .first()
    )
    to_acc = (
        db.query(Account)
        .filter(Account.id == data.to_account_id, Account.user_id == current_user.id)
        .first()
    )

    if not from_acc or not to_acc:
        raise HTTPException(status_code=404, detail="Source or destination account not found")

    transfer_tx = Transaction(
        user_id=current_user.id,
        type=TransactionType.TRANSFER,
        amount=data.amount,
        date=data.date,
        description=data.description,
        from_account_id=from_acc.id,
        to_account_id=to_acc.id,
        source="MANUAL",
    )

    db.add(transfer_tx)
    db.commit()
    db.refresh(transfer_tx)
    return transfer_tx


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tx = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id, Transaction.user_id == current_user.id)
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    db.delete(tx)
    db.commit()
    return None

from datetime import date, datetime
from modules.finance.models import Budget, FinancialGoal, GoalContribution
from modules.finance.schemas import (
    BudgetCreate,
    BudgetResponse,
    BudgetStatusResponse,
    GoalCreate,
    GoalResponse,
    GoalContributionCreate,
    GoalContributionResponse,
    DashboardSummaryResponse,
)
from modules.finance.services import FinanceService

# -------------------------------------------------------------
# BUDGETS
# -------------------------------------------------------------
@router.get("/budgets", response_model=List[BudgetResponse])
def get_budgets(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Budget).filter(Budget.user_id == current_user.id, Budget.month == month).all()


@router.post("/budgets", response_model=BudgetResponse, status_code=status.HTTP_201_CREATED)
def set_budget(
    data: BudgetCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    category = db.query(Category).filter(
        Category.id == data.category_id,
        or_(Category.user_id == current_user.id, Category.is_system == True),
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    existing_budget = db.query(Budget).filter(
        Budget.user_id == current_user.id,
        Budget.category_id == data.category_id,
        Budget.month == data.month,
    ).first()

    if existing_budget:
        existing_budget.limit_amount = data.limit_amount
        db.commit()
        db.refresh(existing_budget)
        return existing_budget

    budget = Budget(**data.model_dump(), user_id=current_user.id)
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return budget


@router.get("/budgets/status", response_model=List[BudgetStatusResponse])
def get_budget_status(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FinanceService.get_monthly_budget_status(db, current_user.id, month)


# -------------------------------------------------------------
# FINANCIAL GOALS & CONTRIBUTIONS
# -------------------------------------------------------------
@router.get("/goals", response_model=List[GoalResponse])
def get_goals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(FinancialGoal).filter(FinancialGoal.user_id == current_user.id).all()


@router.post("/goals", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
def create_goal(
    data: GoalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    goal = FinancialGoal(**data.model_dump(), user_id=current_user.id, saved_amount=Decimal("0.00"))
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


@router.post("/goals/{goal_id}/contribute", response_model=GoalContributionResponse, status_code=status.HTTP_201_CREATED)
def contribute_to_goal(
    goal_id: int,
    data: GoalContributionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    goal = db.query(FinancialGoal).filter(
        FinancialGoal.id == goal_id,
        FinancialGoal.user_id == current_user.id,
    ).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    account = db.query(Account).filter(
        Account.id == data.account_id,
        Account.user_id == current_user.id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Source account not found")

    # Record the contribution
    contribution = GoalContribution(
        goal_id=goal.id,
        account_id=account.id,
        amount=data.amount,
        date=data.date,
        note=data.note,
    )
    db.add(contribution)

    # Sync saved amount on the goal
    goal.saved_amount += data.amount

    # Create corresponding outward ledger transaction
    tx = Transaction(
        user_id=current_user.id,
        type=TransactionType.EXPENSE,
        amount=data.amount,
        date=data.date,
        description=f"Goal Reserve: {goal.name} - {data.note or ''}".strip(),
        account_id=account.id,
        source="MANUAL",
    )
    db.add(tx)

    db.commit()
    db.refresh(contribution)
    return contribution


# -------------------------------------------------------------
# DASHBOARD SUMMARY
# -------------------------------------------------------------
@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    date_str: Optional[str] = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    return FinanceService.get_dashboard_summary(db, current_user.id, target)

@router.put("/transactions/{transaction_id}", response_model=TransactionResponse)
def update_transaction(
    transaction_id: int,
    payload: dict,  # Or your schema e.g. TransactionUpdate
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tx = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id, Transaction.user_id == current_user.id)
        .first()
    )
    if not tx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    # Update fields
    if "amount" in payload and payload["amount"] is not None:
        tx.amount = payload["amount"]
    if "date" in payload and payload["date"]:
        tx.date = payload["date"]
    if "description" in payload and payload["description"]:
        tx.description = payload["description"]
    if "type" in payload and payload["type"]:
        raw_type = str(payload["type"]).upper()
        tx.type = TransactionType[raw_type] if raw_type in TransactionType.__members__ else tx.type
    if "account_id" in payload:
        tx.account_id = payload["account_id"]
    if "category_id" in payload:
        tx.category_id = payload["category_id"]
    if "from_account_id" in payload:
        tx.from_account_id = payload["from_account_id"]
    if "to_account_id" in payload:
        tx.to_account_id = payload["to_account_id"]

    db.commit()
    db.refresh(tx)
    return tx