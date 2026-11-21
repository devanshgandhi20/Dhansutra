"""PH2.5: Account revaluation and snapshot membership run atomically.

Executes the actual AST-extracted account route with a fake DB.
No live app imports, external DB, or Alembic execution.
"""
import importlib.util
import unittest
from pathlib import Path

PATH = Path('tests/test_ph25_account_api_contract.py')
spec = importlib.util.spec_from_file_location('ph25_membership_account_base', PATH)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

D = base.D

class AccountMembershipCapture(unittest.TestCase):
    def setUp(self):
        self.scope = base.update.__globals__
        self.original_store = self.scope['replace_investment_valuation_members']
        self.flush = base.FakeDB.flush
        self.rollback = base.FakeDB.rollback
        self.commit = base.FakeDB.commit
        self.events = []
        def flushed(db):
            self.events.append('flush')
        def rolled_back(db):
            self.events.append('rollback')
        def committed(db):
            self.events.append('commit')
            self.commit(db)
        base.FakeDB.flush = flushed
        base.FakeDB.rollback = rolled_back
        base.FakeDB.commit = committed

    def tearDown(self):
        self.scope['replace_investment_valuation_members'] = self.original_store
        base.FakeDB.flush = self.flush
        base.FakeDB.rollback = self.rollback
        base.FakeDB.commit = self.commit

    def account_db(self, snapshot=D('9000'), anchor=D('8000')):
        acc = base.PH25AccountAPIContract().investment(snapshot, anchor)
        return acc, base.FakeDB(acc, D('10000'))

    def perform(self, db, **fields):
        return base.update(
            10, base.FakeData(**fields), current_user=base.user, db=db
        )

    def tracking_store(self):
        def store(db, user_id, account):
            self.events.append('capture')
            self.assertEqual(user_id, 1)
            self.assertEqual(account.id, 10)
            return 3
        self.scope['replace_investment_valuation_members'] = store

    def test_positive_revaluation_capture_before_commit(self):
        self.tracking_store()
        account, db = self.account_db()
        self.perform(db, current_market_value=D('12000'))
        self.assertEqual(self.events, ['flush', 'capture', 'commit'])
        self.assertEqual(account.current_market_value, D('12000'))
        self.assertEqual(account.valuation_ledger_anchor, D('10000'))
        self.assertEqual(db.commits, 1)

    def test_explicit_zero_captures_membership(self):
        self.tracking_store()
        account, db = self.account_db()
        self.perform(db, current_market_value=D('0'))
        self.assertEqual(self.events, ['flush', 'capture', 'commit'])
        self.assertEqual(account.current_market_value, D('0'))
        self.assertEqual(account.valuation_ledger_anchor, D('10000'))

    def test_clear_snapshot_clears_membership(self):
        self.tracking_store()
        account, db = self.account_db()
        self.perform(db, current_market_value=None)
        self.assertEqual(self.events, ['flush', 'capture', 'commit'])
        self.assertIsNone(account.current_market_value)
        self.assertIsNone(account.valuation_ledger_anchor)

    def test_name_only_update_does_not_recapture(self):
        self.tracking_store()
        account, db = self.account_db()
        self.perform(db, name='Renamed')
        self.assertEqual(self.events, ['commit'])
        self.assertEqual(account.name, 'Renamed')

    def test_starting_balance_only_does_not_recapture(self):
        self.tracking_store()
        account, db = self.account_db()
        self.perform(db, starting_balance=D('6000'))
        self.assertEqual(self.events, ['flush', 'commit'])
        self.assertEqual(account.starting_balance, D('6000'))

    def test_starting_balance_and_valuation_capture_together(self):
        self.tracking_store()
        account, db = self.account_db()
        self.perform(db, starting_balance=D('6000'), current_market_value=D('12500'))
        self.assertEqual(self.events, ['flush', 'capture', 'commit'])
        self.assertEqual(account.valuation_ledger_anchor, D('11000'))

    def test_store_error_rolls_back_before_commit(self):
        def fail(db, user_id, account):
            self.events.append('capture')
            raise RuntimeError('Simulated membership failure')
        self.scope['replace_investment_valuation_members'] = fail
        account, db = self.account_db()
        with self.assertRaisesRegex(RuntimeError, 'Simulated membership failure'):
            self.perform(db, current_market_value=D('11000'))
        self.assertEqual(self.events, ['flush', 'capture', 'rollback'])
        self.assertEqual(db.commits, 0)

    def test_flush_error_rolls_back_without_membership_capture(self):
        self.tracking_store()
        def fail(db):
            self.events.append('flush')
            raise RuntimeError('Simulated flush failure')
        base.FakeDB.flush = fail
        account, db = self.account_db()
        with self.assertRaisesRegex(RuntimeError, 'Simulated flush failure'):
            self.perform(db, current_market_value=D('11000'))
        self.assertEqual(self.events, ['flush', 'rollback'])
        self.assertEqual(db.commits, 0)

if __name__ == '__main__':
    unittest.main()
