
"""PH2.5 transfer lifecycle: isolated route-logic contract tests.

No FastAPI import, database connection or application side effects.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

path = Path("modules/finance/router.py")
tree = ast.parse(path.read_text(encoding="utf-8-sig"))


def route(name):
    matches = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    ]
    if len(matches) != 1:
        raise AssertionError("Unexpected route: " + name)
    return matches[0]


update_route = route("update_transaction")

branches = [
    node for node in update_route.body
    if (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Attribute)
        and isinstance(node.test.left.value, ast.Name)
        and node.test.left.value.id == "tx"
        and node.test.left.attr == "type"
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Attribute)
        and node.test.comparators[0].attr == "TRANSFER"
    )
]

if len(branches) != 1:
    raise AssertionError(
        "Expected one transfer-edit validation branch"
    )

branch_module = ast.Module(
    body=branches[0].body,
    type_ignores=[],
)

ast.fix_missing_locations(branch_module)

branch_code = compile(
    branch_module,
    str(path),
    "exec",
)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)


class TransactionType:
    TRANSFER = "TRANSFER"


class AccountType:
    INVESTMENT = "INVESTMENT"
    BANK = "BANK"
    GOAL_RESERVE = "GOAL_RESERVE"


class PH25TransferLifecycleContract(unittest.TestCase):

    def run_edit(
        self,
        changes,
        *,
        old_from=10,
        old_to=20,
        old_account=10,
        owned=None,
        reserves=None,
    ):
        owned = {10, 20, 30} if owned is None else owned
        reserves = set() if reserves is None else reserves

        def get_owned(db, user_id, account_id, label):
            if user_id != 1 or account_id not in owned:
                raise HTTPException(404, label + " not found")
            kind = (
                AccountType.GOAL_RESERVE
                if account_id in reserves
                else AccountType.INVESTMENT
            )
            return SimpleNamespace(id=account_id, type=kind)

        def reject_goal(account, label):
            if account.type == AccountType.GOAL_RESERVE:
                raise HTTPException(409, "Goal reserve protected")

        scope = {
            "tx": SimpleNamespace(
                type=TransactionType.TRANSFER,
                from_account_id=old_from,
                to_account_id=old_to,
                account_id=old_account,
            ),
            "update_data": dict(changes),
            "TransactionType": TransactionType,
            "HTTPException": HTTPException,
            "get_owned_account": get_owned,
            "reject_goal_reserve_account": reject_goal,
            "current_user": SimpleNamespace(id=1),
            "db": object(),
        }

        exec(branch_code, scope)
        return scope["update_data"]

    def test_original_source_preserved(self):
        data = self.run_edit({"amount": 500})
        self.assertEqual(data["account_id"], 10)
        self.assertEqual(data["from_account_id"], 10)

    def test_changed_source_updates_account_id(self):
        data = self.run_edit({"from_account_id": 30})
        self.assertEqual(data["account_id"], 30)
        self.assertEqual(data["from_account_id"], 30)

    def test_changed_destination_preserves_source(self):
        data = self.run_edit({"to_account_id": 30})
        self.assertEqual(data["account_id"], 10)
        self.assertEqual(data["to_account_id"], 30)

    def test_mismatched_account_id_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.run_edit({"account_id": 30})
        self.assertEqual(caught.exception.status_code, 422)

    def test_explicit_null_account_id_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.run_edit({"account_id": None})
        self.assertEqual(caught.exception.status_code, 422)

    def test_missing_destination_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.run_edit({}, old_to=None)
        self.assertEqual(caught.exception.status_code, 422)

    def test_same_source_destination_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.run_edit({"to_account_id": 10})
        self.assertEqual(caught.exception.status_code, 400)

    def test_other_users_account_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.run_edit(
                {"from_account_id": 99},
                owned={10, 20, 30},
            )
        self.assertEqual(caught.exception.status_code, 404)

    def test_goal_reserve_destination_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            self.run_edit(
                {"to_account_id": 30},
                reserves={30},
            )
        self.assertEqual(caught.exception.status_code, 409)

    def test_historical_source_fallback(self):
        data = self.run_edit(
            {},
            old_from=None,
            old_account=10,
        )
        self.assertEqual(data["from_account_id"], 10)
        self.assertEqual(data["account_id"], 10)

    def test_no_transfer_route_calls_legacy_shift(self):
        for name in (
            "create_transfer",
            "update_transaction",
            "delete_transaction",
            "execute_recurring",
        ):
            with self.subTest(route=name):
                calls = [
                    node
                    for node in ast.walk(route(name))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_shift_investment_value"
                ]
                self.assertEqual(calls, [])

    def test_transfer_routes_do_not_assign_market_snapshot(self):
        for name in (
            "create_transfer",
            "update_transaction",
            "delete_transaction",
            "execute_recurring",
        ):
            with self.subTest(route=name):
                targets = [
                    target
                    for node in ast.walk(route(name))
                    if isinstance(node, ast.Assign)
                    for target in node.targets
                    if isinstance(target, ast.Attribute)
                    and target.attr == "current_market_value"
                ]
                self.assertEqual(targets, [])


if __name__ == "__main__":
    unittest.main()
