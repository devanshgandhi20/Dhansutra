import datetime as _dt
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from modules.auth.userverification import get_current_user, get_db
from modules.finance.models import (
    Account, AccountType, Category, IgnoredSourceId, Transaction,
    TransactionType, Budget, FinancialGoal,
    GoalContribution, RecurringTransaction, RecurringFrequency
)
from modules.finance.schemas import (
    AccountCreate, AccountUpdate, AccountResponse,
    CategoryCreate, CategoryResponse,
    TransactionCreate, TransactionResponse, TransactionUpdate,
    TransferCreate, BudgetCreate, BudgetResponse, BudgetStatusResponse,
    GoalCreate, GoalResponse, GoalContributionCreate, GoalContributionResponse,
    DashboardSummaryResponse,
    RecurringCreate, RecurringUpdate, RecurringResponse, GoalContributionReversalCreate,
)
from modules.finance.services import FinanceService
from modules.users.models import User

router = APIRouter(prefix="/api/finance", tags=["Finance"])

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


# -------------------------------------------------------------
# SHARED HELPERS
# -------------------------------------------------------------
def get_owned_account(db: Session, user_id: int, account_id: int, label: str = "Account") -> Account:
    """Account by id, but only if it belongs to this user (404 otherwise)."""
    account = db.query(Account).filter(Account.id == account_id, Account.user_id == user_id).first()
    if not account:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return account


def get_usable_category(db: Session, user_id: int, category_id: int) -> Category:
    """Category the user may use: their own or a system one (404 otherwise)."""
    category = db.query(Category).filter(
        Category.id == category_id,
        or_(Category.user_id == user_id, Category.is_system == True),  # noqa: E712
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Selected category not found")
    return category


def _shift_investment_value(from_acc: Optional[Account], to_acc: Optional[Account], amount: Decimal, reverse: bool = False) -> None:
    """Keep a manually entered market value in step with money moved into / out of an investment account.

    Only touches accounts whose valuation was set by hand. When current_market_value is
    empty, the account value is derived from its ledger balance (which already includes
    the transfer), so adding to a missing value would make it too small. That was the old bug.
    """
    sign = Decimal("-1") if reverse else Decimal("1")
    if to_acc is not None and to_acc.type == AccountType.INVESTMENT and to_acc.current_market_value is not None:
        to_acc.current_market_value = max(Decimal("0"), to_acc.current_market_value + sign * amount)
    if from_acc is not None and from_acc.type == AccountType.INVESTMENT and from_acc.current_market_value is not None:
        from_acc.current_market_value = max(Decimal("0"), from_acc.current_market_value - sign * amount)

def reject_goal_reserve_account(
    account: Account,
    label: str = "Selected account",
) -> None:
    """
    Ordinary financial operations must not change
    Goal & Reserves balances.

    Goal contributions use their dedicated endpoint.
    """
    if account.type == AccountType.GOAL_RESERVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{label} cannot be a Goal & Reserves account "
                "for ordinary financial operations. "
                "Use the goal contribution workflow."
            ),
        )

