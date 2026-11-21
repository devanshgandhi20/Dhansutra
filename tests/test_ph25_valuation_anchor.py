
"""Isolated PH2.5 ledger-anchor contract tests."""

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path


MODULE_PATH = Path("modules/finance/valuation.py")

spec = importlib.util.spec_from_file_location(
    "ph25_valuation_isolated",
    MODULE_PATH,
)

valuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(valuation)

D = Decimal


class PH25ValuationAnchorTests(unittest.TestCase):

    def effective(self, ledger, snapshot=None, anchor=None):
        return valuation.effective_investment_value(
            ledger, snapshot, anchor
        )

    def test_unvalued_account_uses_ledger(self):
        self.assertEqual(
            self.effective(D("12000")),
            D("12000"),
        )

    def test_legacy_zero_uses_ledger(self):
        self.assertEqual(
            self.effective(D("12000"), D("0"), None),
            D("12000"),
        )

    def test_positive_snapshot_requires_anchor(self):
        with self.assertRaises(ValueError):
            self.effective(D("12000"), D("10000"), None)

    def test_orphan_anchor_rejected(self):
        with self.assertRaises(ValueError):
            self.effective(D("12000"), None, D("10000"))

    def test_explicit_zero_with_anchor_is_respected(self):
        self.assertEqual(
            self.effective(D("10000"), D("0"), D("10000")),
            D("0"),
        )

    def test_initial_positive_valuation(self):
        self.assertEqual(
            self.effective(D("10000"), D("11000"), D("10000")),
            D("11000"),
        )

    def test_incoming_transfer_added_once(self):
        self.assertEqual(
            self.effective(D("12000"), D("11000"), D("10000")),
            D("13000"),
        )

    def test_transfer_amount_edit(self):
        self.assertEqual(
            self.effective(D("15000"), D("11000"), D("10000")),
            D("16000"),
        )

    def test_transfer_deletion_restores_value(self):
        self.assertEqual(
            self.effective(D("10000"), D("11000"), D("10000")),
            D("11000"),
        )

    def test_exact_zero_withdrawal_reversible(self):
        self.assertEqual(
            self.effective(D("0"), D("2000"), D("2000")),
            D("0"),
        )
        self.assertEqual(
            self.effective(D("2000"), D("2000"), D("2000")),
            D("2000"),
        )

    def test_underflow_guard(self):
        candidate = self.effective(
            D("0"), D("500"), D("800")
        )
        with self.assertRaises(ValueError):
            valuation.require_nonnegative_investment_value(candidate)

    def test_manual_revaluation_resets_anchor(self):
        snapshot, anchor = valuation.set_valuation_snapshot(
            D("12500"), D("12000")
        )

        self.assertEqual(
            self.effective(D("12000"), snapshot, anchor),
            D("12500"),
        )
        self.assertEqual(
            self.effective(D("14000"), snapshot, anchor),
            D("14500"),
        )

    def test_investment_transfer_conserves_combined_value(self):
        source = self.effective(
            D("5000"), D("7000"), D("7000")
        )
        destination = self.effective(
            D("5000"), D("3000"), D("3000")
        )

        self.assertEqual(source + destination, D("10000"))

    def test_clear_snapshot_removes_anchor(self):
        self.assertEqual(
            valuation.set_valuation_snapshot(None, D("12000")),
            (None, None),
        )


if __name__ == "__main__":
    unittest.main()
