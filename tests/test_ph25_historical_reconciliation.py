
"""PH2.5 membership-aware historical corrections on disposable SQLite.

Runs AST-extracted helper against the test-only ORM models from the
membership-store contract. This is not a PostgreSQL locking test.
"""
import ast
import copy
import unittest
from decimal import Decimal as D
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from test_ph25_membership_store import (
    Base, Account, AccountType, InvestmentValuationMember, pure,
)

SRC = Path('modules/finance/valuation_transaction_reconciliation.py')
syntax = ast.parse(SRC.read_text(encoding='utf-8-sig'))
functions = [copy.deepcopy(node) for node in syntax.body
             if isinstance(node, ast.FunctionDef)
             and node.name == 'reconcile_valuation_transaction_change']
if len(functions) != 1:
    raise AssertionError('Unexpected PH2.5 reconciliation helper')
compiled = ast.Module(body=functions, type_ignores=[])
ast.fix_missing_locations(compiled)
namespace = {
    'Session': Session, 'Account': Account, 'AccountType': AccountType,
    'InvestmentValuationMember': InvestmentValuationMember,
    'HTTPException': HTTPException,
    'transaction_contributions': pure.transaction_contributions,
    'rebase_included_anchor': pure.rebase_included_anchor,
    'require_safe_account_reassignment': pure.require_safe_account_reassignment,
}
exec(compile(compiled, str(SRC), 'exec'), namespace)
reconcile = namespace['reconcile_valuation_transaction_change']


def tx(n=101, typ='TRANSFER', amount='2000', owner=1,
       acct=2, src=2, dst=1):
    return {'id': n, 'user_id': owner, 'type': typ, 'amount': D(str(amount)),
            'account_id': acct, 'from_account_id': src, 'to_account_id': dst}


