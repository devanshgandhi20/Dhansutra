"""PH2.5 ordinary transaction creation route: isolated execution tests.

Uses a fake SQLAlchemy session. Does NOT establish PostgreSQL correctness.
"""
import ast
import importlib.util
import copy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

D=Decimal

spec=importlib.util.spec_from_file_location('ph25_valuation_for_create', 'modules/finance/valuation.py')
valuation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(valuation)

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code=status_code
        super().__init__(detail)
class IntegrityError(Exception):
    pass
class TransactionType:
    INCOME='INCOME'; EXPENSE='EXPENSE'; TRANSFER='TRANSFER'
class Field:
    def __eq__(self, other):
        return ('eq',other)
class Transaction:
    user_id=Field(); source_id=Field()
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
class FakeAccount:
    def __init__(self, id=10, type='INVESTMENT', ledger='1000', user_id=1, snapshot=None, anchor=None):
        self.id=id; self.type=type; self.user_id=user_id; self.ledger=D(ledger)
        self.current_market_value=snapshot
        self.valuation_ledger_anchor=anchor
class FakeQuery:
    def __init__(self, db):
        self.db=db
    def filter(self,*args):
        return self
    def first(self):
        self.db.events.append('duplicate_query')
        return Transaction() if self.db.duplicate else None
class FakeDB:
    def __init__(self, account=None, duplicate=False, error_at=None):
        self.accounts={account.id:account} if account else {}
        self.previous={a.id:a.ledger for a in self.accounts.values()}
        self.events=[]; self.duplicate=duplicate; self.error_at=error_at
        self.pending=None; self.commits=0
    def query(self, model):
        self.events.append('query')
        return FakeQuery(self)
    def add(self, tx):
        self.events.append('add'); self.pending=tx
    def flush(self):
        self.events.append('flush')
        if self.error_at=='flush':
            raise RuntimeError('simulated flush failure')
        tx=self.pending
        if tx.account_id is not None:
            acc=self.accounts[tx.account_id]
            direction=D('1') if tx.type==TransactionType.INCOME else D('-1')
            acc.ledger+=direction*D(tx.amount)
    def commit(self):
        self.events.append('commit')
        if self.error_at=='integrity':
            raise IntegrityError('duplicate')
        if self.error_at=='commit':
            raise RuntimeError('simulated commit error')
        self.commits+=1
    def rollback(self):
        self.events.append('rollback')
        for aid, old_ledger in self.previous.items():
            self.accounts[aid].ledger=old_ledger
        self.pending=None
    def refresh(self,tx):
        self.events.append('refresh')

def lock_owned_accounts(db,user_id,ids):
    db.events.append('lock')
    result=[]
    for aid in sorted(set(ids)):
        acc=db.accounts.get(aid)
        if acc is None or acc.user_id!=user_id:
            raise HTTPException(404, 'Account not found')
        result.append(acc)
    return result

def reject_goal_reserve_account(acc, label):
    if acc.type=='GOAL_RESERVE':
        raise HTTPException(409, 'Goal & Reserves protected')

def get_usable_category(db,user_id,category_id):
    db.events.append('category')
    if category_id==999:
        raise HTTPException(404, 'Category not found')
    return True

def validate_postwrite_investments(db,user_id,accounts):
    db.events.append('validate')
    for acc in accounts:
        if acc.type=='INVESTMENT':
            val=valuation.effective_investment_value(
                acc.ledger, acc.current_market_value,
                acc.valuation_ledger_anchor,
            )
            try:
                valuation.require_nonnegative_investment_value(val)
            except ValueError as exc:
                raise HTTPException(409, 'Investment balance cannot be negative') from exc

