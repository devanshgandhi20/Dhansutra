
"""PH2.5: Isolated account API valuation regression tests."""

import ast
import copy
import importlib.util
import unittest

from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

D = Decimal

router_path = Path("modules/finance/router.py")
schema_path = Path("modules/finance/schemas.py")

spec = importlib.util.spec_from_file_location(
    "ph25_account_valuation",
    "modules/finance/valuation.py",
)
valuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(valuation)


class AccountType(str, Enum):
    BANK = "BANK"
    CASH = "CASH"
    CREDIT_CARD = "CREDIT_CARD"
    INVESTMENT = "INVESTMENT"
    GOAL_RESERVE = "GOAL_RESERVE"


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class FakeAccount:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.id = kwargs.get("id", 10)
        self.current_market_value = kwargs.get(
            "current_market_value"
        )
        self.valuation_ledger_anchor = kwargs.get(
            "valuation_ledger_anchor"
        )


class FakeData:
    def __init__(self, **fields):
        self.fields = fields
        for key, value in fields.items():
            setattr(self, key, value)

    def model_dump(self, exclude_unset=False):
        return dict(self.fields)


class FakeDB:
    def __init__(self, account=None, ledger=D("10000")):
        self.account = account
        self.ledger = ledger
        self.commits = 0

    def add(self, account):
        self.account = account

    def commit(self):
        self.commits += 1

    def refresh(self, account):
        pass


def get_owned_account(db, user_id, account_id):
    acc = db.account

    if (
        acc is None
        or acc.id != account_id
        or acc.user_id != user_id
    ):
        raise HTTPException(404, "Account not found")

    return acc


class FinanceService:
    @staticmethod
    def calculate_account_balances(db, user_id):
        return [
            SimpleNamespace(
                id=db.account.id,
                current_balance=db.ledger,
            )
        ]


tree = ast.parse(
    router_path.read_text(encoding="utf-8-sig")
)


def extract(name):
    functions = [
        copy.deepcopy(node)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    ]

    if len(functions) != 1:
        raise AssertionError("Unexpected route: " + name)

    function = functions[0]
    function.decorator_list = []
    return function


namespace = {
    "Account": FakeAccount,
    "AccountType": AccountType,
    "AccountCreate": object,
    "AccountUpdate": object,
    "AccountResponse": object,
    "User": object,
    "Session": object,
    "Decimal": Decimal,
    "HTTPException": HTTPException,
    "status": SimpleNamespace(HTTP_201_CREATED=201),
    "Depends": lambda dependency: None,
    "get_current_user": lambda: None,
    "get_db": lambda: None,
    "get_owned_account": get_owned_account,
    "lock_owned_accounts": lambda db, user_id, ids: [
        get_owned_account(db, user_id, account_id)
        for account_id in sorted(set(ids))
    ],
    "FinanceService": FinanceService,
    "set_valuation_snapshot": valuation.set_valuation_snapshot,
}

# PH2.5: In-memory dependencies for atomic membership capture.
FakeDB.flush = lambda self: None
FakeDB.rollback = lambda self: None
namespace["replace_investment_valuation_members"] = (
    lambda db, user_id, account: 0
)
namespace["validate_postwrite_investments"] = (
    lambda db, user_id, accounts: None
)

module = ast.Module(
    body=[
        extract("create_account"),
        extract("update_account"),
    ],
    type_ignores=[],
)

ast.fix_missing_locations(module)
exec(
    compile(module, str(router_path), "exec"),
    namespace,
)

create = namespace["create_account"]
update = namespace["update_account"]
user = SimpleNamespace(id=1)


