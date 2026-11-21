
"""PH2.5 account revaluation locking behavior.

Uses the existing isolated account-API harness.
No FastAPI imports or database connections.
"""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

PATH = Path("tests/test_ph25_account_api_contract.py")

spec = importlib.util.spec_from_file_location(
    "ph25_existing_account_api_tests",
    PATH,
)

base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class PH25RevaluationLockTests(unittest.TestCase):

    def setUp(self):
        self.globals = base.update.__globals__
        self.original_lock = self.globals["lock_owned_accounts"]
        self.original_service = self.globals["FinanceService"]

    def tearDown(self):
        self.globals["lock_owned_accounts"] = self.original_lock
        self.globals["FinanceService"] = self.original_service

    def account(self):
        return base.PH25AccountAPIContract().investment(
            base.D("9000"),
            base.D("8000"),
        )

    def test_lock_precedes_revaluation_ledger_read(self):
        events = []

        def tracked_lock(db, user_id, ids):
            events.append("lock")
            return self.original_lock(db, user_id, ids)

        original_service = self.original_service

        class TrackedService:
            @staticmethod
            def calculate_account_balances(db, user_id):
                events.append("ledger")
                return original_service.calculate_account_balances(
                    db, user_id
                )

        self.globals["lock_owned_accounts"] = tracked_lock
        self.globals["FinanceService"] = TrackedService

        account = self.account()
        db = base.FakeDB(account, base.D("10000"))

        base.update(
            10,
            base.FakeData(
                current_market_value=base.D("11000")
            ),
            current_user=base.user,
            db=db,
        )

        self.assertEqual(events, ["lock", "ledger"])
        self.assertEqual(
            account.valuation_ledger_anchor,
            base.D("10000"),
        )
        self.assertEqual(db.commits, 1)

    def test_revaluation_captures_ledger_after_lock(self):
        def lock_and_advance_ledger(db, user_id, ids):
            result = self.original_lock(db, user_id, ids)
            # Simulate another transfer having committed
            # before this lock was acquired.
            db.ledger = base.D("12000")
            return result

        self.globals[
            "lock_owned_accounts"
        ] = lock_and_advance_ledger

        account = self.account()
        db = base.FakeDB(account, base.D("10000"))

        base.update(
            10,
            base.FakeData(
                current_market_value=base.D("12500")
            ),
            current_user=base.user,
            db=db,
        )

        self.assertEqual(
            account.valuation_ledger_anchor,
            base.D("12000"),
        )

    def test_name_update_locks_without_ledger_read(self):
        events = []

        def tracked_lock(db, user_id, ids):
            events.append("lock")
            self.assertEqual(tuple(ids), (10,))
            return self.original_lock(db, user_id, ids)

        class NoLedgerService:
            @staticmethod
            def calculate_account_balances(db, user_id):
                raise AssertionError(
                    "Name update must not recalculate ledger"
                )

        self.globals["lock_owned_accounts"] = tracked_lock
        self.globals["FinanceService"] = NoLedgerService

        account = self.account()
        db = base.FakeDB(account)

        base.update(
            10,
            base.FakeData(name="Renamed"),
            current_user=base.user,
            db=db,
        )

        self.assertEqual(events, ["lock"])
        self.assertEqual(account.name, "Renamed")
        self.assertEqual(db.commits, 1)

    def test_cross_user_account_not_locked(self):
        account = self.account()
        db = base.FakeDB(account)

        with self.assertRaises(base.HTTPException) as caught:
            base.update(
                10,
                base.FakeData(
                    current_market_value=base.D("12000")
                ),
                current_user=SimpleNamespace(id=2),
                db=db,
            )

        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(db.commits, 0)


if __name__ == "__main__":
    unittest.main()