def is_goal_reserve_transaction(
    db: Session,
    tx: Transaction,
    user_id: int,
) -> bool:
    """
    Protect any existing transaction involving a Goal & Reserves
    account, including historical income, expense and transfers.
    """
    account_ids = {
        account_id
        for account_id in (
            tx.account_id,
            tx.from_account_id,
            tx.to_account_id,
        )
        if account_id is not None
    }

    if not account_ids:
        return False

    return (
        db.query(Account.id)
        .filter(
            Account.user_id == user_id,
            Account.id.in_(account_ids),
            Account.type == AccountType.GOAL_RESERVE,
        )
        .first()
        is not None
    )

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
    # PH2.1: Reserve accounts must start at zero.
    # Their balances must come from recorded goal contributions.
    if (
        data.type == AccountType.GOAL_RESERVE
        and Decimal(str(data.starting_balance)) != Decimal("0")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "A Goal & Reserves account must start "
                "with a zero balance."
            ),
        )
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
    account = get_owned_account(db, current_user.id, account_id)

    update_data = data.model_dump(exclude_unset=True)
    # PH2.1: An existing ordinary account cannot become
    # a Goal & Reserves account.
    if (
        account.type != AccountType.GOAL_RESERVE
        and update_data.get("type") == AccountType.GOAL_RESERVE
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "An existing account cannot be converted "
                "into a Goal & Reserves account."
            ),
        )

    # PH2.1: Preserve reserve-account identity and initial balance.
    # Changing either can invalidate goal accounting and
    # historical-transfer protection.
    if account.type == AccountType.GOAL_RESERVE:

        new_type = update_data.get("type")

        if new_type is not None and new_type != account.type:
            raise HTTPException(
                status_code=409,
                detail="The Goal & Reserves account type cannot be changed."
            )

        new_balance = update_data.get("starting_balance")

        if (
            new_balance is not None
            and Decimal(str(new_balance))
            != Decimal(str(account.starting_balance))
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "The Goal & Reserves starting balance cannot "
                    "be changed directly."
                )
            )

    for key, value in update_data.items():
        if value is None and key in ("name", "type", "starting_balance"):
            continue

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
    account = get_owned_account(db, current_user.id, account_id)
    # PH2.1: Protect Goal & Reserves accounts.
    # Even historical reserve balances may not have complete
    # transaction-to-contribution relationships.
    if account.type == AccountType.GOAL_RESERVE:
        raise HTTPException(
            status_code=409,
            detail=(
                "The Goal & Reserves account cannot be deleted "
                "directly. Its balances and goal allocations "
                "must be reconciled first."
            ),
        )

    # PH2.1: An account may have historical goal contributions
    # even when its original financial transfer is missing.
    has_goal_contributions = (
        db.query(GoalContribution.id)
        .filter(GoalContribution.account_id == account_id)
        .first()
        is not None
    )

    if has_goal_contributions:
        raise HTTPException(
            status_code=409,
            detail=(
                "This account is referenced by financial goal "
                "contributions and cannot be deleted."
            )
        )

    # Deleting an account used to silently delete every transaction on it, and left
    # transfers pointing at nothing. Refuse instead, so history is never lost by accident.
    has_transactions = db.query(Transaction.id).filter(
        Transaction.user_id == current_user.id,
        or_(
            Transaction.account_id == account_id,
            Transaction.from_account_id == account_id,
            Transaction.to_account_id == account_id,
        ),
    ).first()
    if has_transactions:
        raise HTTPException(
            status_code=409,
            detail="This account has transactions. Move or delete them first so no history is lost.",
        )
    has_recurring = db.query(RecurringTransaction.id).filter(
        RecurringTransaction.user_id == current_user.id,
        or_(
            RecurringTransaction.account_id == account_id,
            RecurringTransaction.to_account_id == account_id,
        ),
    ).first()
    if has_recurring:
        raise HTTPException(
            status_code=409,
            detail="This account is used by a recurring item. Delete or change that item first.",
        )

    db.delete(account)
    db.commit()
    return None


