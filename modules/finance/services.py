from datetime import date
from decimal import Decimal
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import extract, func

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


class FinanceService:

    @staticmethod
    def calculate_account_balances(db: Session, user_id: int) -> List[AccountBalanceSummary]:
        accounts = db.query(Account).filter(Account.user_id == user_id).all()
        # Query all transactions belonging to this user from the DB
        user_txs = db.query(Transaction).filter(Transaction.user_id == user_id).all()
        summaries: List[AccountBalanceSummary] = []

        for account in accounts:
            # 1. For Investment accounts, prioritize current_market_value
            if account.type == AccountType.INVESTMENT:
                balance = (
                    account.current_market_value
                    if account.current_market_value is not None
                    else account.starting_balance
                )
            else:
                # 2. Standard cash/bank accounts use starting balance + transactions
                account_inflows = sum(
                    (t.amount for t in user_txs if t.account_id == account.id and t.type == TransactionType.INCOME),
                    Decimal("0.00"),
                )
                account_outflows = sum(
                    (t.amount for t in user_txs if t.account_id == account.id and t.type == TransactionType.EXPENSE),
                    Decimal("0.00"),
                )

                # Transfers in and out
                transfers_in = sum(
                    (t.amount for t in user_txs if t.to_account_id == account.id and t.type == TransactionType.TRANSFER),
                    Decimal("0.00"),
                )
                transfers_out = sum(
                    (t.amount for t in user_txs if t.from_account_id == account.id and t.type == TransactionType.TRANSFER),
                    Decimal("0.00"),
                )

                balance = (
                    (account.starting_balance or Decimal("0.00"))
                    + account_inflows
                    + transfers_in
                    - account_outflows
                    - transfers_out
                )

            summaries.append(
                AccountBalanceSummary(
                    id=account.id,
                    name=account.name,
                    type=account.type,
                    current_balance=Decimal(str(balance or "0.00")),
                )
            )

        return summaries

    @staticmethod
    def get_monthly_budget_status(db: Session, user_id: int, month_str: str) -> List[BudgetStatusResponse]:
        year, month = map(int, month_str.split("-"))
        budgets = db.query(Budget).filter(Budget.user_id == user_id, Budget.month == month_str).all()

        results: List[BudgetStatusResponse] = []
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
                if acc.current_balance < Decimal("0.00"):
                    total_liabilities += abs(acc.current_balance)
                else:
                    total_assets += acc.current_balance
            else:
                total_assets += acc.current_balance

        net_worth = total_assets - total_liabilities

        # Monthly income and expense figures
        year = target_date.year
        month = target_date.month

        monthly_income = Decimal(
            str(
                db.query(func.coalesce(func.sum(Transaction.amount), 0))
                .filter(
                    Transaction.user_id == user_id,
                    Transaction.type == TransactionType.INCOME,
                    extract("year", Transaction.date) == year,
                    extract("month", Transaction.date) == month,
                )
                .scalar()
            )
        )

        monthly_expense = Decimal(
            str(
                db.query(func.coalesce(func.sum(Transaction.amount), 0))
                .filter(
                    Transaction.user_id == user_id,
                    Transaction.type == TransactionType.EXPENSE,
                    extract("year", Transaction.date) == year,
                    extract("month", Transaction.date) == month,
                )
                .scalar()
            )
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