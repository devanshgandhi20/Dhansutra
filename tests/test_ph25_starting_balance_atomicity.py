"""PH2.5 starting balance and valuation accounting execution contract.

Runs actual AST-extracted local update_account with a deterministic fake DB;
no app imports, database access, or PostgreSQL concurrency claims.
"""
import ast
import copy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

D=Decimal

class HTTPException(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        super().__init__(detail)

class AccountType:
    INVESTMENT='INVESTMENT'
    BANK='BANK'
    GOAL_RESERVE='GOAL_RESERVE'
    CASH='CASH'

class FakeData:
    def __init__(self,**kw):self.data=kw
    def model_dump(self,exclude_unset=False):return dict(self.data)

class Account:
    def __init__(self,kind='INVESTMENT',start='5000',snapshot='9000',anchor='8000',movement='5000'):
        self.id=10
        self.user_id=1
        self.type=kind
        self.name='Invest'
        self.starting_balance=D(start)
        self.current_market_value=None if snapshot is None else D(snapshot)
        self.valuation_ledger_anchor=None if anchor is None else D(anchor)
        self.movement=D(movement)

class FakeDB:
    def __init__(self,account=None,fail=None):
        self.acc=account or Account()
        self.initial=copy.deepcopy(self.acc.__dict__)
        self.events=[]
        self.commits=0
        self.fail=fail
    def flush(self):
        self.events.append('flush')
        if self.fail=='flush':raise RuntimeError('flush failed')
    def commit(self):
        self.events.append('commit')
        if self.fail=='commit':raise RuntimeError('commit failed')
        self.commits+=1
    def rollback(self):
        self.events.append('rollback')
        self.acc.__dict__.clear()
        self.acc.__dict__.update(copy.deepcopy(self.initial))
    def refresh(self,account):self.events.append('refresh')

def lock_owned_accounts(db,user_id,ids):
    db.events.append('lock')
    if user_id!=db.acc.user_id or ids!=(db.acc.id,):
        raise HTTPException(404,'Account not found')
    return [db.acc]

def ledger(db):return db.acc.starting_balance+db.acc.movement

class FinanceService:
    @staticmethod
    def calculate_account_balances(db,user_id):
        db.events.append('ledger-read')
        return [SimpleNamespace(id=db.acc.id,current_balance=ledger(db))]

def set_valuation_snapshot(requested,amount):
    if requested is None:return None,None
    result=D(str(requested))
    if result<0:raise ValueError('Market valuation cannot be negative')
    return result,D(str(amount))

def replace_investment_valuation_members(db,user_id,account):
    db.events.append('capture')
    if db.fail=='capture':raise RuntimeError('capture failed')

def effective_value(account):
    balance=account.starting_balance+account.movement
    if account.current_market_value is None:return balance
    if account.current_market_value==0 and account.valuation_ledger_anchor is None:
        return balance
    if account.valuation_ledger_anchor is None:
        raise HTTPException(409,'Snapshot missing anchor')
    return account.current_market_value + balance-account.valuation_ledger_anchor

def validate_postwrite_investments(db,user_id,accounts):
    db.events.append('validate')
    if db.fail=='validate':raise RuntimeError('validation failed')
    for acc in accounts:
        if acc.type=='INVESTMENT' and effective_value(acc)<0:
            raise HTTPException(409,'Investment underflow')

P=Path('modules/finance/router.py')
tree=ast.parse(P.read_text(encoding='utf-8-sig'))
fn=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='update_account']
assert len(fn)==1
extracted=copy.deepcopy(fn[0]);extracted.decorator_list=[]
module=ast.Module(body=[extracted],type_ignores=[])
ast.fix_missing_locations(module)
scope={
    'AccountType':AccountType,
    'AccountUpdate':FakeData,
    'Session':object,'User':object,
    'Depends':lambda x:None,'get_current_user':lambda:None,'get_db':lambda:None,
    'Decimal':D,
    'FinanceService':FinanceService,
    'HTTPException':HTTPException,
    'set_valuation_snapshot':set_valuation_snapshot,
    'replace_investment_valuation_members':replace_investment_valuation_members,
    'validate_postwrite_investments':validate_postwrite_investments,
    'lock_owned_accounts':lock_owned_accounts,
}
exec(compile(module,str(P),'exec'),scope)
UPDATE=scope['update_account']
USER=SimpleNamespace(id=1)