# -------------------------------------------------------------
# CATEGORIES
# -------------------------------------------------------------
@router.get("/categories", response_model=List[CategoryResponse])
def get_categories(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(Category)
        .filter(or_(Category.user_id == current_user.id, Category.is_system == True))  # noqa: E712
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


class DeleteCategoryRequest(BaseModel):
    move_to_category_id: Optional[int] = None


@router.delete("/categories/{category_id}", status_code=status.HTTP_200_OK)
def delete_category(
    category_id: int,
    payload: Optional[DeleteCategoryRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == current_user.id,
        Category.is_system == False  # noqa: E712
    ).first()
    if not cat:
        raise HTTPException(
            status_code=404,
            detail="Category not found or is a protected system category"
        )

    if payload and payload.move_to_category_id:
        if payload.move_to_category_id == category_id:
            raise HTTPException(status_code=400, detail="Choose a different category to move transactions to")
        target_cat = db.query(Category).filter(
            Category.id == payload.move_to_category_id,
            or_(Category.user_id == current_user.id, Category.is_system == True)  # noqa: E712
        ).first()
        if not target_cat:
            raise HTTPException(status_code=400, detail="Target reassignment category does not exist")
        if target_cat.type != cat.type:
            raise HTTPException(
                status_code=400,
                detail=f"Move them to another {cat.type.value.lower()} category so income and expenses are not mixed up."
            )

        db.query(Transaction).filter(
            Transaction.user_id == current_user.id,
            Transaction.category_id == category_id
        ).update({"category_id": payload.move_to_category_id})
        # recurring items that use this category move with it (otherwise they end up with no category)
        db.query(RecurringTransaction).filter(
            RecurringTransaction.user_id == current_user.id,
            RecurringTransaction.category_id == category_id
        ).update({"category_id": payload.move_to_category_id})
    else:
        in_use = db.query(Transaction.id).filter(
            Transaction.user_id == current_user.id,
            Transaction.category_id == category_id
        ).first() or db.query(RecurringTransaction.id).filter(
            RecurringTransaction.user_id == current_user.id,
            RecurringTransaction.category_id == category_id
        ).first()
        if in_use:
            raise HTTPException(
                status_code=400,
                detail="Category is in use. Choose a destination category to reassign its transactions."
            )

    db.delete(cat)
    db.commit()
    return {"status": "success", "deleted_category_id": category_id}


# -------------------------------------------------------------
# TRANSACTIONS & TRANSFERS
# -------------------------------------------------------------
@router.get("/transactions", response_model=List[TransactionResponse])
def get_transactions(
    month: Optional[str] = Query(None, pattern=MONTH_PATTERN),
    account_id: Optional[int] = None,
    type: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Transaction).filter(Transaction.user_id == current_user.id)

    if month:
        year_str, month_str = month.split("-")
        yr, mo = int(year_str), int(month_str)
        num_days = monthrange(yr, mo)[1]
        start_date = _dt.date(yr, mo, 1)
        end_date = _dt.date(yr, mo, num_days)
        query = query.filter(Transaction.date >= start_date, Transaction.date <= end_date)

    if account_id:
        query = query.filter(
            or_(
                Transaction.account_id == account_id,
                Transaction.from_account_id == account_id,
                Transaction.to_account_id == account_id,
            )
        )
    if type and type.upper() != "ALL":
        if type.upper() not in TransactionType.__members__:
            raise HTTPException(status_code=400, detail="Unknown transaction type")
        query = query.filter(Transaction.type == TransactionType[type.upper()])
    if search:
        query = query.filter(Transaction.description.ilike(f"%{search.strip()}%"))

    return query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()


@router.post("/transactions", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    data: TransactionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.type == TransactionType.TRANSFER:
        raise HTTPException(status_code=400, detail="Transfers must be created via /api/finance/transfers")
    if data.account_id is not None:
        account = get_owned_account(
            db,
            current_user.id,
            data.account_id,
            "Selected account",
        )

        reject_goal_reserve_account(
            account,
            "Selected account",
        )

    if data.category_id:
        get_usable_category(db, current_user.id, data.category_id)

    if data.source_id:
        duplicate = db.query(Transaction).filter(
            Transaction.user_id == current_user.id,
            Transaction.source_id == data.source_id
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="Transaction with this source_id already exists")

    tx = Transaction(**data.model_dump(), user_id=current_user.id)
    db.add(tx)
    try:
        db.commit()
    except IntegrityError:
        # lost a race against another request carrying the same source_id
        db.rollback()
        raise HTTPException(status_code=409, detail="Transaction with this source_id already exists")
    db.refresh(tx)
    return tx


@router.put("/transactions/{transaction_id}", response_model=TransactionResponse)
def update_transaction(
    transaction_id: int,
    data: TransactionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tx = db.query(Transaction).filter(
        Transaction.id == transaction_id,
        Transaction.user_id == current_user.id
    ).first()
    if not tx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    # PH2.1: Goal-funded transfers must not be edited independently.
    # The contribution's saved amount would otherwise become inconsistent.
    # PH2.2: Protect both original funding and reversal transfers.
    linked_contribution = (
        db.query(GoalContribution.id)
        .filter(
            or_(
                GoalContribution.transaction_id == tx.id,
                GoalContribution.reversal_transaction_id == tx.id,
            )
        )
        .first()
    )
    if (
        linked_contribution is not None
        or is_goal_reserve_transaction(db, tx, current_user.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This transfer belongs to a financial goal contribution "
                "and cannot be edited directly."
            )
        )

    update_data = data.model_dump(exclude_unset=True)

    for required in ("amount", "date", "type"):
        if required in update_data and update_data[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be empty")

    new_type = update_data.get("type")
    if new_type is not None:
        if new_type == TransactionType.TRANSFER and tx.type != TransactionType.TRANSFER:
            raise HTTPException(status_code=400, detail="Transfers must be created via /api/finance/transfers")
        if tx.type == TransactionType.TRANSFER and new_type != TransactionType.TRANSFER:
            raise HTTPException(status_code=400, detail="A transfer cannot be changed into income or expense")

    # Every account / category id the client sends must belong to this user.
    # (This used to be unchecked, so another user's ids could be attached.)
    for field in ("account_id", "from_account_id", "to_account_id"):
        if update_data.get(field) is not None:
            account = get_owned_account(
                db,
                current_user.id,
                update_data[field],
                "Selected account",
            )

            reject_goal_reserve_account(
                account,
                field,
            )
    if update_data.get("category_id") is not None:
        get_usable_category(db, current_user.id, update_data["category_id"])

    if tx.type == TransactionType.TRANSFER:
        from_id = update_data.get("from_account_id", tx.from_account_id)
        to_id = update_data.get("to_account_id", tx.to_account_id)
        if from_id and to_id and from_id == to_id:
            raise HTTPException(status_code=400, detail="Cannot transfer to the same account")

    for field, value in update_data.items():
        setattr(tx, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Transaction with this source_id already exists")
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

    from_acc = db.query(Account).filter(Account.id == data.from_account_id, Account.user_id == current_user.id).first()
    to_acc = db.query(Account).filter(Account.id == data.to_account_id, Account.user_id == current_user.id).first()
    # Step 1: Confirm both accounts exist
    if not from_acc or not to_acc:
        raise HTTPException(
            status_code=404,
            detail="Source or destination account not found",
        )
    # PH2.1: Ordinary transfers cannot move money
    # into or out of Goal & Reserves.
    reject_goal_reserve_account(
        from_acc,
        "Source account",
    )

    reject_goal_reserve_account(
        to_acc,
        "Destination account",
    )

    tx_amount = Decimal(str(data.amount))

    # Balances are derived from the ledger (see FinanceService), so recording the row is enough.
    transfer_tx = Transaction(
        user_id=current_user.id,
        account_id=from_acc.id,
        from_account_id=from_acc.id,
        to_account_id=to_acc.id,
        type=TransactionType.TRANSFER,
        amount=tx_amount,
        date=data.date,
        description=data.description,
        source="MANUAL",
    )
    db.add(transfer_tx)

    # Keep hand-entered market values of INVESTMENT accounts in step with the money moved
    _shift_investment_value(from_acc, to_acc, tx_amount)

    db.commit()
    db.refresh(transfer_tx)
    return transfer_tx


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tx = db.query(Transaction).filter(
        Transaction.id == transaction_id,
        Transaction.user_id == current_user.id
    ).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # PH2.1: Do not allow deleting a transfer that funds a goal.
    # PH2.2: Preserve original and reversal ledger transactions.
    linked_contribution = (
        db.query(GoalContribution.id)
        .filter(
            or_(
                GoalContribution.transaction_id == tx.id,
                GoalContribution.reversal_transaction_id == tx.id,
            )
        )
        .first()
    )
    if (
        linked_contribution is not None
        or is_goal_reserve_transaction(db, tx, current_user.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This transfer belongs to a financial goal contribution "
                "and cannot be deleted directly."
            )
        )

    # Balances are derived from the ledger, so deleting the row corrects them automatically.
    # Only a hand-entered investment market value needs undoing for transfers.
    if tx.type == TransactionType.TRANSFER:
        from_id = tx.from_account_id or tx.account_id
        from_acc = db.query(Account).filter(Account.id == from_id, Account.user_id == current_user.id).first() if from_id else None
        to_acc = db.query(Account).filter(Account.id == tx.to_account_id, Account.user_id == current_user.id).first() if tx.to_account_id else None
        _shift_investment_value(from_acc, to_acc, Decimal(str(tx.amount)), reverse=True)

    # A transaction imported from Gmail is remembered as deleted, so the next sync does not bring it back.
    if tx.source_id:
        already = db.query(IgnoredSourceId.id).filter(
            IgnoredSourceId.user_id == current_user.id,
            IgnoredSourceId.source_id == tx.source_id,
        ).first()
        if not already:
            db.add(IgnoredSourceId(user_id=current_user.id, source_id=tx.source_id))

    db.delete(tx)
    db.commit()
    return None


# -------------------------------------------------------------
# BUDGETS
# -------------------------------------------------------------
@router.get("/budgets", response_model=List[BudgetResponse])
def get_budgets(
    month: str = Query(..., pattern=MONTH_PATTERN),
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
    get_usable_category(db, current_user.id, data.category_id)

    def find_existing():
        return db.query(Budget).filter(
            Budget.user_id == current_user.id,
            Budget.category_id == data.category_id,
            Budget.month == data.month,
        ).first()

    existing_budget = find_existing()
    if existing_budget:
        existing_budget.limit_amount = data.limit_amount
        db.commit()
        db.refresh(existing_budget)
        return existing_budget

    budget = Budget(**data.model_dump(), user_id=current_user.id)
    db.add(budget)
    try:
        db.commit()
    except IntegrityError:
        # two saves for the same category+month raced; the unique constraint caught it, so update instead
        db.rollback()
        budget = find_existing()
        if not budget:
            raise
        budget.limit_amount = data.limit_amount
        db.commit()
    db.refresh(budget)
    return budget


@router.get("/budgets/status", response_model=List[BudgetStatusResponse])
def get_budget_status(
    month: str = Query(..., pattern=MONTH_PATTERN),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return FinanceService.get_monthly_budget_status(db, current_user.id, month)


# -------------------------------------------------------------
# GOALS & CONTRIBUTIONS
# -------------------------------------------------------------
@router.get("/goals", response_model=List[GoalResponse])
def get_goals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    include_archived: bool = False,
):
    query = (
        db.query(FinancialGoal)
        .options(selectinload(FinancialGoal.contributions))
        .filter(FinancialGoal.user_id == current_user.id)
    )

    # PH2.2: The normal dashboard should display only
    # active goals. Historical goals remain accessible
    # through ?include_archived=true.
    if not include_archived:
        query = query.filter(
            FinancialGoal.closed_at.is_(None)
        )

    return query.all()

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


@router.delete("/goals/{goal_id}", status_code=status.HTTP_200_OK)
def delete_goal(
    goal_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    goal = (
        db.query(FinancialGoal)
        .filter(
            FinancialGoal.id == goal_id,
            FinancialGoal.user_id == current_user.id,
        )
        .with_for_update()
        .first()
    )

    if not goal:
        raise HTTPException(
            status_code=404,
            detail="Goal not found",
        )
    # PH2.2: Archived goals retain their financial history.
    # They must not be physically deleted through this endpoint.
    if goal.closed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This goal is archived and cannot be deleted.",
        )
    # Protection: Never allow deleting the primary Emergency Fund
    if "emergency" in (goal.name or "").lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Emergency Fund target is required and cannot be deleted."
        )
    # PH2.1: Prevent deleting funded goals.
    # Contributions and their ledger transfers must remain consistent.
    has_contributions = (
        db.query(GoalContribution.id)
        .filter(GoalContribution.goal_id == goal.id)
        .first()
        is not None
    )

    saved_amount = Decimal(str(goal.saved_amount or "0.00"))

    if has_contributions or saved_amount != Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This goal has saved funds or contribution history "
                "and cannot be deleted. Reverse active contributions, "
                "then archive the goal."
            )
        )

    # 1. Clean up associated GoalContribution records
    db.query(GoalContribution).filter(GoalContribution.goal_id == goal.id).delete()

    # 2. Delete the goal itself
    db.delete(goal)
    db.commit()
    return {"status": "success", "deleted_goal_id": goal_id}
# -------------------------------------------------------------
# PH2.2: SAFE GOAL ARCHIVAL
# -------------------------------------------------------------
@router.post(
    "/goals/{goal_id}/archive",
    status_code=status.HTTP_200_OK,
)
def archive_goal(
    goal_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Lock the goal to serialize archival with
    # contribution creation and reversal.
    goal = (
        db.query(FinancialGoal)
        .filter(
            FinancialGoal.id == goal_id,
            FinancialGoal.user_id == current_user.id,
        )
        .with_for_update()
        .first()
    )

    if goal is None:
        raise HTTPException(
            status_code=404,
            detail="Goal not found",
        )

    if goal.closed_at is not None:
        raise HTTPException(
            status_code=409,
            detail="This goal is already archived.",
        )

    # Preserve the existing Emergency Fund protection.
    if "emergency" in (goal.name or "").lower():
        raise HTTPException(
            status_code=400,
            detail="The Emergency Fund target cannot be archived.",
        )

    saved_amount = Decimal(str(goal.saved_amount or "0.00"))

    if saved_amount != Decimal("0.00"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Goal cannot be archived while saved funds remain. "
                "Reverse all active contributions first."
            ),
        )

    # Use the same lock order as contribution reversal:
    # goal first, then its contribution records.
    contributions = (
        db.query(GoalContribution)
        .filter(GoalContribution.goal_id == goal.id)
        .order_by(GoalContribution.id.asc())
        .with_for_update()
        .all()
    )

    # Goals without contribution history can use the
    # existing DELETE endpoint instead.
    if not contributions:
        raise HTTPException(
            status_code=409,
            detail=(
                "This goal has no contribution history. "
                "Use the normal delete operation."
            ),
        )

    for contribution in contributions:
        has_reversal_tx = (
            contribution.reversal_transaction_id is not None
        )
        has_reversal_time = (
            contribution.reversed_at is not None
        )

        # Both reversal fields must be populated together.
        if has_reversal_tx != has_reversal_time:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Goal contains an incomplete reversal record. "
                    "Reconciliation is required."
                ),
            )

        if not has_reversal_tx:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Goal contains contributions that have "
                    "not been reversed."
                ),
            )

        # Every reversed contribution must still retain
        # the original PH2.1 funding-transfer reference.
        if contribution.transaction_id is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Goal contains legacy contribution records "
                    "requiring reconciliation."
                ),
            )

        # PH2.2: Verify the actual original funding transfer.
        original_tx = (
            db.query(Transaction)
            .filter(
                Transaction.id == contribution.transaction_id,
                Transaction.user_id == current_user.id,
            )
            .first()
        )

        # Verify the actual compensating reversal transfer.
        reversal_tx = (
            db.query(Transaction)
            .filter(
                Transaction.id == contribution.reversal_transaction_id,
                Transaction.user_id == current_user.id,
            )
            .first()
        )

        if original_tx is None or reversal_tx is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Goal contains missing or inaccessible "
                    "ledger transactions. Reconciliation is required."
                ),
            )

        amount = Decimal(str(contribution.amount))

        # Validate the original contribution:
        # original source -> Goal & Reserves.
        if (
            amount <= Decimal("0.00")
            or original_tx.id != contribution.transaction_id
            or original_tx.user_id != current_user.id
            or original_tx.type != TransactionType.TRANSFER
            or original_tx.account_id != contribution.account_id
            or original_tx.from_account_id != contribution.account_id
            or original_tx.to_account_id is None
            or original_tx.to_account_id == contribution.account_id
            or Decimal(str(original_tx.amount)) != amount
            or original_tx.date != contribution.date
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Original contribution and funding transfer "
                    "are inconsistent. Reconciliation is required."
                ),
            )

        # Validate the reversal:
        # Goal & Reserves -> original funding source.
        if (
            reversal_tx.id != contribution.reversal_transaction_id
            or reversal_tx.user_id != current_user.id
            or reversal_tx.type != TransactionType.TRANSFER
            or reversal_tx.account_id != original_tx.to_account_id
            or reversal_tx.from_account_id != original_tx.to_account_id
            or reversal_tx.to_account_id != contribution.account_id
            or Decimal(str(reversal_tx.amount)) != amount
            or reversal_tx.date < contribution.date
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Contribution reversal does not match "
                    "the original funding transfer. "
                    "Reconciliation is required."
                ),
            )
        # PH2.2: Verify that the original transfer
        # actually funded a Goal & Reserves account.
        reserve = (
            db.query(Account)
            .filter(
                Account.id == original_tx.to_account_id,
                Account.user_id == current_user.id,
                Account.type == AccountType.GOAL_RESERVE,
            )
            .first()
        )

        # Explicit validation is retained so that
        # inconsistent records cannot pass reconciliation.
        if (
            reserve is None
            or reserve.id != original_tx.to_account_id
            or reserve.user_id != current_user.id
            or reserve.type != AccountType.GOAL_RESERVE
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Original contribution does not reference "
                    "a valid Goal & Reserves account owned "
                    "by this user. Reconciliation is required."
                ),
            )

    # Archive without deleting any historical records.
    goal.closed_at = datetime.utcnow()

    db.commit()

    return {
        "status": "success",
        "archived_goal_id": goal.id,
    }

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
    ).with_for_update().first()  # lock the goal so two simultaneous contributions both count
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    # PH2.2: Closed goals are read-only.
    # Do not allow new contributions after archival.
    if goal.closed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This goal is archived and cannot receive new contributions.",
        )

    account = get_owned_account(db, current_user.id, data.account_id, "Source account")

    # PH2.1: A goal contribution must be funded from
    # a non-reserve account.
    if account.type == AccountType.GOAL_RESERVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A Goal & Reserves account cannot fund "
                "another goal contribution. "
                "Select a bank, cash, or other permitted source account."
            ),
        )

    if account.type == AccountType.CREDIT_CARD:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot contribute to goals using a Credit Card")

    contribution = GoalContribution(
        goal_id=goal.id,
        account_id=account.id,
        amount=data.amount,
        date=data.date,
        note=data.note,
    )
    db.add(contribution)
    goal.saved_amount = (goal.saved_amount or Decimal("0.00")) + data.amount

    # Money moves from the source account into the user's Goal & Reserves account (a transfer, not an expense),
    # so net worth is unchanged and the reserve account's balance shows what is set aside.
    reserve = (
        db.query(Account)
        .filter(Account.user_id == current_user.id, Account.type == AccountType.GOAL_RESERVE)
        .order_by(Account.id.asc())
        .first()
    )
    if reserve is None:
        reserve = Account(
            user_id=current_user.id,
            name="Goal & Reserves",
            type=AccountType.GOAL_RESERVE,
            starting_balance=Decimal("0.00"),
        )
        db.add(reserve)
        db.flush()
    if reserve.id == account.id:
        raise HTTPException(status_code=400, detail="Choose a source account other than the Goal & Reserves account")
    tx = Transaction(
        user_id=current_user.id,
        account_id=account.id,
        from_account_id=account.id,
        to_account_id=reserve.id,
        type=TransactionType.TRANSFER,
        amount=data.amount,
        date=data.date,
        description=f"Goal Reserve: {goal.name} - {data.note or ''}".strip(),
        source="MANUAL",
    )

    db.add(tx)

    # Flush both pending records so SQLAlchemy assigns a transaction ID.
    # This does not commit the database transaction.
    db.flush()

    # Establish the permanent link between the contribution
    # and the exact transfer that funded it.
    contribution.transaction_id = tx.id

    # Commit the contribution, transfer, and link together.
    db.commit()

    db.refresh(contribution)
    return contribution

