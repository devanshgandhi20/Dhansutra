from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List
from sqlalchemy.orm import Session
from sqlalchemy import extract, func, or_

from modules.finance.models import (
    Account,
    AccountType,
    Budget,
    Category,
    FinancialGoal,
    GoalContribution,
    Transaction,
    TransactionType,
)
from modules.finance.schemas import (
    AccountBalanceSummary,
    BudgetStatusResponse,
    DashboardSummaryResponse,
)


class FinanceService:

    @staticmethod
    def calculate_account_balances(db: Session, user_id: int) -> List[AccountBalanceSummary]:
        accounts = db.query(Account).filter(Account.user_id == user_id).all()
        summaries = []

        for acc in accounts:
            if acc.type == AccountType.INVESTMENT and acc.current_market_value is not None:
                current_balance = acc.current_market_value
            else:
                starting = acc.starting_balance or Decimal("0.00")

                # Income credited to this account
                income = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
                    Transaction.user_id == user_id,
                    Transaction.account_id == acc.id,
                    Transaction.type == TransactionType.INCOME,
                ).scalar()

                # Expenses debited from this account
                expense = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
                    Transaction.user_id == user_id,
                    Transaction.account_id == acc.id,
                    Transaction.type == TransactionType.EXPENSE,
                ).scalar()

                # Inward transfers
                transfers_in = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
                    Transaction.user_id == user_id,
                    Transaction.to_account_id == acc.id,
                    Transaction.type == TransactionType.TRANSFER,
                ).scalar()

                # Outward transfers
                transfers_out = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
                    Transaction.user_id == user_id,
                    Transaction.from_account_id == acc.id,
                    Transaction.type == TransactionType.TRANSFER,
                ).scalar()

                current_balance = Decimal(str(starting)) + Decimal(str(income)) - Decimal(str(expense)) + Decimal(str(transfers_in)) - Decimal(str(transfers_out))

            summaries.append(
                AccountBalanceSummary(
                    id=acc.id,
                    name=acc.name,
                    type=acc.type,
                    current_balance=current_balance,
                )
            )

        return summaries

    @staticmethod
    def get_monthly_budget_status(db: Session, user_id: int, month_str: str) -> List[BudgetStatusResponse]:
        year, month = map(int, month_str.split("-"))
        budgets = db.query(Budget).filter(Budget.user_id == user_id, Budget.month == month_str).all()

        results = []
        for b in budgets:
            actual_spent = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
                Transaction.user_id == user_id,
                Transaction.category_id == b.category_id,
                Transaction.type == TransactionType.EXPENSE,
                extract("year", Transaction.date) == year,
                extract("month", Transaction.date) == month,
            ).scalar()

            spent = Decimal(str(actual_spent))
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
    def get_dashboard_summary(db: Session, user_id: int, target_date: date) -> DashboardSummaryResponse:
        account_summaries = FinanceService.calculate_account_balances(db, user_id)

        total_assets = Decimal("0.00")
        total_liabilities = Decimal("0.00")

        for acc in account_summaries:
            if acc.type == AccountType.CREDIT_CARD:
                if acc.current_balance < 0:
                    total_liabilities += abs(acc.current_balance)
                else:
                    total_assets += acc.current_balance
            else:
                total_assets += acc.current_balance

        net_worth = total_assets - total_liabilities

        # Monthly figures
        year = target_date.year
        month = target_date.month

        monthly_income = Decimal(str(db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.INCOME,
            extract("year", Transaction.date) == year,
            extract("month", Transaction.date) == month,
        ).scalar()))

        monthly_expense = Decimal(str(db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.EXPENSE,
            extract("year", Transaction.date) == year,
            extract("month", Transaction.date) == month,
        ).scalar()))

        monthly_savings = monthly_income - monthly_expense
        savings_rate = round(float((monthly_savings / monthly_income) * 100), 2) if monthly_income > 0 else 0.0

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