class PH25HistoricalReconciliation(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite+pysqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.inv = Account(id=1, user_id=1, type='INVESTMENT',
                           current_market_value=D('12500'), valuation_ledger_anchor=D('12000'))
        self.bank = Account(id=2, user_id=1, type='BANK')
        self.other_inv = Account(id=3, user_id=1, type='INVESTMENT',
                                 current_market_value=D('6000'), valuation_ledger_anchor=D('6000'))
        self.other_owner = Account(id=4, user_id=2, type='INVESTMENT',
                                   current_market_value=D('1000'), valuation_ledger_anchor=D('1000'))
        self.db.add_all([self.inv, self.bank, self.other_inv, self.other_owner])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def member(self, aid=1, tid=101, value='2000'):
        self.db.add(InvestmentValuationMember(
            account_id=aid, transaction_id=tid, baseline_contribution=D(value)
        ))
        self.db.commit()

    def run_change(self, before, after, locked=None, ident=101, owner=1):
        return reconcile(self.db, owner, ident, before, after,
                         [self.inv, self.bank] if locked is None else locked)

    def test_included_transfer_in_amount_correction(self):
        self.member()
        result = self.run_change(tx(), tx(amount='3000'))
        self.assertEqual(result, {1: D('13000')})
        self.assertEqual(self.inv.current_market_value, D('12500'))
        self.assertEqual(self.inv.valuation_ledger_anchor, D('13000'))

    def test_included_transfer_in_deletion(self):
        self.member()
        self.run_change(tx(), None)
        self.assertEqual(self.inv.valuation_ledger_anchor, D('10000'))
        self.assertEqual(self.db.query(InvestmentValuationMember).count(), 1)

    def test_included_outgoing_transfer_change(self):
        self.member(value='-2000')
        self.run_change(tx(src=1, dst=2, acct=1),
                        tx(amount='3000', src=1, dst=2, acct=1))
        self.assertEqual(self.inv.valuation_ledger_anchor, D('11000'))

    def test_included_outgoing_deletion(self):
        self.member(value='-2000')
        self.run_change(tx(src=1, dst=2, acct=1), None)
        self.assertEqual(self.inv.valuation_ledger_anchor, D('14000'))

    def test_included_income_amount_increases_anchor(self):
        self.member(value='100')
        self.run_change(tx(typ='INCOME', amount='100', acct=1, src=None, dst=None),
                        tx(typ='INCOME', amount='250', acct=1, src=None, dst=None))
        self.assertEqual(self.inv.valuation_ledger_anchor, D('12150'))

    def test_included_expense_amount_increases_anchor_negatively(self):
        self.member(value='-100')
        self.run_change(tx(typ='EXPENSE', amount='100', acct=1, src=None, dst=None),
                        tx(typ='EXPENSE', amount='140', acct=1, src=None, dst=None))
        self.assertEqual(self.inv.valuation_ledger_anchor, D('11960'))

    def test_repeated_edits_use_current_delta(self):
        self.member()
        before = tx()
        middle = tx(amount='3000')
        after = tx(amount='3500')
        self.run_change(before, middle)
        self.run_change(middle, after)
        self.assertEqual(self.inv.valuation_ledger_anchor, D('13500'))
        self.assertEqual(self.db.query(InvestmentValuationMember).one().baseline_contribution, D('2000'))

    def test_new_transfer_not_rebased(self):
        result = self.run_change(None, tx())
        self.assertEqual(result, {})
        self.assertEqual(self.inv.valuation_ledger_anchor, D('12000'))

    def test_existing_nonmember_edit_not_rebased(self):
        result = self.run_change(tx(), tx(amount='3000'))
        self.assertEqual(result, {})
        self.assertEqual(self.inv.valuation_ledger_anchor, D('12000'))

    def test_date_only_edit_no_delta(self):
        self.member()
        self.run_change(tx(), tx())
        self.assertEqual(self.inv.valuation_ledger_anchor, D('12000'))

    def test_account_reassignment_from_valued_rejected(self):
        self.member()
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(), tx(dst=2, src=3, acct=3),
                            locked=[self.inv, self.bank, self.other_inv])
        self.assertEqual(e.exception.status_code, 409)

    def test_account_reassignment_to_valued_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(dst=2, src=2), tx(dst=1, src=2),
                            locked=[self.inv, self.bank])
        self.assertEqual(e.exception.status_code, 409)

    def test_bank_only_reassignment_allowed(self):
        account5 = Account(id=5, user_id=1, type='BANK')
        self.db.add(account5)
        self.db.commit()
        result = self.run_change(tx(typ='EXPENSE', amount='50', acct=2, src=None, dst=None),
                                 tx(typ='EXPENSE', amount='50', acct=5, src=None, dst=None),
                                 locked=[self.bank, account5])
        self.assertEqual(result, {})

    def test_missing_locked_affected_account_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(), tx(amount='4000'), locked=[self.inv])
        self.assertEqual(e.exception.status_code, 409)

    def test_foreign_user_locked_account_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(), tx(amount='4000'), locked=[self.inv, self.bank, self.other_owner])
        self.assertEqual(e.exception.status_code, 404)

    def test_wrong_user_transaction_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(owner=2), tx(amount='4000'))
        self.assertEqual(e.exception.status_code, 404)

    def test_wrong_transaction_id_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(), tx(n=102, amount='4000'))
        self.assertEqual(e.exception.status_code, 409)

    def test_legacy_zero_unanchored_not_rebased(self):
        self.inv.current_market_value = D('0')
        self.inv.valuation_ledger_anchor = None
        self.run_change(tx(), tx(amount='3000'))
        self.assertIsNone(self.inv.valuation_ledger_anchor)

    def test_positive_snapshot_without_anchor_rejected(self):
        self.inv.valuation_ledger_anchor = None
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(), tx(amount='3000'))
        self.assertEqual(e.exception.status_code, 409)

    def test_rollback_reverts_rebase_and_keeps_member(self):
        self.member()
        self.run_change(tx(), tx(amount='3000'))
        self.db.flush()
        self.db.rollback()
        self.assertEqual(self.inv.valuation_ledger_anchor, D('12000'))
        self.assertEqual(self.db.query(InvestmentValuationMember).one().baseline_contribution, D('2000'))

    def test_invalid_negative_amount_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(), tx(amount='-100'))
        self.assertEqual(e.exception.status_code, 409)

    def test_invalid_id_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(tx(), tx(), ident=True)
        self.assertEqual(e.exception.status_code, 422)

    def test_reused_tombstone_transaction_id_rejected(self):
        self.member()
        with self.assertRaises(HTTPException) as e:
            self.run_change(None, tx())
        self.assertEqual(e.exception.status_code, 409)

    def test_both_states_absent_rejected(self):
        with self.assertRaises(HTTPException) as e:
            self.run_change(None, None)
        self.assertEqual(e.exception.status_code, 422)

    def test_unrelated_membership_left_intact(self):
        self.member(aid=3, tid=101, value='900')
        result = self.run_change(tx(), tx(amount='3000'),
                                 locked=[self.inv, self.bank, self.other_inv])
        self.assertEqual(result, {})
        self.assertEqual(self.other_inv.valuation_ledger_anchor, D('6000'))
        self.assertEqual(self.db.query(InvestmentValuationMember).one().baseline_contribution, D('900'))


if __name__ == '__main__':
    unittest.main()
