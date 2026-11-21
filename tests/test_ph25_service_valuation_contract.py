
"""PH2.5: Test actual finance-service valuation expressions in isolation.

Extracts compiled statements from the service AST.
No application imports, database, or API calls.
"""

import ast
import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


D = Decimal

service_path = Path("modules/finance/services.py")
valuation_path = Path("modules/finance/valuation.py")

spec = importlib.util.spec_from_file_location(
    "ph25_valuation_for_service",
    valuation_path,
)
valuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(valuation)

syntax = ast.parse(
    service_path.read_text(encoding="utf-8-sig"),
    filename=str(service_path),
)

finance_class = next(
    node for node in syntax.body
    if isinstance(node, ast.ClassDef)
    and node.name == "FinanceService"
)

def method(name):
    matches = [
        node for node in finance_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"Unexpected service method: {name}")
    return matches[0]


balance_method = method("calculate_account_balances")
dashboard_method = method("get_dashboard_summary")

assignments = [
    node for node in ast.walk(balance_method)
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name)
        and target.id == "market_val"
        for target in node.targets
    )
]

if len(assignments) != 1:
    raise AssertionError("Expected one effective value assignment")

loops = [
    node for node in ast.walk(dashboard_method)
    if isinstance(node, ast.For)
    and isinstance(node.target, ast.Name)
    and node.target.id == "acc"
    and isinstance(node.iter, ast.Name)
    and node.iter.id == "account_summaries"
]

if len(loops) != 1:
    raise AssertionError("Expected one net-worth account loop")

def compile_statement(node):
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module, str(service_path), "exec")


market_code = compile_statement(assignments[0])
assets_code = compile_statement(loops[0])


class AccountType:
    INVESTMENT = "INVESTMENT"
    BANK = "BANK"
    CASH = "CASH"
    CREDIT_CARD = "CREDIT_CARD"
    GOAL_RESERVE = "GOAL_RESERVE"


class PH25ServiceValuationContract(unittest.TestCase):

    def calculate(self, kind, ledger, snapshot=None, anchor=None):
        acc = SimpleNamespace(
            type=kind,
            current_market_value=snapshot,
            valuation_ledger_anchor=anchor,
        )

        scope = {
            "acc": acc,
            "current_balance": ledger,
            "AccountType": AccountType,
            "effective_investment_value":
                valuation.effective_investment_value,
        }

        exec(market_code, scope)
        return scope["market_val"]

    def totals(self, accounts):
        scope = {
            "account_summaries": accounts,
            "total_assets": D("0"),
            "total_liabilities": D("0"),
            "AccountType": AccountType,
            "Decimal": Decimal,
        }

        exec(assets_code, scope)

        return (
            scope["total_assets"],
            scope["total_liabilities"],
        )

    def test_unvalued_investment_uses_ledger(self):
        self.assertEqual(
            self.calculate("INVESTMENT", D("12000")),
            D("12000"),
        )

    def test_legacy_zero_preserves_ledger_fallback(self):
        self.assertEqual(
            self.calculate(
                "INVESTMENT", D("12000"), D("0"), None
            ),
            D("12000"),
        )

    def test_positive_snapshot_tracks_transfer(self):
        self.assertEqual(
            self.calculate(
                "INVESTMENT",
                D("12000"),
                D("11000"),
                D("10000"),
            ),
            D("13000"),
        )

    def test_explicit_zero_does_not_fallback(self):
        self.assertEqual(
            self.calculate(
                "INVESTMENT",
                D("10000"),
                D("0"),
                D("10000"),
            ),
            D("0"),
        )

    def test_missing_positive_anchor_rejected(self):
        with self.assertRaises(ValueError):
            self.calculate(
                "INVESTMENT",
                D("10000"),
                D("11000"),
                None,
            )

    def test_bank_uses_ledger(self):
        self.assertEqual(
            self.calculate(
                "BANK", D("5000"), D("9000"), None
            ),
            D("5000"),
        )

    def test_net_worth_respects_explicit_zero(self):
        summaries = [
            SimpleNamespace(
                type="BANK",
                current_balance=D("5000"),
                current_market_value=D("5000"),
            ),
            SimpleNamespace(
                type="INVESTMENT",
                current_balance=D("10000"),
                current_market_value=D("0"),
            ),
            SimpleNamespace(
                type="CREDIT_CARD",
                current_balance=D("500"),
                current_market_value=D("500"),
            ),
        ]

        assets, liabilities = self.totals(summaries)

        self.assertEqual(assets, D("5000"))
        self.assertEqual(liabilities, D("500"))
        self.assertEqual(assets - liabilities, D("4500"))


if __name__ == "__main__":
    unittest.main()
