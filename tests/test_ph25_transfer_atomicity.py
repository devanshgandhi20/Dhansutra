
"""PH2.5 manual transfer atomicity: isolated route execution."""

import ast
import copy
import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

D = Decimal

spec = importlib.util.spec_from_file_location(
    "ph25_atomic_valuation",
    "modules/finance/valuation.py",
)
valuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(valuation)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)


class TransactionType:
    TRANSFER = "TRANSFER"


class Transaction:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeAccount:
    def __init__(
        self, account_id, kind, ledger,
        snapshot=None, anchor=None, owner=1,
    ):
        self.id = account_id
        self.type = kind
        self.ledger = D(str(ledger))
        self.current_market_value = snapshot
        self.valuation_ledger_anchor = anchor
        self.user_id = owner


class FakeDB:
    def __init__(
        self, accounts,
        fail_flush=False,
        fail_commit=False,
    ):
        self.accounts = {a.id: a for a in accounts}
        self.original = {a.id: a.ledger for a in accounts}
        self.fail_flush = fail_flush
        self.fail_commit = fail_commit
        self.pending = None
        self.events = []
        self.commits = 0

    def add(self, tx):
        self.events.append("add")
        self.pending = tx

    def flush(self):
        self.events.append("flush")
        if self.fail_flush:
            raise RuntimeError("Simulated flush failure")
        tx = self.pending
        amount = D(str(tx.amount))
        self.accounts[tx.from_account_id].ledger -= amount
        self.accounts[tx.to_account_id].ledger += amount

    def commit(self):
        self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("Simulated commit failure")
        self.commits += 1

    def rollback(self):
        self.events.append("rollback")
        for account_id, ledger in self.original.items():
            self.accounts[account_id].ledger = ledger
        self.pending = None

    def refresh(self, obj):
        self.events.append("refresh")


def lock_owned_accounts(db, user_id, account_ids):
    db.events.append("lock")
    db.lock_order = sorted(set(account_ids))
    result = []
    for account_id in db.lock_order:
        account = db.accounts.get(account_id)
        if account is None or account.user_id != user_id:
            raise HTTPException(404, "Account not found")
        result.append(account)
    return result


def reject_goal_reserve_account(account, label):
    if account.type == "GOAL_RESERVE":
        raise HTTPException(409, label + " is protected")


def validate_postwrite_investments(db, user_id, accounts):
    db.events.append("validate")
    if "flush" not in db.events:
        raise AssertionError("Validation before flush")

    for acc in accounts:
        if acc.type != "INVESTMENT":
            continue
        try:
            value = valuation.effective_investment_value(
                acc.ledger,
                acc.current_market_value,
                acc.valuation_ledger_anchor,
            )
            valuation.require_nonnegative_investment_value(value)
        except ValueError as exc:
            raise HTTPException(
                409, "Invalid investment value"
            ) from exc


tree = ast.parse(
    Path("modules/finance/router.py").read_text(
        encoding="utf-8-sig"
    )
)

functions = [
    copy.deepcopy(n) for n in tree.body
    if isinstance(n, ast.FunctionDef)
    and n.name == "create_transfer"
]

if len(functions) != 1:
    raise AssertionError("Unexpected transfer route")

function = functions[0]
function.decorator_list = []

module = ast.Module(body=[function], type_ignores=[])
ast.fix_missing_locations(module)

namespace = {
    "TransferCreate": object,
    "User": object,
    "Session": object,
    "Depends": lambda value: None,
    "get_current_user": lambda: None,
    "get_db": lambda: None,
    "Decimal": Decimal,
    "Transaction": Transaction,
    "TransactionType": TransactionType,
    "HTTPException": HTTPException,
    "lock_owned_accounts": lock_owned_accounts,
    "reject_goal_reserve_account": reject_goal_reserve_account,
    "validate_postwrite_investments": validate_postwrite_investments,
}

exec(
    compile(module, "modules/finance/router.py", "exec"),
    namespace,
)

create = namespace["create_transfer"]
user = SimpleNamespace(id=1)


