"""In-memory SQLite tests of AST-extracted PH2.5 membership store.

Uses test-only ORM models, not the live app or a Neon connection.
"""
import ast
import copy
import importlib.util
import unittest
from decimal import Decimal as D
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import Column, ForeignKey, Integer, Numeric, String, create_engine, and_, or_
from sqlalchemy.orm import declarative_base, sessionmaker, Session

MODULE = Path('modules/finance/valuation_membership_store.py')
PROVENANCE = Path('modules/finance/snapshot_provenance.py')

spec = importlib.util.spec_from_file_location('ph25_pure_provenance_for_sqlite', PROVENANCE)
pure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pure)

Base = declarative_base()

class AccountType:
    INVESTMENT = 'INVESTMENT'
    BANK = 'BANK'

class TransactionType:
    INCOME = 'INCOME'
    EXPENSE = 'EXPENSE'
    TRANSFER = 'TRANSFER'

class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    type = Column(String, nullable=False)
    current_market_value = Column(Numeric(14, 2), nullable=True)
    valuation_ledger_anchor = Column(Numeric(14, 2), nullable=True)

class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    type = Column(String, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    account_id = Column(Integer, nullable=True)
    from_account_id = Column(Integer, nullable=True)
    to_account_id = Column(Integer, nullable=True)

class InvestmentValuationMember(Base):
    __tablename__ = 'investment_valuation_members'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    transaction_id = Column(Integer, nullable=False)
    baseline_contribution = Column(Numeric(14, 2), nullable=False)
    from sqlalchemy import UniqueConstraint
    __table_args__ = (UniqueConstraint('account_id', 'transaction_id'),)

source = ast.parse(MODULE.read_text(encoding='utf-8-sig'))
nodes = [copy.deepcopy(n) for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'replace_investment_valuation_members']
if len(nodes) != 1:
    raise AssertionError('Expected exactly one persistence helper')
code = ast.Module(body=nodes, type_ignores=[])
ast.fix_missing_locations(code)
namespace = {
    'Session': Session, 'Account': Account, 'AccountType': AccountType,
    'Transaction': Transaction, 'TransactionType': TransactionType,
    'InvestmentValuationMember': InvestmentValuationMember,
    'capture_account_membership': pure.capture_account_membership,
    'HTTPException': HTTPException, 'or_': or_, 'and_': and_,
}
exec(compile(code, str(MODULE), 'exec'), namespace)
replace = namespace['replace_investment_valuation_members']

class MembershipStoreContract(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite+pysqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.a = Account(id=1, user_id=1, type='INVESTMENT',
                         current_market_value=D('12500'), valuation_ledger_anchor=D('12000'))
        self.b = Account(id=2, user_id=1, type='BANK')
        self.c = Account(id=3, user_id=2, type='INVESTMENT',
                         current_market_value=D('100'), valuation_ledger_anchor=D('0'))
        self.db.add_all([self.a, self.b, self.c])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def tx(self, n, typ, amount, owner=1, acct=None, src=None, dst=None):
        item = Transaction(id=n, user_id=owner, type=typ, amount=D(str(amount)),
                           account_id=acct, from_account_id=src, to_account_id=dst)
        self.db.add(item)
        self.db.flush()
        return item

    def members(self, aid=1):
        self.db.flush()
        return {m.transaction_id: m.baseline_contribution for m in
                self.db.query(InvestmentValuationMember).filter_by(account_id=aid).all()}

    def capture(self, account=None, user_id=1):
        return replace(self.db, user_id, account or self.a)

    def test_income_and_expense_signs(self):
        self.tx(1, 'INCOME', '150', acct=1)
        self.tx(2, 'EXPENSE', '25', acct=1)
        self.assertEqual(self.capture(), 2)
        self.assertEqual(self.members(), {1:D('150'), 2:D('-25')})

    def test_transfer_in_and_out_signs(self):
        self.tx(1, 'TRANSFER', '200', src=2, dst=1, acct=2)
        self.tx(2, 'TRANSFER', '75', src=1, dst=2, acct=1)
        self.assertEqual(self.capture(), 2)
        self.assertEqual(self.members(), {1:D('200'), 2:D('-75')})

    def test_other_user_transactions_excluded(self):
        self.tx(1, 'INCOME', '100', owner=2, acct=1)
        self.tx(2, 'INCOME', '50', owner=1, acct=1)
        self.capture()
        self.assertEqual(self.members(), {2:D('50')})

    def test_unrelated_transactions_excluded(self):
        self.tx(1, 'INCOME', '100', acct=2)
        self.tx(2, 'TRANSFER', '100', src=2, dst=3, acct=2)
        self.capture()
        self.assertEqual(self.members(), {})

    def test_same_account_transfer_net_zero_still_member(self):
        self.tx(1, 'TRANSFER', '100', src=1, dst=1, acct=1)
        self.capture()
        self.assertEqual(self.members(), {1:D('0')})

    def test_zero_income_still_member(self):
        self.tx(1, 'INCOME', '0', acct=1)
        self.capture()
        self.assertEqual(self.members(), {1:D('0')})

    def test_missing_transfer_source_not_synthesized(self):
        self.tx(1, 'TRANSFER', '100', src=None, dst=2, acct=1)
        self.capture()
        self.assertEqual(self.members(), {})

    def test_snapshot_members_replaced_without_uniqueness_conflict(self):
        tx = self.tx(1, 'INCOME', '100', acct=1)
        self.capture()
        self.assertEqual(self.members(), {1:D('100')})
        tx.amount = D('150')
        self.capture()
        self.assertEqual(self.members(), {1:D('150')})

    def test_recapture_includes_new_transaction(self):
        self.tx(1, 'INCOME', '100', acct=1)
        self.capture()
        self.tx(2, 'TRANSFER', '50', src=2, dst=1, acct=2)
        self.capture()
        self.assertEqual(self.members(), {1:D('100'),2:D('50')})

    def test_snapshot_clearing_drops_members(self):
        self.tx(1, 'INCOME', '100', acct=1)
        self.capture()
        self.a.current_market_value = None
        self.a.valuation_ledger_anchor = None
        self.assertEqual(self.capture(), 0)
        self.assertEqual(self.members(), {})

    def test_explicit_zero_snapshot_records_members(self):
        self.a.current_market_value = D('0')
        self.a.valuation_ledger_anchor = D('100')
        self.tx(1, 'EXPENSE', '10', acct=1)
        self.capture()
        self.assertEqual(self.members(), {1:D('-10')})

    def test_null_snapshot_null_anchor_skips_transaction_query(self):
        self.a.current_market_value = None
        self.a.valuation_ledger_anchor = None
        self.tx(1, 'INCOME', '100', acct=1)
        self.assertEqual(self.capture(), 0)
        self.assertEqual(self.members(), {})

    def test_positive_snapshot_missing_anchor_rejected(self):
        self.a.valuation_ledger_anchor = None
        with self.assertRaises(HTTPException) as raised:
            self.capture()
        self.assertEqual(raised.exception.status_code, 409)

    def test_orphan_anchor_rejected(self):
        self.a.current_market_value = None
        with self.assertRaises(HTTPException) as raised:
            self.capture()
        self.assertEqual(raised.exception.status_code, 409)

    def test_wrong_user_account_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            self.capture(user_id=2)
        self.assertEqual(raised.exception.status_code, 404)

    def test_noninvestment_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            self.capture(account=self.b)
        self.assertEqual(raised.exception.status_code, 409)

    def test_invalid_historical_negative_amount_preserves_members(self):
        self.tx(1, 'INCOME', '100', acct=1)
        self.capture()
        self.db.commit()
        self.tx(2, 'EXPENSE', '-10', acct=1)
        with self.assertRaises(HTTPException) as raised:
            self.capture()
        self.assertEqual(raised.exception.status_code, 409)
        self.db.rollback()
        self.assertEqual(self.members(), {1:D('100')})

    def test_no_commit_inside_helper_and_rollback_reverts(self):
        self.tx(1, 'INCOME', '100', acct=1)
        self.db.commit()
        self.assertEqual(self.capture(), 1)
        self.db.rollback()
        self.assertEqual(self.members(), {})

    def test_tx_delete_keeps_membership_tombstone(self):
        tx = self.tx(1, 'INCOME', '100', acct=1)
        self.capture()
        self.db.commit()
        self.db.delete(tx)
        self.db.commit()
        self.assertEqual(self.members(), {1:D('100')})

if __name__ == '__main__':
    unittest.main()
