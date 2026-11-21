"""PH2.5 current-path accounting and preserved dashboard contracts.

Ten replacement tests exercise AST-extracted current valuation/ledger/anchor
helpers; three original account/dashboard-source tests remain unchanged.
These tests do not connect to PostgreSQL or exercise FastAPI HTTP routes.
"""
import ast
import unittest
from decimal import Decimal, InvalidOperation
from pathlib import Path

D = Decimal
ROOT = Path.cwd().resolve()


def load_pure(rel_path, names, namespace):
    path = ROOT / rel_path
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    chosen = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in chosen} != set(names) or len(chosen) != len(names):
        raise AssertionError("PH2.5 pure-function source contract changed: " + rel_path)
    module = ast.Module(body=chosen, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)


scope = {"Decimal": D, "InvalidOperation": InvalidOperation, "ZERO": D("0")}
load_pure("modules/finance/valuation.py", {
    "as_decimal", "set_valuation_snapshot", "effective_investment_value",
    "require_nonnegative_investment_value",
}, scope)
load_pure("modules/finance/snapshot_provenance.py", {
    "_amount", "_account_id", "transaction_contributions",
    "capture_account_membership", "rebase_included_anchor",
}, scope)

set_snapshot = scope["set_valuation_snapshot"]
value = scope["effective_investment_value"]
guard = scope["require_nonnegative_investment_value"]
contributions = scope["transaction_contributions"]
capture = scope["capture_account_membership"]
rebase = scope["rebase_included_anchor"]


