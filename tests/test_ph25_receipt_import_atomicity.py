"""PH2.5 Gmail receipt batch atomicity (isolated real route execution).

No application imports, network, or external database connections.
"""
import ast
import copy
import unittest
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional

D = Decimal

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)

class IntegrityError(Exception):
    pass

class Field:
    def __init__(self, name): self.name = name
    def __eq__(self, other): return ('eq',self.name,other)
    def in_(self, values): return ('in',self.name,tuple(values))

class Account:
    id=Field('id')
    user_id=Field('user_id')

class Category:
    id=Field('id')
    user_id=Field('user_id')
    is_system=Field('is_system')

class Transaction:
    id=Field('id')
    user_id=Field('user_id')
    source_id=Field('source_id')
    def __init__(self, **fields): self.__dict__.update(fields)

class AccountType:
    GOAL_RESERVE='GOAL_RESERVE'
    INVESTMENT='INVESTMENT'
    BANK='BANK'

class TransactionType:
    INCOME='INCOME'
    EXPENSE='EXPENSE'

class Query:
    def __init__(self, db, entity): self.db=db; self.entity=entity; self.filters=[]
    def filter(self,*filters): self.filters.extend(filters); return self
    def all(self):
        if self.entity is Account:
            return [a for a in self.db.accounts.values() if a.user_id==self.db.user_id and (
                not any(f[0]=='in' and f[1]=='id' and a.id not in f[2] for f in self.filters))]
        if self.entity is Category.id: return [(x,) for x in self.db.categories]
        raise AssertionError('Unexpected query all: '+str(self.entity))
    def first(self):
        if self.entity is Transaction.id:
            sid=next((f[2] for f in self.filters if f[0]=='eq' and f[1]=='source_id'),None)
            if sid is not None and sid in self.db.existing_sources: return (1,)
            return None
        raise AssertionError('Unexpected query first: '+str(self.entity))

class Savepoint:
    def __init__(self, db): self.db=db
    def __enter__(self):
        self.db.events.append('savepoint')
        self.old_balances={k:v.ledger for k,v in self.db.accounts.items()}
        self.old_len=len(self.db.pending)
        return self
    def __exit__(self, typ, value, tb):
        if typ is not None:
            for k,value in self.old_balances.items(): self.db.accounts[k].ledger=value
            del self.db.pending[self.old_len:]
            self.db.events.append('savepoint_rollback')
        return False

class FakeDB:
    def __init__(self, accounts, *, fail_commit=False, fail_source=None, guard_error=False, categories=()):
        self.accounts={a.id:a for a in accounts}
        self.initial={a.id:a.ledger for a in accounts}
        self.user_id=1
        self.pending=[]
        self.existing_sources=set()
        self.ignored_sources=set()
        self.events=[]
        self.commits=0
        self.fail_commit=fail_commit
        self.fail_source=fail_source
        self.guard_error=guard_error
        self.categories=set(categories)
    def query(self, entity): return Query(self, entity)
    def begin_nested(self): return Savepoint(self)
    def add(self, tx): self.pending.append(tx); self.events.append('add')
    def flush(self):
        self.events.append('flush')
        tx=self.pending[-1]
        if tx.source_id==self.fail_source: raise IntegrityError('duplicate')
        acc=self.accounts[tx.account_id]
        acc.ledger += D(tx.amount) * (1 if tx.type==TransactionType.INCOME else -1)
    def commit(self):
        self.events.append('commit')
        if self.fail_commit: raise RuntimeError('Commit failed')
        self.commits+=1
    def rollback(self):
        self.events.append('rollback')
        for k,v in self.initial.items(): self.accounts[k].ledger=v
        self.pending=[]

def lock_owned_accounts(db,user_id,ids):
    ids=list(ids)
    db.events.append('lock')
    db.lock_ids=sorted(set(ids))
    for account_id in db.lock_ids:
        acc=db.accounts.get(account_id)
        if not acc or acc.user_id!=user_id: raise HTTPException(404,'Invalid account')
    return [db.accounts[k] for k in db.lock_ids]

