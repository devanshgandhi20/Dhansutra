from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from modules.finance.models import AccountType, TransactionType
from typing import List

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
    target_date: Optional[date] = None


class GoalCreate(GoalBase):
    pass


class GoalContributionCreate(BaseModel):
    account_id: int
    amount: Decimal = Field(..., gt=0)
    date: date
    note: Optional[str] = Field(None, max_length=255)


class GoalContributionResponse(BaseModel):
    id: int
    goal_id: int
    account_id: int
    amount: Decimal
    date: date
    note: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class GoalResponse(GoalBase):
    id: int
    user_id: int
    saved_amount: Decimal
    created_at: datetime
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
    anchor_date: Optional[date] = None


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[AccountType] = None
    starting_balance: Optional[Decimal] = None
    current_market_value: Optional[Decimal] = None
    anchor_date: Optional[date] = None


class AccountResponse(AccountBase):
    id: int
    user_id: int
    created_at: datetime

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
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------- TRANSACTIONS ----------------
class TransactionBase(BaseModel):
    type: TransactionType
    amount: Decimal = Field(..., gt=0)
    date: date
    description: Optional[str] = Field(None, max_length=255)
    category_id: Optional[int] = None
    account_id: Optional[int] = None
    source: Optional[str] = Field(default="MANUAL", max_length=50)
    source_id: Optional[str] = Field(None, max_length=255)


class TransactionCreate(TransactionBase):
    pass


class TransferCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    date: date
    from_account_id: int
    to_account_id: int
    description: Optional[str] = Field(default="Account Transfer", max_length=255)


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    type: TransactionType
    amount: Decimal
    date: date
    description: Optional[str]
    category_id: Optional[int]
    account_id: Optional[int]
    from_account_id: Optional[int]
    to_account_id: Optional[int]
    source: str
    source_id: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)