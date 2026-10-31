from datetime import date
from decimal import Decimal
from typing import Dict, List, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from modules.finance.models import (
    Account,
    AccountType,
    Budget,
    Transaction,
    TransactionType,
)
from modules.finance.schemas import (
    AccountBalanceSummary,
    BudgetStatusResponse,
    DashboardSummaryResponse,
)

ZERO = Decimal("0.00")


def _month_bounds(year: int, month: int) -> Tuple[date, date]:
    """First day of the month and first day of the next month (half-open range, index friendly)."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _dec(value) -> Decimal:
    return Decimal(str(value or "0"))


class FinanceService:

    @staticmethod
    def get_monthly_budget_status(db: Session, user_id: int, month_str: str) -> List[BudgetStatusResponse]:
        year, month = map(int, month_str.split("-"))
        start, end = _month_bounds(year, month)

        budgets = (
            db.query(Budget)
            .options(joinedload(Budget.category))
            .filter(Budget.user_id == user_id, Budget.month == month_str)
            .all()
        )
        if not budgets:
            return []

        # One grouped query for every category instead of one query per budget.
        spent_by_category: Dict[int, Decimal] = {
            category_id: _dec(total)
            for category_id, total in db.query(Transaction.category_id, func.coalesce(func.sum(Transaction.amount), 0))
            .filter(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.EXPENSE,
                Transaction.category_id.in_([b.category_id for b in budgets]),
                Transaction.date >= start,
                Transaction.date < end,
            )
            .group_by(Transaction.category_id)
            .all()
        }

        results: List[BudgetStatusResponse] = []
        for b in budgets:
            spent = spent_by_category.get(b.category_id, ZERO)
            limit = b.limit_amount
            remaining = limit - spent
            pct = round(float((spent / limit) * 100), 2) if limit > 0 else 0.0

            results.append(
                BudgetStatusResponse(
                    category_id=b.category_id,
                    category_name=b.category.name if b.category else "Unknown",
                    month=b.month,
                    limit_amount=limit,
                    spent_amount=spent,
                    remaining_amount=remaining,
                    usage_percentage=pct,
                )
            )

        return results

    @staticmethod
    def calculate_account_balances(db: Session, user_id: int) -> List[AccountBalanceSummary]:
        accounts = db.query(Account).filter(Account.user_id == user_id).all()

        # Four grouped queries in total (was four queries PER account).
        income: Dict[int, Decimal] = {}
        expense: Dict[int, Decimal] = {}
        for account_id, tx_type, total in (
            db.query(Transaction.account_id, Transaction.type, func.coalesce(func.sum(Transaction.amount), 0))
            .filter(
                Transaction.user_id == user_id,
                Transaction.account_id.isnot(None),
                Transaction.type.in_([TransactionType.INCOME, TransactionType.EXPENSE]),
            )
            .group_by(Transaction.account_id, Transaction.type)
            .all()
        ):
            (income if tx_type == TransactionType.INCOME else expense)[account_id] = _dec(total)

        transfers_in: Dict[int, Decimal] = {
            account_id: _dec(total)
            for account_id, total in db.query(Transaction.to_account_id, func.coalesce(func.sum(Transaction.amount), 0))
            .filter(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.TRANSFER,
                Transaction.to_account_id.isnot(None),
            )
            .group_by(Transaction.to_account_id)
            .all()
        }
        transfers_out: Dict[int, Decimal] = {
            account_id: _dec(total)
            for account_id, total in db.query(Transaction.from_account_id, func.coalesce(func.sum(Transaction.amount), 0))
            .filter(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.TRANSFER,
                Transaction.from_account_id.isnot(None),
            )
            .group_by(Transaction.from_account_id)
            .all()
        }

        summaries = []
        for acc in accounts:
            starting = _dec(acc.starting_balance)
            income_sum = income.get(acc.id, ZERO)
            expense_sum = expense.get(acc.id, ZERO)
            t_in = transfers_in.get(acc.id, ZERO)
            t_out = transfers_out.get(acc.id, ZERO)

            if acc.type == AccountType.CREDIT_CARD:
                # Credit cards: starting liability + expenses - payments/inflows + cash advances/outflows
                current_balance = starting + expense_sum - income_sum - t_in + t_out
            else:
                # Standard Bank, Cash, Demat, and Investment accounts
                current_balance = starting + income_sum - expense_sum + t_in - t_out

            # For investment accounts, use current_market_value if set, otherwise fallback to current_balance
            market_val = (
                Decimal(str(acc.current_market_value))
                if acc.current_market_value is not None and acc.current_market_value > 0
                else current_balance
            )

            summaries.append(
                AccountBalanceSummary(
                    id=acc.id,
                    name=acc.name,
                    type=acc.type,
                    starting_balance=starting,
                    current_balance=current_balance,
                    current_market_value=market_val,
                )
            )

        return summaries

    @staticmethod
    def get_dashboard_summary(db: Session, user_id: int, target_date: date) -> DashboardSummaryResponse:
        account_summaries = FinanceService.calculate_account_balances(db, user_id)

        total_assets = Decimal("0.00")
        total_liabilities = Decimal("0.00")

        for acc in account_summaries:
            if acc.type == AccountType.CREDIT_CARD:
                # A positive credit card balance is an outstanding debt (liability)
                # If negative, it indicates an overpayment credit (asset)
                if acc.current_balance > Decimal("0.00"):
                    total_liabilities += acc.current_balance
                else:
                    total_assets += abs(acc.current_balance)
            elif acc.type == AccountType.INVESTMENT:
                if (
                    acc.current_market_value is not None
                    and acc.current_market_value > 0
                ):
                    total_assets += acc.current_market_value
                else:
                    total_assets += acc.current_balance
            else:
                total_assets += acc.current_balance

        net_worth = total_assets - total_liabilities

        # PH2.4: Income uses its attributed reporting month.
        # Unattributed income uses the actual transaction month.
        # Expenses always use their actual transaction dates.
        start, end = _month_bounds(target_date.year, target_date.month)
        month_str = f"{target_date.year:04d}-{target_date.month:02d}"

        monthly_income = _dec(
            db.query(func.coalesce(func.sum(Transaction.amount), 0))
            .filter(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.INCOME,
                or_(
                    Transaction.income_period == month_str,
                    and_(
                        Transaction.income_period.is_(None),
                        Transaction.date >= start,
                        Transaction.date < end,
                    ),
                ),
            )
            .scalar()
        )

        monthly_expense = _dec(
            db.query(func.coalesce(func.sum(Transaction.amount), 0))
            .filter(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.EXPENSE,
                Transaction.date >= start,
                Transaction.date < end,
            )
            .scalar()
        )

        monthly_savings = monthly_income - monthly_expense
        savings_rate = (
            round(float((monthly_savings / monthly_income) * 100), 2)
            if monthly_income > 0
            else 0.0
        )

        return DashboardSummaryResponse(
            net_worth=net_worth,
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            monthly_income=monthly_income,
            monthly_expense=monthly_expense,
            monthly_savings=monthly_savings,
            savings_rate=savings_rate,
            accounts=account_summaries,
        )