# -------------------------------------------------------------
# PH2.2: GOAL CONTRIBUTION REVERSAL
# -------------------------------------------------------------
@router.post(
    "/goals/{goal_id}/contributions/{contribution_id}/reverse",
    response_model=GoalContributionResponse,
    status_code=status.HTTP_201_CREATED,
)
def reverse_goal_contribution(
    goal_id: int,
    contribution_id: int,
    data: GoalContributionReversalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Lock the goal first. All contribution and reversal
    # operations for this goal must use the same lock order.
    goal = (
        db.query(FinancialGoal)
        .filter(
            FinancialGoal.id == goal_id,
            FinancialGoal.user_id == current_user.id,
        )
        .with_for_update()
        .first()
    )

    if goal is None:
        raise HTTPException(404, "Goal not found")

    if goal.closed_at is not None:
        raise HTTPException(
            409, "Closed goals cannot have contributions reversed."
        )

    contribution = (
        db.query(GoalContribution)
        .filter(
            GoalContribution.id == contribution_id,
            GoalContribution.goal_id == goal.id,
        )
        .with_for_update()
        .first()
    )

    if contribution is None:
        raise HTTPException(404, "Goal contribution not found")

    # Reject completed or inconsistently recorded reversals.
    if (
        contribution.reversal_transaction_id is not None
        or contribution.reversed_at is not None
    ):
        raise HTTPException(
            409, "This contribution has already been reversed."
        )

    # PH2.1 legacy contributions may lack transaction links.
    # They cannot be reversed automatically.
    if contribution.transaction_id is None:
        raise HTTPException(
            409,
            "This contribution has no linked funding transfer. "
            "Manual reconciliation is required.",
        )

    amount = Decimal(str(contribution.amount))

    if amount <= Decimal("0.00"):
        raise HTTPException(
            409, "Contribution amount is invalid."
        )

    if data.date < contribution.date:
        raise HTTPException(
            400, "Reversal date cannot precede contribution date."
        )

    if data.date > date.today():
        raise HTTPException(
            400, "Reversal date cannot be in the future."
        )

    # Verify the original transaction belongs to the user.
    original_tx = (
        db.query(Transaction)
        .filter(
            Transaction.id == contribution.transaction_id,
            Transaction.user_id == current_user.id,
        )
        .first()
    )

    if original_tx is None:
        raise HTTPException(
            409, "Original funding transfer was not found."
        )

    # Verify the original source still belongs to the user.
    source = get_owned_account(
        db,
        current_user.id,
        contribution.account_id,
        "Original funding account",
    )

    # Ensure the stored contribution-to-transfer link
    # actually represents the expected movement of funds.
    if (
        original_tx.type != TransactionType.TRANSFER
        or original_tx.account_id != source.id
        or original_tx.from_account_id != source.id
        or Decimal(str(original_tx.amount)) != amount
        or original_tx.date != contribution.date
    ):
        raise HTTPException(
            409,
            "Original contribution and funding transfer do not match.",
        )

    # Identify and lock the exact reserve account that
    # received the original contribution.
    #
    # Locking this account serializes reversals across
    # different goals sharing the same reserve.
    reserve = (
        db.query(Account)
        .filter(
            Account.id == original_tx.to_account_id,
            Account.user_id == current_user.id,
            Account.type == AccountType.GOAL_RESERVE,
        )
        .with_for_update()
        .first()
    )

    if reserve is None or reserve.id == source.id:
        raise HTTPException(
            409, "Original Goal & Reserves account is invalid."
        )

    saved_amount = Decimal(str(goal.saved_amount or "0.00"))

    if saved_amount < amount:
        raise HTTPException(
            409,
            "Goal saved amount is insufficient for this reversal.",
        )

    # Account balances are ledger-derived, so check the
    # available reserve funds before creating the transfer.
    balances = FinanceService.calculate_account_balances(
        db, current_user.id
    )

    reserve_balance = next(
        (
            Decimal(str(account.current_balance))
            for account in balances
            if account.id == reserve.id
        ),
        None,
    )

    if reserve_balance is None or reserve_balance < amount:
        raise HTTPException(
            409,
            "Goal & Reserves has insufficient funds "
            "for this reversal.",
        )

    description = (
        f"Goal reversal #{contribution.id}: {goal.name}"
    )

    if data.note:
        description += f" - {data.note}"

    # The reverse transfer restores the original source's
    # ledger balance and decreases the reserve balance.
    reversal_tx = Transaction(
        user_id=current_user.id,
        account_id=reserve.id,
        from_account_id=reserve.id,
        to_account_id=source.id,
        type=TransactionType.TRANSFER,
        amount=amount,
        date=data.date,
        description=description[:255],
        source="MANUAL",
    )

    db.add(reversal_tx)
    db.flush()

    # Record both parts of the reversal in one transaction.
    goal.saved_amount = saved_amount - amount
    contribution.reversal_transaction_id = reversal_tx.id
    contribution.reversed_at = datetime.utcnow()

    # No separate investment valuation adjustment:
    # the original goal contribution did not apply one.
    # Investment valuation corrections belong to PH2.4.
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
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    return FinanceService.get_dashboard_summary(db, current_user.id, target)


# -------------------------------------------------------------
# RECURRING TRANSACTIONS
# -------------------------------------------------------------
def calculate_next_date(current_date: _dt.date, freq: RecurringFrequency) -> _dt.date:
    if freq == RecurringFrequency.WEEKLY:
        return current_date + _dt.timedelta(days=7)
    elif freq == RecurringFrequency.YEARLY:
        try:
            return current_date.replace(year=current_date.year + 1)
        except ValueError:
            return current_date.replace(year=current_date.year + 1, day=28)
    else:
        year = current_date.year + (1 if current_date.month == 12 else 0)
        month = 1 if current_date.month == 12 else current_date.month + 1
        max_days = monthrange(year, month)[1]
        day = min(current_date.day, max_days)
        return _dt.date(year, month, day)


def validate_recurring_refs(
    db: Session,
    user_id: int,
    tx_type: TransactionType,
    account_id: int,
    to_account_id: Optional[int],
    category_id: Optional[int],
) -> None:
    """Shared by create and update: every referenced account/category must belong to the user."""
    source_account = get_owned_account(
        db,
        user_id,
        account_id,
        "Selected account",
    )

    reject_goal_reserve_account(
        source_account,
        "Selected account",
    )

    if tx_type == TransactionType.TRANSFER:
        if not to_account_id:
            raise HTTPException(status_code=400, detail="Destination account required for recurring transfers")
        if account_id == to_account_id:
            raise HTTPException(status_code=400, detail="Source and destination cannot be identical")
        destination_account = get_owned_account(
            db,
            user_id,
            to_account_id,
            "Destination account",
        )

        reject_goal_reserve_account(
            destination_account,
            "Destination account",
        )
    else:
        if not category_id:
            raise HTTPException(status_code=400, detail="Category required for income or expense recurring items")
        get_usable_category(db, user_id, category_id)


@router.get("/recurring", response_model=List[RecurringResponse])
def get_recurring(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(RecurringTransaction)
        .filter(RecurringTransaction.user_id == current_user.id)
        .order_by(RecurringTransaction.next_date.asc())
        .all()
    )


@router.post("/recurring", response_model=RecurringResponse, status_code=status.HTTP_201_CREATED)
def create_recurring(
    data: RecurringCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    validate_recurring_refs(db, current_user.id, data.type, data.account_id, data.to_account_id, data.category_id)

    rec = RecurringTransaction(**data.model_dump(), created_at=datetime.utcnow(), user_id=current_user.id)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


@router.put("/recurring/{recurring_id}", response_model=RecurringResponse)
def update_recurring(
    recurring_id: int,
    data: RecurringUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = db.query(RecurringTransaction).filter(
        RecurringTransaction.id == recurring_id,
        RecurringTransaction.user_id == current_user.id
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring transaction not found")

    update_dict = data.model_dump(exclude_unset=True)

    for required in ("description", "amount", "type", "account_id", "frequency", "next_date"):
        if required in update_dict and update_dict[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be empty")

    validate_recurring_refs(
        db,
        current_user.id,
        update_dict.get("type", rec.type),
        update_dict.get("account_id", rec.account_id),
        update_dict.get("to_account_id", rec.to_account_id),
        update_dict.get("category_id", rec.category_id),
    )

    for field, value in update_dict.items():
        setattr(rec, field, value)

    db.commit()
    db.refresh(rec)
    return rec


@router.post("/recurring/{recurring_id}/execute", response_model=TransactionResponse)
def execute_recurring(
    recurring_id: int,
    expected_date: Optional[_dt.date] = Query(
        None,
        description="Due date the caller believes is being posted. If the item has already moved past it "
                    "(another tab or device posted it first) the call is refused with 409 instead of posting again.",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Row lock: two simultaneous requests for the same item are handled one after the other.
    rec = db.query(RecurringTransaction).filter(
        RecurringTransaction.id == recurring_id,
        RecurringTransaction.user_id == current_user.id
    ).with_for_update().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring transaction not found")

    if expected_date is not None and expected_date != rec.next_date:
        raise HTTPException(status_code=409, detail="This recurring item was already posted for that date")
    # PH2.1: Revalidate accounts before execution.
    # Existing recurring schedules may predate reserve protections.
    validate_recurring_refs(
        db,
        current_user.id,
        rec.type,
        rec.account_id,
        rec.to_account_id,
        rec.category_id,
    )

    if rec.type == TransactionType.TRANSFER:
        from_acc = get_owned_account(db, current_user.id, rec.account_id, "Source account")
        to_acc = get_owned_account(db, current_user.id, rec.to_account_id, "Destination account") if rec.to_account_id else None
        new_tx = Transaction(
            user_id=current_user.id,
            account_id=rec.account_id,
            type=TransactionType.TRANSFER,
            amount=rec.amount,
            date=rec.next_date,
            description=rec.description,
            from_account_id=rec.account_id,
            to_account_id=rec.to_account_id,
            source="RECURRING",
        )
        # same market-value handling as a manual transfer
        _shift_investment_value(from_acc, to_acc, Decimal(str(rec.amount)))
    else:
        new_tx = Transaction(
            user_id=current_user.id,
            account_id=rec.account_id,
            category_id=rec.category_id,
            type=rec.type,
            amount=rec.amount,
            date=rec.next_date,
            description=rec.description,
            source="RECURRING",
        )

    db.add(new_tx)
    rec.next_date = calculate_next_date(rec.next_date, rec.frequency)
    db.commit()
    db.refresh(new_tx)
    return new_tx


@router.delete("/recurring/{recurring_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recurring(
    recurring_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rec = db.query(RecurringTransaction).filter(
        RecurringTransaction.id == recurring_id,
        RecurringTransaction.user_id == current_user.id
    ).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recurring transaction not found")

    db.delete(rec)
    db.commit()
    return None