class PH25ManualTransferAtomicity(unittest.TestCase):

    def bank(self, account_id=10):
        return FakeAccount(account_id, "BANK", "20000")

    def investment(
        self,
        account_id=20,
        ledger="10000",
        snapshot=None,
        anchor=None,
    ):
        return FakeAccount(
            account_id, "INVESTMENT", ledger, snapshot, anchor
        )

    def run_transfer(
        self, db, source=10, destination=20, amount="2000"
    ):
        data = SimpleNamespace(
            from_account_id=source,
            to_account_id=destination,
            amount=D(amount),
            date="2026-11-01",
            description="Test transfer",
        )
        return create(data, current_user=user, db=db)

    def test_unvalued_investment(self):
        investment = self.investment()
        db = FakeDB([self.bank(), investment])
        tx = self.run_transfer(db)
        self.assertEqual(tx.account_id, 10)
        self.assertEqual(investment.ledger, D("12000"))
        self.assertIsNone(investment.current_market_value)
        self.assertEqual(db.events, [
            "lock", "add", "flush",
            "validate", "commit", "refresh"
        ])

    def test_positive_snapshot_added_once(self):
        inv = self.investment(
            snapshot=D("11000"), anchor=D("10000")
        )
        db = FakeDB([self.bank(), inv])
        self.run_transfer(db)
        self.assertEqual(inv.current_market_value, D("11000"))
        self.assertEqual(
            valuation.effective_investment_value(
                inv.ledger, inv.current_market_value,
                inv.valuation_ledger_anchor,
            ),
            D("13000"),
        )

    def test_legacy_zero_not_promoted(self):
        inv = self.investment(snapshot=D("0"))
        db = FakeDB([self.bank(), inv])
        self.run_transfer(db)
        self.assertEqual(inv.current_market_value, D("0"))
        self.assertEqual(inv.ledger, D("12000"))

    def test_explicit_zero_snapshot(self):
        inv = self.investment(
            snapshot=D("0"), anchor=D("10000")
        )
        db = FakeDB([self.bank(), inv])
        self.run_transfer(db)
        self.assertEqual(
            valuation.effective_investment_value(
                inv.ledger, inv.current_market_value,
                inv.valuation_ledger_anchor,
            ),
            D("2000"),
        )

    def test_investment_to_investment(self):
        source = self.investment(
            10, "7000", D("7000"), D("7000")
        )
        target = self.investment(
            20, "3000", D("3000"), D("3000")
        )
        db = FakeDB([source, target])
        self.run_transfer(db)
        self.assertEqual(source.ledger, D("5000"))
        self.assertEqual(target.ledger, D("5000"))
        self.assertEqual(source.current_market_value, D("7000"))
        self.assertEqual(target.current_market_value, D("3000"))

    def test_underflow_rolls_back(self):
        source = self.investment(
            10, "800", D("500"), D("800")
        )
        bank = self.bank(20)
        db = FakeDB([source, bank])
        with self.assertRaises(HTTPException) as caught:
            self.run_transfer(db, amount="800")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(source.ledger, D("800"))
        self.assertEqual(bank.ledger, D("20000"))
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.events[-1], "rollback")

    def test_sorted_account_locks(self):
        inv = self.investment(10)
        db = FakeDB([inv, self.bank(20)])
        self.run_transfer(db, source=20, destination=10)
        self.assertEqual(db.lock_order, [10, 20])

    def test_other_user_account_rejected(self):
        inv = self.investment()
        inv.user_id = 2
        db = FakeDB([self.bank(), inv])
        with self.assertRaises(HTTPException) as caught:
            self.run_transfer(db)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertNotIn("add", db.events)

    def test_goal_reserve_rejected(self):
        reserve = FakeAccount(20, "GOAL_RESERVE", "0")
        db = FakeDB([self.bank(), reserve])
        with self.assertRaises(HTTPException) as caught:
            self.run_transfer(db)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotIn("add", db.events)

    def test_same_account_rejected(self):
        db = FakeDB([self.bank()])
        with self.assertRaises(HTTPException) as caught:
            self.run_transfer(db, 10, 10)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(db.events, [])

    def test_flush_failure_rolls_back(self):
        db = FakeDB(
            [self.bank(), self.investment()],
            fail_flush=True,
        )
        with self.assertRaises(RuntimeError):
            self.run_transfer(db)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.events[-1], "rollback")
        self.assertEqual(db.accounts[20].ledger, D("10000"))

    def test_commit_failure_rolls_back(self):
        db = FakeDB(
            [self.bank(), self.investment()],
            fail_commit=True,
        )
        with self.assertRaises(RuntimeError):
            self.run_transfer(db)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.events[-1], "rollback")
        self.assertEqual(db.accounts[20].ledger, D("10000"))


if __name__ == "__main__":
    unittest.main()