def validate_postwrite_investments(db,user_id,accounts):
    db.events.append('validate')
    if db.guard_error: raise RuntimeError('Simulated validation failure')
    for a in accounts:
        if a.type != AccountType.INVESTMENT: continue
        if a.valuation is None:
            value=a.ledger
        else:
            value=a.valuation + a.ledger - a.anchor
        if value < 0: raise HTTPException(409,'Negative investment valuation')

def _positive_amount(value):
    try:
        result=D(str(value)).quantize(D('0.01'))
        return result if result.is_finite() and result>0 else None
    except (InvalidOperation, ValueError): return None

def _is_ignored(db,user_id,sid): return sid in db.ignored_sources

def or_(*conditions): return ('or',conditions)

source=Path('modules/finance/imports.py').read_text(encoding='utf-8-sig')
tree=ast.parse(source)
functions=[copy.deepcopy(n) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='commit_receipts']
if len(functions)!=1: raise AssertionError('Unexpected receipt route')
fn=functions[0]
fn.decorator_list=[]
module=ast.Module(body=[fn],type_ignores=[])
ast.fix_missing_locations(module)
ns={
    'CommitReceiptsRequest':object, 'Session':object, 'Depends':lambda f:None,
    'get_db':lambda:None,'get_current_user':lambda:None,'User':object,
    'Dict':Dict,'Optional':Optional, 'Account':Account,'Category':Category,
    'Transaction':Transaction,'TransactionType':TransactionType,'AccountType':AccountType,
    'HTTPException':HTTPException,'IntegrityError':IntegrityError,'datetime':datetime,
    '_positive_amount':_positive_amount,'_is_ignored':_is_ignored,'or_':or_,
    'lock_owned_accounts':lock_owned_accounts,
    'validate_postwrite_investments':validate_postwrite_investments,
}
exec(compile(module,'modules/finance/imports.py','exec'),ns)
route=ns['commit_receipts']
user=SimpleNamespace(id=1)

def account(id=10, kind=AccountType.INVESTMENT, balance=1000, owner=1, valuation=None, anchor=None):
    return SimpleNamespace(id=id,type=kind,ledger=D(balance),user_id=owner,
        valuation=D(valuation) if valuation is not None else None,
        anchor=D(anchor) if anchor is not None else None)

def item(*, sid='x1', account_id=10, amount='100', kind='EXPENSE',date='2026-11-01',desc='Test',category_id=None):
    return SimpleNamespace(source_id=sid,account_id=account_id,amount=amount,type=kind,
        date=date,description=desc,category_id=category_id)

def run(db,*items):
    return route(SimpleNamespace(transactions=list(items)), db=db,current_user=user)

