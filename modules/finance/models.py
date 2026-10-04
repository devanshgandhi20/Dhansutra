import enum
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    Date,
    DateTime,
    Boolean,
    ForeignKey,
    Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from core.database import Base


class AccountType(str, enum.Enum):
    BANK = "BANK"
    CASH = "CASH"
    CREDIT_CARD = "CREDIT_CARD"
    INVESTMENT = "INVESTMENT"
    GOAL_RESERVE = "GOAL_RESERVE"


class TransactionType(str, enum.Enum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    TRANSFER = "TRANSFER"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    type = Column(SAEnum(AccountType, name="account_type_enum"), nullable=False)
    starting_balance = Column(Numeric(14, 2), default=0.00, nullable=False)
    current_market_value = Column(Numeric(14, 2), nullable=True)  # Used for investments
    anchor_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="accounts")
    transactions = relationship(
        "Transaction",
        foreign_keys="Transaction.account_id",
        back_populates="account",
        cascade="all, delete-orphan",
    )


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # Null = System standard
    name = Column(String(100), nullable=False)
    type = Column(SAEnum(TransactionType, name="category_type_enum"), nullable=False)
    icon = Column(String(50), default="tag")
    is_system = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="categories")
    transactions = relationship("Transaction", back_populates="category")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(SAEnum(TransactionType, name="transaction_type_enum"), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    date = Column(Date, nullable=False, index=True)
    description = Column(String(255), nullable=True)

    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)

    # Double-entry link for transfers
    from_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    to_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)

    source = Column(String(50), default="MANUAL", nullable=False)  # MANUAL, GMAIL, CSV
    source_id = Column(String(255), nullable=True, index=True)    # Duplicate prevention for external sync
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")
    account = relationship("Account", foreign_keys=[account_id], back_populates="transactions")
    from_account = relationship("Account", foreign_keys=[from_account_id])
    to_account = relationship("Account", foreign_keys=[to_account_id])


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    month = Column(String(7), nullable=False)  # Format: YYYY-MM
    limit_amount = Column(Numeric(14, 2), nullable=False)

    user = relationship("User", back_populates="budgets")
    category = relationship("Category")


class FinancialGoal(Base):
    __tablename__ = "financial_goals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    target_amount = Column(Numeric(14, 2), nullable=False)
    saved_amount = Column(Numeric(14, 2), default=0.00, nullable=False)
    target_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="goals")
    contributions = relationship("GoalContribution", back_populates="goal", cascade="all, delete-orphan")


class GoalContribution(Base):
    __tablename__ = "goal_contributions"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("financial_goals.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    date = Column(Date, nullable=False)
    note = Column(String(255), nullable=True)

    goal = relationship("FinancialGoal", back_populates="contributions")
    account = relationship("Account")

# Add Frequency Enum
class RecurringFrequency(str, enum.Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


# Add RecurringTransaction Model
class RecurringTransaction(Base):
    __tablename__ = "recurring_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    description = Column(String(255), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    type = Column(SAEnum(TransactionType), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    frequency = Column(SAEnum(RecurringFrequency), nullable=False, default=RecurringFrequency.MONTHLY)
    next_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", backref="recurring_transactions")
    account = relationship("Account")
    category = relationship("Category")