tree=ast.parse(Path('modules/finance/router.py').read_text(encoding='utf-8-sig'))
functions=[copy.deepcopy(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='create_transaction']
if len(functions)!=1:
    raise AssertionError('Expected exactly one create transaction route')
fn=functions[0]; fn.decorator_list=[]
module=ast.Module(body=[fn],type_ignores=[])
ast.fix_missing_locations(module)
ns={
    'TransactionCreate':object, 'User':object, 'Session':object,
    'Depends':lambda x:None, 'get_db':lambda:None, 'get_current_user':lambda:None,
    'HTTPException':HTTPException, 'IntegrityError':IntegrityError,
    'TransactionType':TransactionType, 'Transaction':Transaction,
    'lock_owned_accounts':lock_owned_accounts,
    'reject_goal_reserve_account':reject_goal_reserve_account,
    'get_usable_category':get_usable_category,
    'validate_postwrite_investments':validate_postwrite_investments,
    'status':SimpleNamespace(HTTP_404_NOT_FOUND=404),
}
exec(compile(module, 'modules/finance/router.py', 'exec'),ns)
create=ns['create_transaction']; user=SimpleNamespace(id=1)
class Data:
    def __init__(self,**kwargs):
        base={'type':'EXPENSE','amount':D('100'),'date':'2026-11-01',
              'account_id':10,'category_id':None,'source_id':None,
              'source':'MANUAL','description':'Test','income_period':None}
        base.update(kwargs)
        self.__dict__.update(base)
    def model_dump(self):
        return vars(self).copy()
class CreateAtomicity(unittest.TestCase):
    def test_investment_expense_with_ledger_fallback(self):
        acc=FakeAccount(); db=FakeDB(acc)
        tx=create(Data(),current_user=user,db=db)
        self.assertEqual(tx.account_id,10)
        self.assertEqual(acc.ledger,D('900'))
        self.assertEqual(db.events,['lock','add','flush','validate','commit','refresh'])
        self.assertEqual(db.commits,1)
    def test_investment_income_updates_ledger_only(self):
        acc=FakeAccount(snapshot=D('1300'), anchor=D('1000'));db=FakeDB(acc)
        create(Data(type='INCOME',amount=D('400')),current_user=user,db=db)
        self.assertEqual(acc.ledger,D('1400'))
        self.assertEqual(acc.current_market_value,D('1300'))
        self.assertEqual(valuation.effective_investment_value(acc.ledger,acc.current_market_value,acc.valuation_ledger_anchor),D('1700'))
    def test_expense_underflow_rolls_back(self):
        acc=FakeAccount(ledger='70');db=FakeDB(acc)
        with self.assertRaises(HTTPException) as ex:
            create(Data(amount=D('90')),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,409)
        self.assertEqual(acc.ledger,D('70'))
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')
    def test_explicit_zero_underflow_rolls_back(self):
        acc=FakeAccount(snapshot=D('0'),anchor=D('1000'));db=FakeDB(acc)
        with self.assertRaises(HTTPException):
            create(Data(amount=D('1')),current_user=user,db=db)
        self.assertEqual(acc.ledger,D('1000'))
        self.assertEqual(db.commits,0)
    def test_bank_negative_not_subject_to_investment_guard(self):
        acc=FakeAccount(type='BANK',ledger='10');db=FakeDB(acc)
        create(Data(amount=D('50')),current_user=user,db=db)
        self.assertEqual(acc.ledger,D('-40'))
        self.assertEqual(db.commits,1)
    def test_missing_account_rejected(self):
        db=FakeDB(FakeAccount())
        with self.assertRaises(HTTPException) as ex:
            create(Data(account_id=11),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,404)
        self.assertNotIn('add',db.events)
    def test_foreign_user_account_rejected(self):
        db=FakeDB(FakeAccount(user_id=2))
        with self.assertRaises(HTTPException) as ex:
            create(Data(),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,404)
        self.assertNotIn('add',db.events)
    def test_goal_reserve_rejected(self):
        db=FakeDB(FakeAccount(type='GOAL_RESERVE'))
        with self.assertRaises(HTTPException) as ex:
            create(Data(),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,409)
        self.assertNotIn('add',db.events)
    def test_transfer_uses_other_route(self):
        db=FakeDB(FakeAccount())
        with self.assertRaises(HTTPException) as ex:
            create(Data(type='TRANSFER'),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,400)
        self.assertEqual(db.events,[])
    def test_income_period_kept(self):
        db=FakeDB(FakeAccount())
        tx=create(Data(type='INCOME',income_period='2026-12'),current_user=user,db=db)
        self.assertEqual(tx.income_period,'2026-12')
    def test_nonincome_period_rejected(self):
        db=FakeDB(FakeAccount())
        with self.assertRaises(HTTPException) as ex:
            create(Data(income_period='2026-12'),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,422)
        self.assertNotIn('add',db.events)
    def test_duplicate_source_id_rejected_before_write(self):
        db=FakeDB(FakeAccount(),duplicate=True)
        with self.assertRaises(HTTPException) as ex:
            create(Data(source_id='some-id'),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,409)
        self.assertNotIn('add',db.events)
    def test_integrity_error_rollback_409(self):
        db=FakeDB(FakeAccount(),error_at='integrity')
        with self.assertRaises(HTTPException) as ex:
            create(Data(source_id='some-id'),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,409)
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')
    def test_flush_error_rollback(self):
        db=FakeDB(FakeAccount(),error_at='flush')
        with self.assertRaises(RuntimeError):
            create(Data(),current_user=user,db=db)
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')
    def test_unexpected_commit_error_rollback(self):
        db=FakeDB(FakeAccount(),error_at='commit')
        with self.assertRaises(RuntimeError):
            create(Data(),current_user=user,db=db)
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')
    def test_optional_no_account_still_allowed(self):
        db=FakeDB()
        tx=create(Data(account_id=None),current_user=user,db=db)
        self.assertIsNone(tx.account_id)
        self.assertEqual(db.commits,1)
        self.assertEqual(db.events,['lock','add','flush','validate','commit','refresh'])
    def test_category_guard_intact(self):
        db=FakeDB(FakeAccount())
        with self.assertRaises(HTTPException) as ex:
            create(Data(category_id=999),current_user=user,db=db)
        self.assertEqual(ex.exception.status_code,404)
        self.assertNotIn('add',db.events)

if __name__ == '__main__':
    unittest.main()