class PH25OpeningBalanceIntegrity(unittest.TestCase):
    def apply(self,db,**updates):
        return UPDATE(10,FakeData(**updates),current_user=USER,db=db)

    def test_anchored_start_increase_keeps_effective_valuation(self):
        db=FakeDB();before=effective_value(db.acc)
        self.apply(db,starting_balance=D('6500'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('9500'))
        self.assertEqual(before,effective_value(db.acc))
        self.assertEqual(db.events,['lock','flush','validate','commit','refresh'])

    def test_anchored_start_decrease_keeps_effective_valuation(self):
        db=FakeDB();before=effective_value(db.acc)
        self.apply(db,starting_balance=D('4000'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('7000'))
        self.assertEqual(effective_value(db.acc),before)

    def test_anchor_does_not_recapture_members_without_revaluation(self):
        db=FakeDB();self.apply(db,starting_balance=D('6000'))
        self.assertNotIn('capture',db.events)

    def test_anchored_explicit_zero_survives_opening_change(self):
        db=FakeDB(Account(snapshot='0',anchor='10000',movement='5000'))
        self.apply(db,starting_balance=D('5500'))
        self.assertEqual(db.acc.current_market_value,D('0'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('10500'))
        self.assertEqual(effective_value(db.acc),D('0'))

    def test_existing_post_snapshot_movement_retained(self):
        db=FakeDB(Account(snapshot='9000',anchor='8000',movement='6000'))
        before=effective_value(db.acc)
        self.apply(db,starting_balance=D('7200'))
        self.assertEqual(effective_value(db.acc),before)

    def test_combined_revaluation_sets_one_fresh_anchor(self):
        db=FakeDB()
        self.apply(db,starting_balance=D('6000'),current_market_value=D('12500'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('11000'))
        self.assertEqual(effective_value(db.acc),D('12500'))
        self.assertEqual(db.events,['lock','ledger-read','flush','capture','validate','commit','refresh'])

    def test_combined_explicit_zero_creates_new_anchor(self):
        db=FakeDB()
        self.apply(db,starting_balance=D('5500'),current_market_value=D('0'))
        self.assertEqual(db.acc.current_market_value,D('0'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('10500'))

    def test_combined_clear_reverts_to_ledger_and_membership_clear(self):
        db=FakeDB()
        self.apply(db,starting_balance=D('5800'),current_market_value=None)
        self.assertIsNone(db.acc.current_market_value)
        self.assertIsNone(db.acc.valuation_ledger_anchor)
        self.assertEqual(effective_value(db.acc),D('10800'))
        self.assertEqual(db.events,['lock','flush','capture','validate','commit','refresh'])

    def test_unvalued_investment_ledger_changes(self):
        db=FakeDB(Account(snapshot=None,anchor=None))
        self.apply(db,starting_balance=D('6000'))
        self.assertEqual(effective_value(db.acc),D('11000'))
        self.assertNotIn('capture',db.events)

    def test_legacy_zero_with_null_anchor_falls_back_to_ledger(self):
        db=FakeDB(Account(snapshot='0',anchor=None))
        self.apply(db,starting_balance=D('6000'))
        self.assertIsNone(db.acc.valuation_ledger_anchor)
        self.assertEqual(effective_value(db.acc),D('11000'))

    def test_bank_starting_balance_changes_without_investment_validator(self):
        db=FakeDB(Account(kind='BANK',snapshot=None,anchor=None))
        self.apply(db,starting_balance=D('4000'))
        self.assertEqual(db.events,['lock','commit','refresh'])
        self.assertEqual(db.acc.starting_balance,D('4000'))

    def test_name_only_does_not_flush_or_capture(self):
        db=FakeDB();self.apply(db,name='New Name')
        self.assertEqual(db.events,['lock','commit','refresh'])

    def test_same_start_balance_no_flush(self):
        db=FakeDB();self.apply(db,starting_balance=D('5000'))
        self.assertEqual(db.events,['lock','commit','refresh'])

    def test_orphan_anchor_rejected(self):
        db=FakeDB(Account(snapshot=None,anchor='8000'))
        with self.assertRaises(HTTPException) as ctx:self.apply(db,starting_balance=D('6000'))
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(db.commits,0)

    def test_unvalued_underflow_reverts_opening_edit(self):
        db=FakeDB(Account(snapshot=None,anchor=None,start='1000',movement='-800'))
        with self.assertRaises(HTTPException):self.apply(db,starting_balance=D('600'))
        self.assertEqual(db.acc.starting_balance,D('1000'))
        self.assertEqual(db.events[-1],'rollback')

    def test_clearing_snapshot_underflow_rolls_back(self):
        db=FakeDB(Account(start='0',snapshot='50',anchor='0',movement='-10'))
        with self.assertRaises(HTTPException):self.apply(db,current_market_value=None)
        self.assertEqual(db.acc.current_market_value,D('50'))
        self.assertEqual(db.events[-1],'rollback')

    def test_flush_failure_rollback_reverts_anchor(self):
        db=FakeDB(fail='flush')
        with self.assertRaises(RuntimeError):self.apply(db,starting_balance=D('6000'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('8000'))
        self.assertEqual(db.acc.starting_balance,D('5000'))
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')

    def test_commit_failure_reverts_anchor(self):
        db=FakeDB(fail='commit')
        with self.assertRaises(RuntimeError):self.apply(db,starting_balance=D('6000'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('8000'))
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')

    def test_validation_failure_rolls_back_and_does_not_commit(self):
        db=FakeDB(fail='validate')
        with self.assertRaises(RuntimeError):self.apply(db,starting_balance=D('6000'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('8000'))
        self.assertEqual(db.commits,0)

    def test_membership_failure_rolls_back_combined_revaluation(self):
        db=FakeDB(fail='capture')
        with self.assertRaises(RuntimeError):self.apply(db,current_market_value=D('15000'))
        self.assertEqual(db.acc.valuation_ledger_anchor,D('8000'))
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')

    def test_goals_starting_balance_protected(self):
        db=FakeDB(Account(kind='GOAL_RESERVE',start='0',snapshot=None,anchor=None))
        with self.assertRaises(HTTPException) as ctx:self.apply(db,starting_balance=D('100'))
        self.assertEqual(ctx.exception.status_code,409)
        self.assertEqual(db.commits,0)

    def test_investment_conversion_rejected(self):
        db=FakeDB()
        with self.assertRaises(HTTPException) as ctx:self.apply(db,type='BANK')
        self.assertEqual(ctx.exception.status_code,409)

if __name__=='__main__':unittest.main()
