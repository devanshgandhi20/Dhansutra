"""Isolated PH2.5 account locking and postwrite guard tests.

Executes AST-extracted implementation with fake DB objects.
No application imports or database connections.
"""

import ast
import copy
import importlib.util
import unittest

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

D = Decimal

module_path = Path(
    "modules/finance/transaction_integrity.py"
)

valuation_path = Path(
    "modules/finance/valuation.py"
)

spec = importlib.util.spec_from_file_location(
    "ph25_guard_valuation",
    valuation_path,
)

valuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(valuation)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class AccountType:
    INVESTMENT = "INVESTMENT"
    BANK = "BANK"


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value


class Account:
    id = Field("id")
    user_id = Field("user_id")


class Query:
    def __init__(self, db):
        self.db = db
        self.filters = {}
        self.refreshed = False
        self.locked = False

    def filter(self, *conditions):
        self.filters.update(dict(conditions))
        return self

    def populate_existing(self):
        self.refreshed = True
        return self

    def with_for_update(self):
        self.locked = True
        return self

    def one_or_none(self):
        account_id = self.filters.get("id")
        user_id = self.filters.get("user_id")

        self.db.lock_order.append(account_id)

        if not self.refreshed or not self.locked:
            raise AssertionError("Account lock or refresh missing")

        account = self.db.accounts.get(account_id)

        if account is None or account.user_id != user_id:
            return None

        return account


class FakeDB:
    def __init__(self, accounts=None, summaries=None):
        self.accounts = accounts or {}
        self.summaries = summaries or []
        self.lock_order = []
        self.balance_reads = 0

    def query(self, model):
        if model is not Account:
            raise AssertionError("Unexpected query model")
        return Query(self)


class FinanceService:
    @staticmethod
    def calculate_account_balances(db, user_id):
        db.balance_reads += 1
        return db.summaries


tree = ast.parse(
    module_path.read_text(encoding="utf-8-sig")
)

names = (
    "lock_owned_accounts",
    "validate_postwrite_investments",
)

functions = []

for name in names:
    matches = [
        copy.deepcopy(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    ]

    if len(matches) != 1:
        raise AssertionError("Unexpected helper: " + name)

    functions.append(matches[0])

namespace = {
    "Session": object,
    "Iterable": Iterable,
    "Account": Account,
    "AccountType": AccountType,
    "FinanceService": FinanceService,
    "HTTPException": HTTPException,
    "require_nonnegative_investment_value":
        valuation.require_nonnegative_investment_value,
}

module = ast.Module(
    body=functions,
    type_ignores=[],
)

ast.fix_missing_locations(module)

exec(
    compile(module, str(module_path), "exec"),
    namespace,
)

lock_accounts = namespace["lock_owned_accounts"]
validate = namespace["validate_postwrite_investments"]


def make_account(account_id, kind="INVESTMENT", user_id=1):
    return SimpleNamespace(
        id=account_id,
        user_id=user_id,
        type=kind,
    )


def make_summary(account_id, value, kind="INVESTMENT"):
    return SimpleNamespace(
        id=account_id,
        type=kind,
        current_market_value=value,
    )


class PH25TransactionIntegrity(unittest.TestCase):

    def test_sorted_unique_account_locks(self):
        db = FakeDB(
            accounts={
                1: make_account(1),
                2: make_account(2),
                3: make_account(3),
            }
        )

        locked = lock_accounts(db, 1, [3, 1, 2, 1])

        self.assertEqual(db.lock_order, [1, 2, 3])
        self.assertEqual(
            [account.id for account in locked],
            [1, 2, 3],
        )

    def test_empty_lock_list_is_valid(self):
        db = FakeDB()
        self.assertEqual(lock_accounts(db, 1, []), [])
        self.assertEqual(db.lock_order, [])

    def test_cross_user_account_rejected(self):
        db = FakeDB(
            accounts={3: make_account(3, user_id=2)}
        )

        with self.assertRaises(HTTPException) as caught:
            lock_accounts(db, 1, [3])

        self.assertEqual(caught.exception.status_code, 404)

    def test_missing_account_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            lock_accounts(FakeDB(), 1, [10])

        self.assertEqual(caught.exception.status_code, 404)

    def test_invalid_identifier_rejected(self):
        for value in (None, 0, -1, True, "10"):
            with self.subTest(value=value):
                with self.assertRaises(HTTPException) as caught:
                    lock_accounts(FakeDB(), 1, [value])

                self.assertEqual(
                    caught.exception.status_code, 422
                )

    def test_positive_investment_accepted(self):
        account = make_account(3)
        db = FakeDB(
            summaries=[make_summary(3, D("13000"))]
        )

        validate(db, 1, [account])
        self.assertEqual(db.balance_reads, 1)

    def test_exact_zero_investment_accepted(self):
        account = make_account(3)
        db = FakeDB(
            summaries=[make_summary(3, D("0"))]
        )

        validate(db, 1, [account])

    def test_negative_investment_rejected(self):
        account = make_account(3)
        db = FakeDB(
            summaries=[make_summary(3, D("-0.01"))]
        )

        with self.assertRaises(HTTPException) as caught:
            validate(db, 1, [account])

        self.assertEqual(caught.exception.status_code, 409)

    def test_missing_investment_summary_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            validate(
                FakeDB(summaries=[]),
                1,
                [make_account(3)],
            )

        self.assertEqual(caught.exception.status_code, 409)

    def test_unreconciled_summary_type_rejected(self):
        db = FakeDB(
            summaries=[
                make_summary(3, D("500"), kind="BANK")
            ]
        )

        with self.assertRaises(HTTPException) as caught:
            validate(db, 1, [make_account(3)])

        self.assertEqual(caught.exception.status_code, 409)

    def test_noninvestment_skips_valuation_check(self):
        bank = make_account(3, kind="BANK")
        db = FakeDB()

        validate(db, 1, [bank])

        self.assertEqual(db.balance_reads, 0)

    def test_invalid_decimal_value_rejected(self):
        account = make_account(3)

        for value in (None, "NaN", "Infinity"):
            with self.subTest(value=value):
                db = FakeDB(
                    summaries=[make_summary(3, value)]
                )

                with self.assertRaises(HTTPException) as caught:
                    validate(db, 1, [account])

                self.assertEqual(
                    caught.exception.status_code, 409
                )


if __name__ == "__main__":
    unittest.main()
