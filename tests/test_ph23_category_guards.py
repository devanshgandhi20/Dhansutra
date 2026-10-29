import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException

from modules.finance.models import (
    Account,
    Budget,
    Category,
    GoalContribution,
    RecurringTransaction,
    Transaction,
)
from modules.finance.router import (
    DeleteCategoryRequest,
    delete_category,
)


class CategoryIntegrityTests(unittest.TestCase):

    def setUp(self):
        category_type = SimpleNamespace(value="EXPENSE")

        self.user = SimpleNamespace(id=10)
        self.category = SimpleNamespace(
            id=7,
            type=category_type,
        )
        self.destination = SimpleNamespace(
            id=8,
            type=category_type,
        )

        self.db = MagicMock()

        self.category_query = MagicMock()
        self.category_query.filter.return_value.first.side_effect = [
            self.category,
            self.destination,
        ]

        self.budget_query = MagicMock()
        self.budget_query.filter.return_value.first.return_value = None

        self.linked_query = MagicMock()
        self.linked_query.join.return_value.filter.return_value.first.return_value = None

        self.reserve_query = MagicMock()
        self.reserve_query.join.return_value.filter.return_value.first.return_value = None

        self.transaction_query = MagicMock()
        self.recurring_query = MagicMock()

        def query_dispatch(entity):
            if entity is Category:
                return self.category_query
            if entity is Budget.id:
                return self.budget_query
            if entity is GoalContribution.id:
                return self.linked_query
            if entity is Transaction.id:
                return self.reserve_query
            if entity is Transaction:
                return self.transaction_query
            if entity is RecurringTransaction:
                return self.recurring_query

            raise AssertionError(
                f"Unexpected query entity: {entity}"
            )

        self.db.query.side_effect = query_dispatch

    def reassign_category(self):
        return delete_category(
            category_id=7,
            payload=DeleteCategoryRequest(
                move_to_category_id=8
            ),
            current_user=self.user,
            db=self.db,
        )

    def assert_no_mutation(self):
        self.db.delete.assert_not_called()
        self.db.commit.assert_not_called()
        self.transaction_query.filter.return_value.update.assert_not_called()
        self.recurring_query.filter.return_value.update.assert_not_called()

    def test_rejects_category_with_budgets(self):
        self.budget_query.filter.return_value.first.return_value = (55,)

        with self.assertRaises(HTTPException) as ctx:
            self.reassign_category()

        self.assertEqual(ctx.exception.status_code, 409)
        self.assert_no_mutation()

    def test_rejects_category_with_goal_linked_transaction(self):
        self.linked_query.join.return_value.filter.return_value.first.return_value = (77,)

        with self.assertRaises(HTTPException) as ctx:
            self.reassign_category()

        self.assertEqual(ctx.exception.status_code, 409)
        self.assert_no_mutation()

        join_condition = self.linked_query.join.call_args.args[1]
        self.assertIn(
            "reversal_transaction_id",
            str(join_condition),
        )

    def test_rejects_historical_reserve_transaction(self):
        self.reserve_query.join.return_value.filter.return_value.first.return_value = (88,)

        with self.assertRaises(HTTPException) as ctx:
            self.reassign_category()

        self.assertEqual(ctx.exception.status_code, 409)
        self.assert_no_mutation()

    def test_allows_unrelated_category_reassignment(self):
        result = self.reassign_category()

        self.assertEqual(result["status"], "success")
        self.transaction_query.filter.return_value.update.assert_called_once()
        self.recurring_query.filter.return_value.update.assert_called_once()
        self.db.delete.assert_called_once_with(self.category)
        self.db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
