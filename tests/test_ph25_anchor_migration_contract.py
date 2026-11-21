"""PH2.5: Local migration structure and operation tests.

No database connection or migration execution.
"""

import ast
import importlib.util
import unittest
from pathlib import Path


MIGRATION_PATH = Path(
    "alembic/versions/"
    "a0d91bc6e275_ph25_valuation_ledger_anchor.py"
)

MODELS_PATH = Path("modules/finance/models.py")


class OperationRecorder:
    def __init__(self):
        self.calls = []

    def add_column(self, table, column):
        self.calls.append(("add", table, column))

    def execute(self, statement):
        self.calls.append(("execute", str(statement)))

    def drop_column(self, table, column):
        self.calls.append(("drop", table, column))


def load_migration():
    spec = importlib.util.spec_from_file_location(
        "ph25_migration_contract",
        MIGRATION_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PH25MigrationContract(unittest.TestCase):

    def test_revision_chain(self):
        migration = load_migration()

        self.assertEqual(
            migration.revision, "a0d91bc6e275"
        )
        self.assertEqual(
            migration.down_revision, "7b4c2d8e9f01"
        )

    def test_account_model_has_nullable_anchor(self):
        source = MODELS_PATH.read_text(
            encoding="utf-8-sig"
        )
        tree = ast.parse(source)

        account = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "Account"
        )

        names = [
            target.id
            for node in account.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        ]

        self.assertEqual(
            names.count("valuation_ledger_anchor"),
            1,
        )
        self.assertIn(
            "valuation_ledger_anchor = Column(Numeric(14, 2), nullable=True)",
            source,
        )

    def test_upgrade_adds_nullable_numeric_column(self):
        migration = load_migration()
        recorder = OperationRecorder()
        migration.op = recorder

        migration.upgrade()

        self.assertEqual(len(recorder.calls), 2)

        operation, table, column = recorder.calls[0]

        self.assertEqual(operation, "add")
        self.assertEqual(table, "accounts")
        self.assertEqual(
            column.name, "valuation_ledger_anchor"
        )
        self.assertTrue(column.nullable)
        self.assertEqual(str(column.type), "NUMERIC(14, 2)")

    def test_backfill_preserves_legacy_valuations(self):
        migration = load_migration()
        recorder = OperationRecorder()
        migration.op = recorder

        migration.upgrade()

        operation, sql = recorder.calls[1]

        self.assertEqual(operation, "execute")

        required = [
            "a.current_market_value > 0",
            "a.type = 'INVESTMENT'",
            "a.starting_balance",
            "m.user_id = a.user_id",
            "m.account_id = a.id",
            "type = 'INCOME'",
            "type IN ('INCOME', 'EXPENSE')",
            "type = 'TRANSFER'",
            "to_account_id",
            "from_account_id",
            "-amount AS delta",
            "COALESCE",
        ]

        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, sql)

        # The migration must never UPDATE the snapshot itself.
        self.assertNotIn(
            "SET current_market_value", sql
        )
        self.assertNotIn(
            "UPDATE transactions", sql
        )

    def test_downgrade_drops_only_anchor_column(self):
        migration = load_migration()
        recorder = OperationRecorder()
        migration.op = recorder

        migration.downgrade()

        self.assertEqual(
            recorder.calls,
            [("drop", "accounts", "valuation_ledger_anchor")],
        )


if __name__ == "__main__":
    unittest.main()