class ReceiptImportAtomicity(unittest.TestCase):
    def test_expense_ledger_and_commit(self):
        a=account(); db=FakeDB([a]); result=run(db,item())
        self.assertEqual((a.ledger,result['committed_count'],db.commits),(D('900'),1,1))
        self.assertLess(db.events.index('lock'),db.events.index('savepoint'))
        self.assertLess(db.events.index('flush'),db.events.index('validate'))
        self.assertLess(db.events.index('validate'),db.events.index('commit'))
    def test_income_investment_positive(self):
        a=account(valuation='1200',anchor='1000'); db=FakeDB([a]); run(db,item(kind='INCOME'))
        self.assertEqual(a.ledger,D('1100')); self.assertEqual(a.valuation,D('1200'))
    def test_explicit_zero_negative_skipped(self):
        a=account(valuation='0',anchor='1000'); db=FakeDB([a]); result=run(db,item())
        self.assertEqual((a.ledger,result['committed_count'],result['skipped_count']),(D('1000'),0,1))
    def test_mixed_batch_partial_success(self):
        a=account(balance='100'); db=FakeDB([a]); result=run(db,item(sid='a',amount='20'),item(sid='b',amount='500'),item(sid='c',amount='30'))
        self.assertEqual((result['committed_count'],result['skipped_count'],a.ledger),(2,1,D('50')))
    def test_duplicate_in_request_skipped(self):
        db=FakeDB([account()]); result=run(db,item(),item())
        self.assertEqual((result['committed_count'],result['skipped_count']),(1,1))
    def test_duplicate_in_db_skipped(self):
        db=FakeDB([account()]); db.existing_sources.add('x1'); result=run(db,item())
        self.assertEqual((result['committed_count'],result['skipped_count']),(0,1))
    def test_ignored_source_skipped(self):
        db=FakeDB([account()]); db.ignored_sources.add('x1'); result=run(db,item())
        self.assertEqual((result['committed_count'],result['skipped_count']),(0,1))
    def test_rejected_id_not_marked_seen(self):
        a=account(balance='100'); db=FakeDB([a]); result=run(db,item(amount='200'),item(amount='20'))
        self.assertEqual((result['committed_count'],result['skipped_count'],a.ledger),(1,1,D('80')))
    def test_cross_user_skipped(self):
        db=FakeDB([account(owner=2)]); result=run(db,item())
        self.assertEqual((result['committed_count'],result['skipped_count']),(0,1)); self.assertEqual(db.lock_ids,[])
    def test_goal_reserve_skipped(self):
        db=FakeDB([account(kind=AccountType.GOAL_RESERVE)]); result=run(db,item())
        self.assertEqual((result['committed_count'],result['skipped_count']),(0,1)); self.assertEqual(db.lock_ids,[])
    def test_missing_account_skipped(self):
        db=FakeDB([]); result=run(db,item())
        self.assertEqual((result['committed_count'],result['skipped_count']),(0,1))
    def test_invalid_date_skipped(self):
        db=FakeDB([account()]); result=run(db,item(date='bad-date'))
        self.assertEqual((result['committed_count'],result['skipped_count']),(0,1))
    def test_invalid_amount_skipped(self):
        db=FakeDB([account()]); result=run(db,item(amount='0'))
        self.assertEqual((result['committed_count'],result['skipped_count']),(0,1))
    def test_unique_integrity_error_is_row_local(self):
        a=account(); db=FakeDB([a],fail_source='bad'); result=run(db,item(sid='bad'),item(sid='good'))
        self.assertEqual((result['committed_count'],result['skipped_count'],a.ledger),(1,1,D('900')))
    def test_sorted_all_account_locks_before_first_write(self):
        a=account(20); b=account(10); db=FakeDB([a,b]); run(db,item(account_id=20),item(account_id=10))
        self.assertEqual(db.lock_ids,[10,20]); self.assertLess(db.events.index('lock'),db.events.index('savepoint'))
    def test_commit_error_rolls_back_batch(self):
        a=account(); db=FakeDB([a],fail_commit=True)
        with self.assertRaises(RuntimeError): run(db,item())
        self.assertEqual((a.ledger,db.commits),(D('1000'),0)); self.assertEqual(db.events[-1],'rollback')
    def test_unexpected_validation_error_aborts_batch(self):
        a=account(); db=FakeDB([a],guard_error=True)
        with self.assertRaises(RuntimeError): run(db,item())
        # Caller session teardown rolls back outer transaction; our per-row
        # savepoint must already have removed the proposed ledger delta.
        self.assertEqual(a.ledger,D('1000')); self.assertEqual(db.events[-1], 'rollback')
    def test_bank_negative_allowed(self):
        a=account(kind=AccountType.BANK,balance='10'); db=FakeDB([a]); result=run(db,item(amount='100'))
        self.assertEqual((a.ledger,result['committed_count']),(D('-90'),1))
    def test_empty_batch(self):
        db=FakeDB([]); result=run(db)
        self.assertEqual((result['committed_count'],result['skipped_count'],db.commits),(0,0,1))
    def test_categories_upfront_rejected(self):
        db=FakeDB([account()]);
        with self.assertRaises(HTTPException) as caught: run(db,item(category_id=99))
        self.assertEqual(caught.exception.status_code,404); self.assertEqual(db.commits,0)
    def test_category_valid(self):
        db=FakeDB([account()],categories=[9]); result=run(db,item(category_id=9))
        self.assertEqual(result['committed_count'],1)

if __name__=='__main__': unittest.main()
