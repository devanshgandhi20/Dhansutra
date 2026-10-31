from __future__ import annotations
import datetime as _dt
import re
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from modules.finance.models import AccountType, TransactionType, RecurringFrequency

# ---------------- BUDGETS ----------------
class BudgetBase(BaseModel):
    category_id: int
    month: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")  # YYYY-MM format
    limit_amount: Decimal = Field(..., gt=0)


class BudgetCreate(BudgetBase):
    pass


class BudgetResponse(BudgetBase):
    id: int
    user_id: int

    model_config = ConfigDict(from_attributes=True)


class BudgetStatusResponse(BaseModel):
    category_id: int
    category_name: str
    month: str
    limit_amount: Decimal
    spent_amount: Decimal
    remaining_amount: Decimal
    usage_percentage: float


# ---------------- GOALS & CONTRIBUTIONS ----------------
class GoalBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    target_amount: Decimal = Field(..., gt=0)
    target_date: Optional[_dt.date] = None


class GoalCreate(GoalBase):
    pass


class GoalContributionCreate(BaseModel):
    account_id: int
    amount: Decimal = Field(..., gt=0)
    date: _dt.date
    note: Optional[str] = Field(None, max_length=255)

class GoalContributionReversalCreate(BaseModel):
    date: _dt.date
    note: Optional[str] = Field(None, max_length=255)

class GoalContributionResponse(BaseModel):
    id: int
    goal_id: int
    account_id: int
    amount: Decimal
    date: _dt.date
    note: Optional[str]

    # Original PH2.1 funding transfer.
    # May be NULL for legacy contributions.
    transaction_id: Optional[int] = None

    # PH2.2 reversal information.
    reversal_transaction_id: Optional[int] = None
    reversed_at: Optional[_dt.datetime] = None

    model_config = ConfigDict(from_attributes=True)

class GoalResponse(GoalBase):
    id: int
    user_id: int
    saved_amount: Decimal
    created_at: _dt.datetime

    # NULL = active goal.
    # Non-NULL = archived goal.
    closed_at: Optional[_dt.datetime] = None

    contributions: List[GoalContributionResponse] = []

    model_config = ConfigDict(from_attributes=True)

# ---------------- DASHBOARD AGGREGATIONS ----------------
class AccountBalanceSummary(BaseModel):
    id: int
    name: str
    type: AccountType
    current_balance: Decimal
    current_market_value: Optional[Decimal] = None

    class Config:
        from_attributes = True


class DashboardSummaryResponse(BaseModel):
    net_worth: Decimal
    total_assets: Decimal
    total_liabilities: Decimal
    monthly_income: Decimal
    monthly_expense: Decimal
    monthly_savings: Decimal
    savings_rate: float
    accounts: List[AccountBalanceSummary]


# ---------------- ACCOUNTS ----------------
class AccountBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: AccountType
    starting_balance: Decimal = Field(default=Decimal("0.00"))
    current_market_value: Optional[Decimal] = None
    anchor_date: Optional[_dt.date] = None


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[AccountType] = None
    starting_balance: Optional[Decimal] = None
    current_market_value: Optional[Decimal] = None
    anchor_date: Optional[_dt.date] = None


class AccountResponse(AccountBase):
    id: int
    user_id: int
    created_at: _dt.datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------- CATEGORIES ----------------
class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: TransactionType
    icon: Optional[str] = Field(default="tag", max_length=50)


class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id: int
    user_id: Optional[int] = None
    is_system: bool
    created_at: _dt.datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------- TRANSACTIONS ----------------

_INCOME_PERIOD_RE = re.compile(
    r"^[0-9]{4}-(0[1-9]|1[0-2])$"
)


def validate_income_period(value):
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(
            "income_period must be YYYY-MM or null"
        )

    if not _INCOME_PERIOD_RE.fullmatch(value):
        raise ValueError(
            "income_period must be a valid YYYY-MM month"
        )

    return value


class TransactionBase(BaseModel):
    type: TransactionType
    amount: Decimal = Field(..., gt=0)
    date: _dt.date
    description: Optional[str] = Field(None, max_length=255)
    category_id: Optional[int] = None
    account_id: Optional[int] = None
    source: Optional[str] = Field(default="MANUAL", max_length=50)
    source_id: Optional[str] = Field(None, max_length=255)
    income_period: Optional[str] = None

    @field_validator("income_period", mode="before")
    @classmethod
    def _validate_income_period(cls, value):
        return validate_income_period(value)



class TransactionCreate(TransactionBase):
    pass


class TransferCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    date: _dt.date
    from_account_id: int
    to_account_id: int
    description: Optional[str] = Field(default="Account Transfer", max_length=255)


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    type: TransactionType
    amount: Decimal
    date: _dt.date
    description: Optional[str]
    category_id: Optional[int]
    account_id: Optional[int]
    from_account_id: Optional[int]
    to_account_id: Optional[int]
    source: str
    source_id: Optional[str]
    created_at: _dt.datetime
    income_period: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class TransactionUpdate(BaseModel):
    amount: Optional[Decimal] = Field(None, gt=0)
    date: Optional[_dt.date] = None
    description: Optional[str] = Field(None, max_length=255)
    type: Optional[TransactionType] = None
    account_id: Optional[int] = None
    category_id: Optional[int] = None
    from_account_id: Optional[int] = None
    to_account_id: Optional[int] = None
    source_id: Optional[str] = Field(None, max_length=255)
    income_period: Optional[str] = None

    @field_validator("income_period", mode="before")
    @classmethod
    def _validate_income_period(cls, value):
        return validate_income_period(value)

    model_config = ConfigDict(from_attributes=True, extra="ignore")

def _date_only(value):
    """The dashboard sends/receives ISO strings; accept '2026-10-16T00:00:00' too and keep only the date."""
    if isinstance(value, str) and "T" in value:
        return value.split("T", 1)[0]
    if isinstance(value, _dt.datetime):
        return value.date()
    return value


class RecurringBase(BaseModel):
    description: str = Field(..., min_length=1, max_length=255)
    amount: Decimal = Field(..., gt=0)
    type: TransactionType
    account_id: int
    to_account_id: Optional[int] = None
    category_id: Optional[int] = None
    frequency: RecurringFrequency
    # The column is a DATE; this used to be a datetime, which made the API return
    # "...T00:00:00" and forced the frontend to strip it everywhere.
    next_date: _dt.date

    @field_validator("next_date", mode="before")
    @classmethod
    def _next_date_only(cls, v):
        return _date_only(v)


class RecurringCreate(RecurringBase):
    pass


class RecurringUpdate(BaseModel):
    description: Optional[str] = Field(None, min_length=1, max_length=255)
    amount: Optional[Decimal] = Field(None, gt=0)
    type: Optional[TransactionType] = None
    account_id: Optional[int] = None
    to_account_id: Optional[int] = None
    category_id: Optional[int] = None
    frequency: Optional[RecurringFrequency] = None
    next_date: Optional[_dt.date] = None

    @field_validator("next_date", mode="before")
    @classmethod
    def _next_date_only(cls, v):
        return _date_only(v)


class RecurringResponse(RecurringBase):
    id: int
    user_id: int

    model_config = ConfigDict(from_attributes=True)