class PH25TransferContract(unittest.TestCase):
    def test_null_snapshot_uses_ledger_fallback(self):
        self.assertEqual(set_snapshot(None, D("500")), (None, None))
        self.assertEqual(value(D("1200"), None, None), D("1200"))

    def test_positive_snapshot_remains_unchanged_on_transfer(self):
        snapshot, anchor = set_snapshot(D("10000"), D("5000"))
        movement = contributions("TRANSFER", D("2000"),
                                 account_id=1, from_account_id=1, to_account_id=2)
        self.assertEqual(movement, {1: D("-2000"), 2: D("2000")})
        self.assertEqual(value(anchor + movement[2], snapshot, anchor), D("12000"))
        self.assertEqual(snapshot, D("10000"))
        self.assertEqual(anchor, D("5000"))

    def test_bank_to_bank_transfer_conserves_net_worth(self):
        movement = contributions("TRANSFER", D("750"),
                                 account_id=1, from_account_id=1, to_account_id=3)
        self.assertEqual(movement, {1: D("-750"), 3: D("750")})
        self.assertEqual(sum(movement.values(), D("0")), D("0"))

    def test_legacy_zero_unanchored_uses_ledger(self):
        stored_zero = D("0")
        self.assertEqual(value(D("2000"), stored_zero, None), D("2000"))
        self.assertEqual(stored_zero, D("0"))

    def test_explicit_zero_snapshot_stays_zero(self):
        snapshot, anchor = set_snapshot(D("0"), D("500"))
        self.assertEqual((snapshot, anchor), (D("0"), D("500")))
        movement = contributions("TRANSFER", D("2000"),
                                 account_id=1, from_account_id=1, to_account_id=2)
        self.assertEqual(value(anchor + movement[2], snapshot, anchor), D("2000"))
        self.assertEqual(snapshot, D("0"))

    def test_underflow_is_rejected_before_commit(self):
        snapshot, anchor = set_snapshot(D("500"), D("1000"))
        movement = contributions("TRANSFER", D("800"),
                                 account_id=2, from_account_id=2, to_account_id=1)
        invalid_effective = value(anchor + movement[2], snapshot, anchor)
        self.assertEqual(invalid_effective, D("-300"))
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            guard(invalid_effective)
        self.assertEqual(value(anchor, snapshot, anchor), D("500"))

    def test_transfer_round_trip_restores_effective_value(self):
        snapshot, anchor = set_snapshot(D("500"), D("1000"))
        inbound = contributions("TRANSFER", D("300"),
                                account_id=1, from_account_id=1, to_account_id=2)
        interim_ledger = anchor + inbound[2]
        self.assertEqual(guard(value(interim_ledger, snapshot, anchor)), D("800"))
        outbound = contributions("TRANSFER", D("300"),
                                 account_id=2, from_account_id=2, to_account_id=1)
        final_ledger = interim_ledger + outbound[2]
        self.assertEqual(guard(value(final_ledger, snapshot, anchor)), D("500"))
        self.assertEqual(snapshot, D("500"))

    def test_investment_to_investment_transfer_conserves_value(self):
        first_snapshot, first_anchor = set_snapshot(D("700"), D("2000"))
        second_snapshot, second_anchor = set_snapshot(D("900"), D("3000"))
        move = contributions("TRANSFER", D("200"),
                             account_id=2, from_account_id=2, to_account_id=3)
        first = guard(value(first_anchor + move[2], first_snapshot, first_anchor))
        second = guard(value(second_anchor + move[3], second_snapshot, second_anchor))
        self.assertEqual((first, second), (D("500"), D("1100")))
        self.assertEqual(first + second, D("1600"))
        self.assertEqual((first_snapshot, second_snapshot), (D("700"), D("900")))

    def test_included_historical_edit_rebases_anchor(self):
        rows = [{"id": 91, "type": "TRANSFER", "amount": D("100"),
                 "account_id": 1, "from_account_id": 1, "to_account_id": 2}]
        frozen_membership = capture(2, rows)
        self.assertEqual(frozen_membership, {91: D("100")})
        snapshot, anchor = set_snapshot(D("700"), D("1500"))
        after_anchor = rebase(anchor, included=91 in frozen_membership,
                              before=D("100"), after=D("180"))
        self.assertEqual(after_anchor, D("1580"))
        self.assertEqual(value(D("1580"), snapshot, after_anchor), snapshot)
        self.assertEqual(frozen_membership[91], D("100"))

    def test_included_historical_delete_rebases_anchor(self):
        rows = [{"id": 91, "type": "TRANSFER", "amount": D("100"),
                 "account_id": 1, "from_account_id": 1, "to_account_id": 2}]
        frozen_membership = capture(2, rows)
        snapshot, anchor = set_snapshot(D("700"), D("1500"))
        after_anchor = rebase(anchor, included=91 in frozen_membership,
                              before=D("100"), after=D("0"))
        self.assertEqual(after_anchor, D("1400"))
        self.assertEqual(value(D("1400"), snapshot, after_anchor), snapshot)
        self.assertEqual(frozen_membership, {91: D("100")})

    def test_new_investment_creation_leaves_market_unset(self):
        dashboard = Path(
            "templates/user_dashboard.html"
        ).read_text(encoding="utf-8-sig")

        start = dashboard.index(
            "async function handleAccountCreate(e)"
        )
        end = dashboard.index(
            "function getAccountBalance(a)",
            start,
        )

        creation = dashboard[start:end]

        self.assertIn(
            "const payload = { name, type, starting_balance };",
            creation,
        )
        self.assertNotIn(
            "payload.current_market_value",
            creation,
        )

    def test_seed_investment_has_no_explicit_market_value(self):
        dashboard = Path(
            "templates/user_dashboard.html"
        ).read_text(encoding="utf-8-sig")

        section = dashboard.split(
            "async function bootstrapData()", 1
        )[1].split(
            "function isValidDashboardSummary", 1
        )[0]

        self.assertIn(
            "{ name: 'Mutual Funds', type: 'INVESTMENT', starting_balance: 0 }",
            section,
        )
        self.assertNotIn(
            "current_market_value: 0",
            section,
        )

    def test_valuation_editor_preserves_unset_value(self):
        dashboard = Path(
            "templates/user_dashboard.html"
        ).read_text(encoding="utf-8-sig")

        section = dashboard.split(
            "function openValuationsModal()", 1
        )[1].split(
            "function openGmailModal()", 1
        )[0]

        self.assertIn(
            "a.current_market_value ?? ''",
            section,
        )
        self.assertIn(
            "const raw = inp.value.trim();",
            section,
        )
        self.assertIn(
            "raw === '' ? null : Number(raw)",
            section,
        )
        self.assertIn(
            "if (existing === normalized) continue;",
            section,
        )
        self.assertIn(
            "current_market_value: update.current_market_value",
            section,
        )


if __name__ == "__main__":
    unittest.main()
