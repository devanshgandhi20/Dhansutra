import os
import unittest

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# No real database or Redis connections.
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault(
    "DB_URL",
    "postgresql://test:test@127.0.0.1:1/dhansutra_test",
)
os.environ.setdefault(
    "REDIS_URL",
    "redis://127.0.0.1:1/0",
)
os.environ.setdefault("SECRET_KEY", "test-secret-only")
os.environ.setdefault("SECRET_REFRESH_KEY", "test-refresh-secret-only")

from fastapi import HTTPException

from modules.finance.models import AccountType, TransactionType
from modules.finance.router import (
    reverse_goal_contribution,
    archive_goal,
)
from modules.finance.schemas import GoalContributionReversalCreate
from modules.finance.services import FinanceService


class TestGoalReversal(unittest.TestCase):

    def setUp(self):
        self.user = SimpleNamespace(id=7)

        self.goal = SimpleNamespace(
            id=11,
            user_id=7,
            name="Vacation Savings",
            saved_amount=Decimal("50.00"),
            closed_at=None,
        )

        self.contribution = SimpleNamespace(
            id=77,
            goal_id=11,
            account_id=21,
            transaction_id=91,
            reversal_transaction_id=None,
            reversed_at=None,
            amount=Decimal("50.00"),
            date=date(2020, 1, 1),
            note="Original allocation",
        )

        self.original_tx = SimpleNamespace(
            id=91,
            user_id=7,
            account_id=21,
            from_account_id=21,
            to_account_id=22,
            type=TransactionType.TRANSFER,
            amount=Decimal("50.00"),
            date=date(2020, 1, 1),
        )

        self.source = SimpleNamespace(
            id=21,
            user_id=7,
            type=AccountType.BANK,
        )

        self.reserve = SimpleNamespace(
            id=22,
            user_id=7,
            type=AccountType.GOAL_RESERVE,
        )

    def query_result(self, result):
        query = MagicMock()
        query.filter.return_value = query
        query.with_for_update.return_value = query
        query.first.return_value = result
        return query

    def make_db(self):
        db = MagicMock()

        # Query order:
        # 1. Locked goal
        # 2. Locked contribution
        # 3. Original transaction
        # 4. Original source account
        # 5. Locked reserve account
        db.query.side_effect = [
            self.query_result(self.goal),
            self.query_result(self.contribution),
            self.query_result(self.original_tx),
            self.query_result(self.source),
            self.query_result(self.reserve),
        ]

        # Simulate assigning the reversal transaction ID
        # during SQLAlchemy flush().
        def assign_transaction_id():
            reversal_tx = db.add.call_args.args[0]
            reversal_tx.id = 101

        db.flush.side_effect = assign_transaction_id

        return db

    def reverse(self, db):
        payload = GoalContributionReversalCreate(
            date=date.today(),
            note="Return unused savings",
        )

        return reverse_goal_contribution(
            goal_id=self.goal.id,
            contribution_id=self.contribution.id,
            data=payload,
            current_user=self.user,
            db=db,
        )

    def assert_conflict(self, action):
        with self.assertRaises(HTTPException) as ctx:
            action()

        self.assertEqual(ctx.exception.status_code, 409)

    def test_successful_full_reversal(self):
        db = self.make_db()

        balance = SimpleNamespace(
            id=self.reserve.id,
            current_balance=Decimal("50.00"),
        )

        with patch.object(
            FinanceService,
            "calculate_account_balances",
            return_value=[balance],
        ):
            result = self.reverse(db)

        db.add.assert_called_once()
        db.flush.assert_called_once()
        db.commit.assert_called_once()

        reversal_tx = db.add.call_args.args[0]

        self.assertEqual(
            reversal_tx.type,
            TransactionType.TRANSFER,
        )
        self.assertEqual(
            reversal_tx.from_account_id,
            self.reserve.id,
        )
        self.assertEqual(
            reversal_tx.to_account_id,
            self.source.id,
        )
        self.assertEqual(
            reversal_tx.amount,
            Decimal("50.00"),
        )

        self.assertEqual(
            self.goal.saved_amount,
            Decimal("0.00"),
        )
        self.assertEqual(
            self.contribution.reversal_transaction_id,
            101,
        )
        self.assertIsNotNone(
            self.contribution.reversed_at
        )

        # Original funding reference must not change.
        self.assertEqual(
            self.contribution.transaction_id,
            91,
        )
        self.assertIs(result, self.contribution)

    def test_duplicate_reversal_rejected(self):
        db = self.make_db()

        self.contribution.reversal_transaction_id = 101

        self.assert_conflict(lambda: self.reverse(db))

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_legacy_unlinked_contribution_rejected(self):
        db = self.make_db()

        self.contribution.transaction_id = None

        self.assert_conflict(lambda: self.reverse(db))

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_insufficient_reserve_balance_rejected(self):
        db = self.make_db()

        balance = SimpleNamespace(
            id=self.reserve.id,
            current_balance=Decimal("49.00"),
        )

        with patch.object(
            FinanceService,
            "calculate_account_balances",
            return_value=[balance],
        ):
            self.assert_conflict(lambda: self.reverse(db))

        self.assertEqual(
            self.goal.saved_amount,
            Decimal("50.00"),
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()
    def test_mismatched_original_amount_rejected(self):
        db = self.make_db()

        # Funding transfer does not match the contribution.
        self.original_tx.amount = Decimal("40.00")

        self.assert_conflict(lambda: self.reverse(db))

        self.assertEqual(
            self.goal.saved_amount,
            Decimal("50.00"),
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_mismatched_original_source_rejected(self):
        db = self.make_db()

        # Transfer originates from a different account.
        self.original_tx.from_account_id = 999

        self.assert_conflict(lambda: self.reverse(db))

        self.assertIsNone(
            self.contribution.reversal_transaction_id
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_non_transfer_funding_transaction_rejected(self):
        db = self.make_db()

        # Contributions must originate from a TRANSFER,
        # not an income or expense transaction.
        self.original_tx.type = TransactionType.INCOME

        self.assert_conflict(lambda: self.reverse(db))

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_insufficient_goal_saved_amount_rejected(self):
        db = self.make_db()

        # Goal balance is smaller than the amount
        # associated with its original contribution.
        self.goal.saved_amount = Decimal("49.00")

        self.assert_conflict(lambda: self.reverse(db))

        self.assertEqual(
            self.goal.saved_amount,
            Decimal("49.00"),
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()

class TestGoalArchive(unittest.TestCase):

    def setUp(self):
        self.user = SimpleNamespace(id=7)

        self.goal = SimpleNamespace(
            id=11,
            user_id=7,
            name="Vacation Savings",
            saved_amount=Decimal("0.00"),
            closed_at=None,
        )

        self.contribution = SimpleNamespace(
            id=77,
            goal_id=11,
            transaction_id=91,
            reversal_transaction_id=101,
            reversed_at=datetime(2026, 10, 27),
        )
        # Original funding transfer.
        self.original_tx = SimpleNamespace(
            id=91,
            user_id=7,
            account_id=21,
            from_account_id=21,
            to_account_id=22,
            type=TransactionType.TRANSFER,
            amount=Decimal("50.00"),
            date=date(2020, 1, 1),
        )

        # Compensating reversal transfer.
        self.reversal_tx = SimpleNamespace(
            id=101,
            user_id=7,
            account_id=22,
            from_account_id=22,
            to_account_id=21,
            type=TransactionType.TRANSFER,
            amount=Decimal("50.00"),
            date=date(2026, 10, 27),
        )
        self.reserve = SimpleNamespace(
            id=22,
            user_id=7,
            type=AccountType.GOAL_RESERVE,
        )

        self.contribution.account_id = 21
        self.contribution.amount = Decimal("50.00")
        self.contribution.date = date(2020, 1, 1)

    def make_db(self, contributions=None):
        db = MagicMock()

        if contributions is None:
            contributions = [self.contribution]

        goal_query = MagicMock()
        goal_query.filter.return_value = goal_query
        goal_query.with_for_update.return_value = goal_query
        goal_query.first.return_value = self.goal

        contribution_query = MagicMock()
        contribution_query.filter.return_value = contribution_query
        contribution_query.order_by.return_value = contribution_query
        contribution_query.with_for_update.return_value = contribution_query
        contribution_query.all.return_value = contributions

        original_query = MagicMock()
        original_query.filter.return_value = original_query
        original_query.first.return_value = self.original_tx

        reversal_query = MagicMock()
        reversal_query.filter.return_value = reversal_query
        reversal_query.first.return_value = self.reversal_tx

        # Future archival query order:
        # 1. Goal
        # 2. Contributions
        # 3. Original funding transaction
        # 4. Reversal transaction
        reserve_query = MagicMock()
        reserve_query.filter.return_value = reserve_query
        reserve_query.first.return_value = self.reserve
        db.query.side_effect = [
            goal_query,
            contribution_query,
            original_query,
            reversal_query,
            reserve_query,
        ]

        return db

    def archive(self, db):
        return archive_goal(
            goal_id=self.goal.id,
            current_user=self.user,
            db=db,
        )

    def assert_conflict(self, action):
        with self.assertRaises(HTTPException) as ctx:
            action()

        self.assertEqual(ctx.exception.status_code, 409)

    def test_fully_reversed_goal_can_be_archived(self):
        db = self.make_db()

        result = self.archive(db)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["archived_goal_id"], 11)

        self.assertIsNotNone(self.goal.closed_at)
        self.assertIsInstance(
            self.goal.closed_at,
            datetime,
        )

        db.commit.assert_called_once()
        db.delete.assert_not_called()

        # Contribution and original transfer references
        # must remain unchanged.
        self.assertEqual(
            self.contribution.transaction_id,
            91,
        )
        self.assertEqual(
            self.contribution.reversal_transaction_id,
            101,
        )

    def test_goal_with_remaining_funds_cannot_archive(self):
        self.goal.saved_amount = Decimal("50.00")
        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        self.assertIsNone(self.goal.closed_at)
        db.commit.assert_not_called()

    def test_unreversed_contribution_blocks_archive(self):
        self.contribution.reversal_transaction_id = None
        self.contribution.reversed_at = None

        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        self.assertIsNone(self.goal.closed_at)
        db.commit.assert_not_called()

    def test_incomplete_reversal_blocks_archive(self):
        # Reversal transaction recorded, but timestamp missing.
        self.contribution.reversed_at = None

        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        self.assertIsNone(self.goal.closed_at)
        db.commit.assert_not_called()

    def test_legacy_unlinked_contribution_blocks_archive(self):
        self.contribution.transaction_id = None

        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        self.assertIsNone(self.goal.closed_at)
        db.commit.assert_not_called()

    def test_goal_without_history_uses_delete(self):
        db = self.make_db(contributions=[])

        self.assert_conflict(lambda: self.archive(db))

        self.assertIsNone(self.goal.closed_at)
        db.commit.assert_not_called()

    def test_archive_rejects_wrong_reversal_destination(self):
        # Reversal sends money to an unrelated account.
        self.reversal_tx.to_account_id = 999
        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        db.commit.assert_not_called()

    def test_archive_rejects_wrong_reversal_amount(self):
        # Reversal must exactly match the contribution.
        self.reversal_tx.amount = Decimal("40.00")
        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        db.commit.assert_not_called()

    def test_archive_rejects_foreign_user_reversal(self):
        # Another user's transaction cannot count
        # as this goal's reversal.
        self.reversal_tx.user_id = 999
        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        db.commit.assert_not_called()
    def test_archive_rejects_non_reserve_destination(self):
        # Original transaction points to an ordinary account,
        # even though its reversal has the opposite direction.
        self.reserve.type = AccountType.BANK

        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        db.commit.assert_not_called()

    def test_archive_rejects_foreign_reserve_account(self):
        # Reserve account belongs to a different user.
        self.reserve.user_id = 999

        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        db.commit.assert_not_called()

    def test_archive_rejects_missing_reserve_account(self):
        # Referenced reserve account no longer exists.
        self.reserve = None

        db = self.make_db()

        self.assert_conflict(lambda: self.archive(db))

        db.commit.assert_not_called()

if __name__ == "__main__":
    unittest.main()