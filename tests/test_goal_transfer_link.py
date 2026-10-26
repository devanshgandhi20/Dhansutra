import os
import unittest

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Testing configuration: no real database connections are required.
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault(
    "DB_URL",
    "postgresql://test:test@127.0.0.1:1/dhansutra_test"
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("SECRET_KEY", "test-secret-only")
os.environ.setdefault("SECRET_REFRESH_KEY", "test-refresh-secret-only")

from fastapi import HTTPException
from sqlalchemy import UniqueConstraint

from modules.finance.models import (
    AccountType,
    FinancialGoal,
    GoalContribution,
    Transaction,
    TransactionType,
)

from modules.finance.router import (
    contribute_to_goal,
    create_recurring,
    execute_recurring,
    update_recurring,
    update_transaction,
    delete_transaction,
    delete_goal,
    update_account,
    delete_account,
    create_transaction,
    create_transfer,
    validate_recurring_refs,
    create_account,
)
from modules.finance.imports import (
    commit_receipts,
    webhook_ingest_transactions,
)
from modules.finance.schemas import GoalContributionCreate


class TestGoalTransferLink(unittest.TestCase):

    def setUp(self):
        self.user = SimpleNamespace(id=7)

    def make_db(self, *query_results):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = (
            list(query_results)
        )
        return db

    def make_goal(self, saved="0.00"):
        return SimpleNamespace(
            id=11,
            user_id=self.user.id,
            name="Vacation Savings",
            saved_amount=Decimal(saved),
        )

    def make_transaction(self, tx_type=TransactionType.TRANSFER):
        return SimpleNamespace(
            id=91,
            user_id=self.user.id,
            type=tx_type,
            amount=Decimal("500.00"),
            account_id=21,
            from_account_id=21,
            to_account_id=22,
            source_id=None,
        )

    def assert_conflict(self, action):
        with self.assertRaises(HTTPException) as ctx:
            action()

        self.assertEqual(ctx.exception.status_code, 409)

    # ---------------------------------------------------------
    # Model relationship
    # ---------------------------------------------------------

    def test_contribution_model_has_unique_transfer_reference(self):
        table = GoalContribution.__table__

        self.assertIn("transaction_id", table.columns)

        column = table.columns["transaction_id"]

        self.assertTrue(column.nullable)

        foreign_keys = list(column.foreign_keys)
        self.assertEqual(len(foreign_keys), 1)

        self.assertEqual(
            foreign_keys[0].target_fullname,
            "transactions.id",
        )

        self.assertEqual(
            foreign_keys[0].ondelete,
            "RESTRICT",
        )

        unique_constraints = [
            constraint
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ]

        self.assertTrue(
            any(
                constraint.name == "uq_goal_contributions_transaction_id"
                and [col.name for col in constraint.columns]
                == ["transaction_id"]
                for constraint in unique_constraints
            )
        )

    # ---------------------------------------------------------
    # Contribution creation
    # ---------------------------------------------------------

    def test_contribution_links_to_created_transfer(self):
        db = MagicMock()

        goal = self.make_goal()

        source_type = next(
            item for item in AccountType
            if item not in (
                AccountType.CREDIT_CARD,
                AccountType.GOAL_RESERVE,
            )
        )

        source = SimpleNamespace(
            id=21,
            type=source_type,
        )

        reserve = SimpleNamespace(
            id=22,
            type=AccountType.GOAL_RESERVE,
        )

        query = db.query.return_value.filter.return_value

        query.with_for_update.return_value.first.return_value = goal
        query.order_by.return_value.first.return_value = reserve

        def assign_generated_id():
            for call in db.add.call_args_list:
                obj = call.args[0]

                if isinstance(obj, Transaction):
                    obj.id = 901

        db.flush.side_effect = assign_generated_id

        data = GoalContributionCreate(
            account_id=21,
            amount=Decimal("500.00"),
            date=date(2026, 10, 26),
            note="Monthly savings",
        )

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=source,
        ):
            contribution = contribute_to_goal(
                goal_id=goal.id,
                data=data,
                current_user=self.user,
                db=db,
            )

        transactions = [
            call.args[0]
            for call in db.add.call_args_list
            if isinstance(call.args[0], Transaction)
        ]

        self.assertEqual(len(transactions), 1)

        tx = transactions[0]

        self.assertEqual(contribution.transaction_id, tx.id)
        self.assertEqual(contribution.transaction_id, 901)

        self.assertEqual(tx.type, TransactionType.TRANSFER)
        self.assertEqual(tx.from_account_id, source.id)
        self.assertEqual(tx.to_account_id, reserve.id)

        self.assertEqual(tx.amount, Decimal("500.00"))
        self.assertEqual(goal.saved_amount, Decimal("500.00"))

        db.flush.assert_called_once()
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # Transaction update protection
    # ---------------------------------------------------------

    def test_cannot_edit_goal_linked_transfer(self):
        tx = self.make_transaction()

        db = self.make_db(tx, (41,))

        update = MagicMock()
        update.model_dump.return_value = {
            "amount": Decimal("100.00")
        }

        self.assert_conflict(
            lambda: update_transaction(
                transaction_id=tx.id,
                data=update,
                current_user=self.user,
                db=db,
            )
        )

        self.assertEqual(tx.amount, Decimal("500.00"))
        db.commit.assert_not_called()

    def test_can_edit_unlinked_transaction(self):
        tx = self.make_transaction(TransactionType.INCOME)

        db = self.make_db(
            tx,
            None,  # No linked goal contribution
            None,  # No GOAL_RESERVE account
        )

        update = MagicMock()
        update.model_dump.return_value = {
            "description": "Updated description"
        }

        update_transaction(
            transaction_id=tx.id,
            data=update,
            current_user=self.user,
            db=db,
        )

        self.assertEqual(tx.description, "Updated description")
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # Transaction deletion protection
    # ---------------------------------------------------------

    def test_cannot_delete_goal_linked_transfer(self):
        tx = self.make_transaction()

        db = self.make_db(tx, (41,))

        self.assert_conflict(
            lambda: delete_transaction(
                transaction_id=tx.id,
                current_user=self.user,
                db=db,
            )
        )

        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_can_delete_unlinked_transaction(self):
        tx = self.make_transaction(TransactionType.INCOME)

        db = self.make_db(
            tx,
            None,  # No linked goal contribution
            None,  # No GOAL_RESERVE account
        )

        delete_transaction(
            transaction_id=tx.id,
            current_user=self.user,
            db=db,
        )

        db.delete.assert_called_once_with(tx)
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # Goal deletion protection
    # ---------------------------------------------------------

    def test_cannot_delete_goal_with_contribution_history(self):
        goal = self.make_goal(saved="0.00")

        # Historical contributions may not have transaction_id.
        db = self.make_db(goal, (51,))

        self.assert_conflict(
            lambda: delete_goal(
                goal_id=goal.id,
                current_user=self.user,
                db=db,
            )
        )

        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_cannot_delete_goal_with_nonzero_saved_amount(self):
        goal = self.make_goal(saved="500.00")

        db = self.make_db(goal, None)

        self.assert_conflict(
            lambda: delete_goal(
                goal_id=goal.id,
                current_user=self.user,
                db=db,
            )
        )

        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_can_delete_empty_goal(self):
        goal = self.make_goal(saved="0.00")

        db = self.make_db(goal, None)

        delete_goal(
            goal_id=goal.id,
            current_user=self.user,
            db=db,
        )

        db.delete.assert_called_once_with(goal)
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # Historical Goal & Reserves transfer protection
    # ---------------------------------------------------------

    def test_cannot_edit_historical_reserve_transfer(self):
        tx = self.make_transaction()

        # Transaction exists, no contribution link,
        # but one associated account is GOAL_RESERVE.
        db = self.make_db(tx, None, (22,))

        update = MagicMock()
        update.model_dump.return_value = {
            "amount": Decimal("100.00")
        }

        self.assert_conflict(
            lambda: update_transaction(
                transaction_id=tx.id,
                data=update,
                current_user=self.user,
                db=db,
            )
        )

        self.assertEqual(tx.amount, Decimal("500.00"))
        db.commit.assert_not_called()

    def test_cannot_delete_historical_reserve_transfer(self):
        tx = self.make_transaction()

        # No direct contribution link, but reserve account found.
        db = self.make_db(tx, None, (22,))

        self.assert_conflict(
            lambda: delete_transaction(
                transaction_id=tx.id,
                current_user=self.user,
                db=db,
            )
        )

        db.delete.assert_not_called()
        db.commit.assert_not_called()

    # ---------------------------------------------------------
    # Ordinary bank-to-bank transfers remain editable/deletable
    # ---------------------------------------------------------

    def test_can_edit_unlinked_regular_transfer(self):
        tx = self.make_transaction()

        # Transaction exists, no contribution link,
        # and neither account is GOAL_RESERVE.
        db = self.make_db(tx, None, None)

        update = MagicMock()
        update.model_dump.return_value = {
            "description": "Updated bank transfer"
        }

        update_transaction(
            transaction_id=tx.id,
            data=update,
            current_user=self.user,
            db=db,
        )

        self.assertEqual(
            tx.description,
            "Updated bank transfer"
        )
        db.commit.assert_called_once()

    def test_can_delete_unlinked_regular_transfer(self):
        tx = self.make_transaction()

        db = self.make_db(
            tx,
            None,  # No linked contribution
            None,  # No GOAL_RESERVE account
            None,  # Source-account lookup
            None,  # Destination-account lookup
        )

        delete_transaction(
            transaction_id=tx.id,
            current_user=self.user,
            db=db,
        )

        db.delete.assert_called_once_with(tx)
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # PH2.1 Account management safeguards
    # ---------------------------------------------------------

    def make_account(
        self,
        account_type=AccountType.GOAL_RESERVE,
    ):
        return SimpleNamespace(
            id=22,
            user_id=self.user.id,
            name="Goal & Reserves",
            type=account_type,
            starting_balance=Decimal("0.00"),
        )

    def test_cannot_change_reserve_account_type(self):
        account = self.make_account()
        db = MagicMock()

        data = MagicMock()
        data.model_dump.return_value = {
            "type": AccountType.BANK
        }

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=account,
        ):
            self.assert_conflict(
                lambda: update_account(
                    account_id=account.id,
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        self.assertEqual(
            account.type,
            AccountType.GOAL_RESERVE,
        )
        db.commit.assert_not_called()

    def test_cannot_change_reserve_starting_balance(self):
        account = self.make_account()
        db = MagicMock()

        data = MagicMock()
        data.model_dump.return_value = {
            "starting_balance": Decimal("500.00")
        }

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=account,
        ):
            self.assert_conflict(
                lambda: update_account(
                    account_id=account.id,
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        self.assertEqual(
            account.starting_balance,
            Decimal("0.00"),
        )
        db.commit.assert_not_called()

    def test_can_rename_reserve_account(self):
        account = self.make_account()
        db = MagicMock()

        data = MagicMock()
        data.model_dump.return_value = {
            "name": "My Goal Reserves"
        }

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=account,
        ):
            update_account(
                account_id=account.id,
                data=data,
                current_user=self.user,
                db=db,
            )

        self.assertEqual(
            account.name,
            "My Goal Reserves",
        )
        self.assertEqual(
            account.type,
            AccountType.GOAL_RESERVE,
        )
        db.commit.assert_called_once()

    def test_cannot_delete_account_with_contributions(self):
        account = self.make_account(AccountType.BANK)

        # Account is referenced by a historical contribution.
        db = self.make_db((51,))

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=account,
        ):
            self.assert_conflict(
                lambda: delete_account(
                    account_id=account.id,
                    current_user=self.user,
                    db=db,
                )
            )

        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_cannot_delete_reserve_account(self):
        account = self.make_account()

        # The reserve account is protected even when
        # no transaction or contribution records exist.
        db = self.make_db(None, None, None)

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=account,
        ):
            self.assert_conflict(
                lambda: delete_account(
                    account_id=account.id,
                    current_user=self.user,
                    db=db,
                )
            )

        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_can_delete_unused_regular_account(self):
        account = self.make_account(AccountType.BANK)

        # No contributions, transactions or recurring items.
        db = self.make_db(None, None, None)

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=account,
        ):
            delete_account(
                account_id=account.id,
                current_user=self.user,
                db=db,
            )

        db.delete.assert_called_once_with(account)
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # PH2.1: Manual reserve-account restrictions
    # ---------------------------------------------------------

    def test_cannot_create_manual_transaction_in_reserve(self):
        reserve = self.make_account(AccountType.GOAL_RESERVE)

        for tx_type in (
            TransactionType.INCOME,
            TransactionType.EXPENSE,
        ):
            with self.subTest(transaction_type=tx_type):
                db = MagicMock()

                data = MagicMock()
                data.type = tx_type
                data.account_id = reserve.id
                data.category_id = None
                data.source_id = None

                with patch(
                    "modules.finance.router.get_owned_account",
                    return_value=reserve,
                ):
                    self.assert_conflict(
                        lambda: create_transaction(
                            data=data,
                            current_user=self.user,
                            db=db,
                        )
                    )

                db.add.assert_not_called()
                db.commit.assert_not_called()

    def test_cannot_transfer_into_reserve(self):
        bank = SimpleNamespace(
            id=21,
            type=AccountType.BANK,
        )
        reserve = self.make_account(AccountType.GOAL_RESERVE)

        db = self.make_db(bank, reserve)

        data = SimpleNamespace(
            from_account_id=bank.id,
            to_account_id=reserve.id,
            amount=Decimal("500.00"),
            date=date(2026, 10, 26),
            description="Manual reserve transfer",
        )

        self.assert_conflict(
            lambda: create_transfer(
                data=data,
                current_user=self.user,
                db=db,
            )
        )

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cannot_transfer_out_of_reserve(self):
        reserve = self.make_account(AccountType.GOAL_RESERVE)
        bank = SimpleNamespace(
            id=21,
            type=AccountType.BANK,
        )

        db = self.make_db(reserve, bank)

        data = SimpleNamespace(
            from_account_id=reserve.id,
            to_account_id=bank.id,
            amount=Decimal("500.00"),
            date=date(2026, 10, 26),
            description="Manual reserve withdrawal",
        )

        self.assert_conflict(
            lambda: create_transfer(
                data=data,
                current_user=self.user,
                db=db,
            )
        )

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cannot_reassign_transaction_to_reserve(self):
        tx = self.make_transaction(TransactionType.EXPENSE)
        reserve = self.make_account(AccountType.GOAL_RESERVE)

        # Existing transaction with no contribution link.
        # Its current accounts are not reserve accounts.
        # The requested new account IS a reserve account.
        db = self.make_db(
            tx,
            None,  # No linked goal contribution
            None,  # No existing GOAL_RESERVE account
        )

        data = MagicMock()
        data.model_dump.return_value = {
            "account_id": reserve.id,
        }

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=reserve,
        ):
            self.assert_conflict(
                lambda: update_transaction(
                    transaction_id=tx.id,
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        self.assertEqual(tx.account_id, 21)
        db.commit.assert_not_called()

    def test_can_create_manual_transaction_using_bank(self):
        bank = self.make_account(AccountType.BANK)
        db = MagicMock()

        data = MagicMock()
        data.type = TransactionType.EXPENSE
        data.account_id = bank.id
        data.category_id = None
        data.source_id = None

        data.model_dump.return_value = {
            "type": TransactionType.EXPENSE,
            "account_id": bank.id,
            "category_id": None,
            "amount": Decimal("250.00"),
            "date": date(2026, 10, 26),
            "description": "Groceries",
            "source_id": None,
        }

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=bank,
        ):
            tx = create_transaction(
                data=data,
                current_user=self.user,
                db=db,
            )

        self.assertEqual(tx.type, TransactionType.EXPENSE)
        self.assertEqual(tx.account_id, bank.id)
        self.assertEqual(tx.amount, Decimal("250.00"))

        db.add.assert_called_once()
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # PH2.1: Recurring transaction reserve protection
    # ---------------------------------------------------------

    def test_cannot_create_recurring_expense_in_reserve(self):
        reserve = self.make_account(AccountType.GOAL_RESERVE)
        db = MagicMock()

        data = SimpleNamespace(
            type=TransactionType.EXPENSE,
            account_id=reserve.id,
            to_account_id=None,
            category_id=1,
        )

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=reserve,
        ):
            self.assert_conflict(
                lambda: create_recurring(
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cannot_create_recurring_transfer_to_reserve(self):
        bank = self.make_account(AccountType.BANK)
        bank.id = 21

        reserve = self.make_account(AccountType.GOAL_RESERVE)
        reserve.id = 22

        db = MagicMock()

        data = SimpleNamespace(
            type=TransactionType.TRANSFER,
            account_id=bank.id,
            to_account_id=reserve.id,
            category_id=None,
        )

        with patch(
            "modules.finance.router.get_owned_account",
            side_effect=[bank, reserve],
        ):
            self.assert_conflict(
                lambda: create_recurring(
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cannot_update_recurring_to_reserve(self):
        reserve = self.make_account(AccountType.GOAL_RESERVE)

        rec = SimpleNamespace(
            id=301,
            user_id=self.user.id,
            type=TransactionType.EXPENSE,
            account_id=21,
            to_account_id=None,
            category_id=1,
        )

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = rec

        data = MagicMock()
        data.model_dump.return_value = {
            "account_id": reserve.id,
        }

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=reserve,
        ):
            self.assert_conflict(
                lambda: update_recurring(
                    recurring_id=rec.id,
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        self.assertEqual(rec.account_id, 21)
        db.commit.assert_not_called()

    def test_cannot_execute_historical_recurring_in_reserve(self):
        reserve = self.make_account(AccountType.GOAL_RESERVE)

        rec = SimpleNamespace(
            id=301,
            user_id=self.user.id,
            type=TransactionType.EXPENSE,
            account_id=reserve.id,
            to_account_id=None,
            category_id=1,
            next_date=date(2026, 10, 26),
        )

        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = rec

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=reserve,
        ):
            self.assert_conflict(
                lambda: execute_recurring(
                    recurring_id=rec.id,
                    expected_date=None,
                    current_user=self.user,
                    db=db,
                )
            )

        self.assertEqual(
            rec.next_date,
            date(2026, 10, 26),
        )
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_regular_recurring_references_are_allowed(self):
        bank = self.make_account(AccountType.BANK)
        bank.id = 21

        db = MagicMock()

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=bank,
        ), patch(
            "modules.finance.router.get_usable_category",
        ) as category_check:

            validate_recurring_refs(
                db,
                self.user.id,
                TransactionType.EXPENSE,
                bank.id,
                None,
                1,
            )

        category_check.assert_called_once()
        db.commit.assert_not_called()

    # ---------------------------------------------------------
    # PH2.1: Reserve account creation and conversion
    # ---------------------------------------------------------

    def test_cannot_convert_bank_to_reserve(self):
        account = self.make_account(AccountType.BANK)
        db = MagicMock()

        data = MagicMock()
        data.model_dump.return_value = {
            "type": AccountType.GOAL_RESERVE,
        }

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=account,
        ):
            self.assert_conflict(
                lambda: update_account(
                    account_id=account.id,
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        self.assertEqual(account.type, AccountType.BANK)
        db.commit.assert_not_called()

    def test_cannot_create_reserve_with_nonzero_balance(self):
        db = MagicMock()

        data = MagicMock()
        data.type = AccountType.GOAL_RESERVE
        data.starting_balance = Decimal("5000.00")

        self.assert_conflict(
            lambda: create_account(
                data=data,
                current_user=self.user,
                db=db,
            )
        )

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_can_create_zero_balance_reserve(self):
        db = MagicMock()

        data = MagicMock()
        data.type = AccountType.GOAL_RESERVE
        data.starting_balance = Decimal("0.00")
        data.model_dump.return_value = {
            "name": "Goal & Reserves",
            "type": AccountType.GOAL_RESERVE,
            "starting_balance": Decimal("0.00"),
        }

        account = create_account(
            data=data,
            current_user=self.user,
            db=db,
        )

        self.assertEqual(
            account.type,
            AccountType.GOAL_RESERVE,
        )
        self.assertEqual(
            account.starting_balance,
            Decimal("0.00"),
        )
        self.assertEqual(account.user_id, self.user.id)

        db.add.assert_called_once_with(account)
        db.commit.assert_called_once()

    # ---------------------------------------------------------
    # PH2.1: Gmail import reserve-account safeguards
    # ---------------------------------------------------------

    def test_gmail_batch_skips_reserve_account(self):
        reserve = self.make_account(AccountType.GOAL_RESERVE)
        db = MagicMock()

        db.query.return_value.filter.return_value.first.return_value = reserve

        item = SimpleNamespace(
            source_id=None,
            account_id=reserve.id,
            category_id=None,
            amount=Decimal("500.00"),
            date="2026-10-26",
            description="Imported expense",
            type="EXPENSE",
        )

        result = commit_receipts(
            payload=SimpleNamespace(transactions=[item]),
            current_user=self.user,
            db=db,
        )

        self.assertEqual(result["committed_count"], 0)
        self.assertEqual(result["skipped_count"], 1)

        db.add.assert_not_called()
        db.commit.assert_called_once()

    def test_gmail_batch_allows_bank_account(self):
        bank = self.make_account(AccountType.BANK)
        bank.id = 21

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = bank

        item = SimpleNamespace(
            source_id=None,
            account_id=bank.id,
            category_id=None,
            amount=Decimal("500.00"),
            date="2026-10-26",
            description="Imported bank expense",
            type="EXPENSE",
        )

        result = commit_receipts(
            payload=SimpleNamespace(transactions=[item]),
            current_user=self.user,
            db=db,
        )

        self.assertEqual(result["committed_count"], 1)
        self.assertEqual(result["skipped_count"], 0)

        db.add.assert_called_once()
        imported_tx = db.add.call_args.args[0]

        self.assertEqual(imported_tx.account_id, bank.id)
        self.assertEqual(imported_tx.type, TransactionType.EXPENSE)

        db.commit.assert_called_once()

    def test_webhook_falls_back_to_bank_not_named_axis(self):
        bank = self.make_account(AccountType.BANK)
        bank.id = 21
        bank.name = "HDFC Bank"

        db = MagicMock()

        # No Axis BANK account found; fallback finds HDFC BANK.
        db.query.return_value.filter.return_value.first.side_effect = [
            None,
            bank,
        ]

        item = SimpleNamespace(
            source_id=None,
            amount=Decimal("250.00"),
            date="2026-10-26",
            type="EXPENSE",
            description="Webhook bank expense",
        )

        with patch(
            "modules.finance.imports.settings.SYNC_TOKEN",
            "ph21-test-secret",
        ), patch(
            "modules.finance.imports._resolve_sync_user",
            return_value=self.user,
        ), patch(
            "modules.finance.imports.resolve_category",
            return_value=None,
        ):
            result = webhook_ingest_transactions(
                payload=SimpleNamespace(transactions=[item]),
                x_sync_token="ph21-test-secret",
                db=db,
            )

        self.assertEqual(result["imported"], 1)

        db.add.assert_called_once()
        imported_tx = db.add.call_args.args[0]

        self.assertEqual(imported_tx.account_id, bank.id)

        # Both account-selection queries must restrict to BANK.
        filter_calls = db.query.return_value.filter.call_args_list
        self.assertEqual(len(filter_calls), 2)

        for filter_call in filter_calls:
            sql = " AND ".join(
                str(expression.compile(
                    compile_kwargs={"literal_binds": True}
                ))
                for expression in filter_call.args
            )
            self.assertIn("type", sql)
            self.assertIn("'BANK'", sql)

        db.commit.assert_called_once()

    def test_webhook_rejects_when_no_bank_account_exists(self):
        db = MagicMock()

        # Neither the preferred Axis BANK query nor the fallback
        # BANK query finds an account.
        db.query.return_value.filter.return_value.first.side_effect = [
            None,
            None,
        ]

        with patch(
            "modules.finance.imports.settings.SYNC_TOKEN",
            "ph21-test-secret",
        ), patch(
            "modules.finance.imports._resolve_sync_user",
            return_value=self.user,
        ):
            with self.assertRaises(HTTPException) as ctx:
                webhook_ingest_transactions(
                    payload=SimpleNamespace(transactions=[]),
                    x_sync_token="ph21-test-secret",
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("bank account", ctx.exception.detail.lower())

        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_manual_transfer_missing_account_returns_404(self):
        source = SimpleNamespace(
            id=21,
            type=AccountType.BANK,
        )

        destination = SimpleNamespace(
            id=22,
            type=AccountType.BANK,
        )

        for from_acc, to_acc in (
            (None, destination),
            (source, None),
        ):
            with self.subTest(
                missing_source=from_acc is None,
                missing_destination=to_acc is None,
            ):
                db = self.make_db(from_acc, to_acc)

                data = SimpleNamespace(
                    from_account_id=21,
                    to_account_id=22,
                )

                with self.assertRaises(HTTPException) as ctx:
                    create_transfer(
                        data=data,
                        current_user=self.user,
                        db=db,
                    )

                self.assertEqual(ctx.exception.status_code, 404)
                db.add.assert_not_called()
                db.commit.assert_not_called()

    def test_cannot_edit_historical_reserve_expense(self):
        tx = self.make_transaction(TransactionType.EXPENSE)

        tx.account_id = 22
        tx.from_account_id = None
        tx.to_account_id = None

        # Existing transaction, no contribution link,
        # but its account is GOAL_RESERVE.
        db = self.make_db(tx, None, (22,))

        data = MagicMock()
        data.model_dump.return_value = {
            "amount": Decimal("100.00"),
        }

        self.assert_conflict(
            lambda: update_transaction(
                transaction_id=tx.id,
                data=data,
                current_user=self.user,
                db=db,
            )
        )

        self.assertEqual(tx.amount, Decimal("500.00"))
        db.commit.assert_not_called()

    def test_cannot_delete_historical_reserve_income(self):
        tx = self.make_transaction(TransactionType.INCOME)

        tx.account_id = 22
        tx.from_account_id = None
        tx.to_account_id = None

        db = self.make_db(tx, None, (22,))

        self.assert_conflict(
            lambda: delete_transaction(
                transaction_id=tx.id,
                current_user=self.user,
                db=db,
            )
        )

        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_cannot_contribute_from_another_goal_reserve(self):
        goal = self.make_goal()

        reserve_source = SimpleNamespace(
            id=31,
            type=AccountType.GOAL_RESERVE,
        )

        db = MagicMock()

        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = goal

        data = GoalContributionCreate(
            account_id=reserve_source.id,
            amount=Decimal("500.00"),
            date=date(2026, 10, 26),
            note="Reserve-to-reserve contribution",
        )

        with patch(
            "modules.finance.router.get_owned_account",
            return_value=reserve_source,
        ):
            self.assert_conflict(
                lambda: contribute_to_goal(
                    goal_id=goal.id,
                    data=data,
                    current_user=self.user,
                    db=db,
                )
            )

        self.assertEqual(
            goal.saved_amount,
            Decimal("0.00"),
        )

        db.add.assert_not_called()
        db.commit.assert_not_called()

if __name__ == "__main__":
    unittest.main()