class PH25AccountAPIContract(unittest.TestCase):

    def investment(self, snapshot=None, anchor=None):
        return FakeAccount(
            id=10,
            user_id=1,
            name="Investment",
            type=AccountType.INVESTMENT,
            starting_balance=D("5000"),
            current_market_value=snapshot,
            valuation_ledger_anchor=anchor,
        )

    def create_data(self, value=None):
        return FakeData(
            name="Investment",
            type=AccountType.INVESTMENT,
            starting_balance=D("5000"),
            current_market_value=value,
        )

    def test_create_null_snapshot(self):
        db = FakeDB()
        acc = create(
            self.create_data(),
            current_user=user, db=db,
        )
        self.assertIsNone(acc.current_market_value)
        self.assertIsNone(acc.valuation_ledger_anchor)
        self.assertEqual(db.commits, 1)

    def test_create_explicit_zero(self):
        db = FakeDB()
        acc = create(
            self.create_data(D("0")),
            current_user=user, db=db,
        )
        self.assertEqual(acc.current_market_value, D("0"))
        self.assertEqual(acc.valuation_ledger_anchor, D("5000"))

    def test_create_positive_snapshot(self):
        db = FakeDB()
        acc = create(
            self.create_data(D("6000")),
            current_user=user, db=db,
        )
        self.assertEqual(acc.valuation_ledger_anchor, D("5000"))

    def test_create_noninvestment_valuation_rejected(self):
        db = FakeDB()
        payload = FakeData(
            name="Bank",
            type=AccountType.BANK,
            starting_balance=D("5000"),
            current_market_value=D("6000"),
        )
        with self.assertRaises(HTTPException) as caught:
            create(payload, current_user=user, db=db)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(db.commits, 0)

    def test_revaluation_anchors_current_ledger(self):
        acc = self.investment(D("9000"), D("8000"))
        db = FakeDB(acc, D("10000"))
        update(
            10,
            FakeData(current_market_value=D("11000")),
            current_user=user, db=db,
        )
        self.assertEqual(acc.current_market_value, D("11000"))
        self.assertEqual(acc.valuation_ledger_anchor, D("10000"))

    def test_clearing_valuation_clears_anchor(self):
        acc = self.investment(D("9000"), D("8000"))
        db = FakeDB(acc)
        update(
            10,
            FakeData(current_market_value=None),
            current_user=user, db=db,
        )
        self.assertIsNone(acc.current_market_value)
        self.assertIsNone(acc.valuation_ledger_anchor)

    def test_name_change_preserves_anchor(self):
        acc = self.investment(D("9000"), D("8000"))
        db = FakeDB(acc)
        update(
            10,
            FakeData(name="Renamed"),
            current_user=user, db=db,
        )
        self.assertEqual(acc.current_market_value, D("9000"))
        self.assertEqual(acc.valuation_ledger_anchor, D("8000"))

    def test_starting_balance_and_revaluation(self):
        acc = self.investment(D("9000"), D("8000"))
        db = FakeDB(acc, D("10000"))
        update(
            10,
            FakeData(
                starting_balance=D("6000"),
                current_market_value=D("12000"),
            ),
            current_user=user, db=db,
        )
        self.assertEqual(acc.starting_balance, D("6000"))
        self.assertEqual(acc.valuation_ledger_anchor, D("11000"))

    def test_investment_type_conversion_rejected(self):
        acc = self.investment()
        db = FakeDB(acc)
        with self.assertRaises(HTTPException) as caught:
            update(
                10,
                FakeData(type=AccountType.BANK),
                current_user=user, db=db,
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(db.commits, 0)

    def test_cross_user_update_rejected(self):
        acc = self.investment()
        db = FakeDB(acc)
        with self.assertRaises(HTTPException) as caught:
            update(
                10,
                FakeData(current_market_value=D("500")),
                current_user=SimpleNamespace(id=2), db=db,
            )
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(db.commits, 0)

    def test_response_only_anchor_and_summary_preserved(self):
        syntax = ast.parse(
            schema_path.read_text(encoding="utf-8-sig")
        )

        fields = {}

        for name in (
            "AccountBase",
            "AccountUpdate",
            "AccountResponse",
            "AccountBalanceSummary",
        ):
            cls = next(
                item for item in syntax.body
                if isinstance(item, ast.ClassDef)
                and item.name == name
            )

            fields[name] = {
                item.target.id: item.value
                for item in cls.body
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
            }

        self.assertNotIn(
            "valuation_ledger_anchor", fields["AccountBase"]
        )
        self.assertNotIn(
            "valuation_ledger_anchor", fields["AccountUpdate"]
        )
        self.assertIn(
            "valuation_ledger_anchor", fields["AccountResponse"]
        )

        for name in ("AccountBase", "AccountUpdate"):
            self.assertIsInstance(
                fields[name]["current_market_value"],
                ast.Call,
            )

        self.assertIsInstance(
            fields["AccountBalanceSummary"]["current_market_value"],
            ast.Constant,
        )
        self.assertIsNone(
            fields["AccountBalanceSummary"]["current_market_value"].value
        )


if __name__ == "__main__":
    unittest.main()
