"""PH2.5 webhook ingestion atomicity, isolated live-function extraction.

Only imports built-ins and AST-extracted webhook route. No Postgres/Neon/network.
"""
import ast
import copy
import hmac
import unittest
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

D = Decimal
class HTTPException(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        super().__init__(detail)
class IntegrityError(Exception): pass

class Field:
    def __init__(self,name): self.name=name
    def __eq__(self,value): return ('eq',self.name,value)
    def ilike(self,value): return ('ilike',self.name,value)
class Account:
    user_id=Field('user_id'); type=Field('type'); name=Field('name')
class Transaction:
    id=Field('id'); user_id=Field('user_id'); source_id=Field('source_id')
    def __init__(self,**kw): self.__dict__.update(kw)
class AccountType:
    BANK='BANK'; INVESTMENT='INVESTMENT'
class TType(str):
    @property
    def value(self): return str(self)
class TransactionType:
    INCOME=TType('INCOME'); EXPENSE=TType('EXPENSE')
class Query:
    def __init__(self,db,entity): self.db=db; self.entity=entity; self.clauses=[]
    def filter(self,*clauses): self.clauses.extend(clauses); return self
    def first(self):
        if self.entity is Account:
            if any(c[0]=='ilike' for c in self.clauses):
                return next((a for a in self.db.accounts if a.user_id==self.db.user_id and a.type==AccountType.BANK and 'axis' in a.name.lower()),None)
            return next((a for a in self.db.accounts if a.user_id==self.db.user_id and a.type==AccountType.BANK),None)
        if self.entity is Transaction.id:
            sid=next((c[2] for c in self.clauses if c[0]=='eq' and c[1]=='source_id'),None)
            return (1,) if sid in self.db.existing else None
        raise AssertionError(f'Unexpected query {self.entity}')
class Savepoint:
    def __init__(self,db): self.db=db
    def __enter__(self):
        self.db.events.append('savepoint')
        self.prev=len(self.db.pending)
        self.balances=[a.ledger for a in self.db.accounts]
        return self
    def __exit__(self,typ,exc,tb):
        if typ:
            del self.db.pending[self.prev:]
            for a,amount in zip(self.db.accounts,self.balances): a.ledger=amount
            self.db.events.append('savepoint_rollback')
        return False
class FakeDB:
    def __init__(self,accounts=None,*,token='actual-private-token',fail_source=None,fail_commit=False,
                 fail_flush=False,fail_guard=False,fail_category=False,ignored=(),existing=(),lock_kind=None):
        self.accounts=accounts if accounts is not None else [bank()]
        self.user_id=1
        self.pending=[]; self.events=[]; self.commits=0; self.rollbacks=0
        self.initial=[a.ledger for a in self.accounts]
        self.token=token; self.fail_source=fail_source; self.fail_commit=fail_commit
        self.fail_flush=fail_flush; self.fail_guard=fail_guard
        self.fail_category=fail_category; self.ignored=set(ignored); self.existing=set(existing)
        self.lock_kind=lock_kind
        self.categories=[]
    def query(self,entity): return Query(self,entity)
    def begin_nested(self): return Savepoint(self)
    def add(self,tx): self.pending.append(tx); self.events.append('add')
    def flush(self):
        self.events.append('flush')
        tx=self.pending[-1]
        if tx.source_id==self.fail_source: raise IntegrityError('duplicate')
        if self.fail_flush: raise RuntimeError('flush unavailable')
        target=next(a for a in self.accounts if a.id==tx.account_id)
        target.ledger += D(tx.amount) * (1 if tx.type==TransactionType.INCOME else -1)
    def commit(self):
        self.events.append('commit')
        if self.fail_commit: raise RuntimeError('commit unavailable')
        self.commits+=1
    def rollback(self):
        self.events.append('rollback'); self.rollbacks+=1
        self.pending=[]
        for a,value in zip(self.accounts,self.initial): a.ledger=value

def bank(id=4,name='Axis Bank',owner=1,kind=AccountType.BANK,ledger='1000'):
    return SimpleNamespace(id=id,name=name,user_id=owner,type=kind,ledger=D(ledger))
def lock_owned_accounts(db,user_id,ids):
    db.events.append('lock')
    db.locked_ids=sorted(set(ids))
    if len(db.locked_ids)!=1: raise AssertionError('Expected one BANK row lock')
    target=next((a for a in db.accounts if a.id==db.locked_ids[0] and a.user_id==user_id),None)
    if not target: raise HTTPException(404,'Account missing')
    if db.lock_kind is not None: target.type=db.lock_kind
    return [target]
def validate_postwrite_investments(db,user_id,locked_accounts):
    db.events.append('validate')
    if db.fail_guard: raise RuntimeError('Guard unavailable')
    for a in locked_accounts:
        if a.type==AccountType.INVESTMENT and a.ledger<0: raise HTTPException(409,'Investment underflow')
def _resolve_sync_user(db): return SimpleNamespace(id=db.user_id)
def _positive_amount(value):
    try:
        amount=D(str(value)).quantize(D('0.01'))
        return amount if amount.is_finite() and amount>0 else None
    except (InvalidOperation,ValueError,TypeError): return None
def _is_ignored(db,user_id,sid): return sid in db.ignored
def resolve_category(desc,kind,user_id,db):
    db.events.append('category')
    if db.fail_category: raise RuntimeError('Category resolver failure')
    db.categories.append((desc,kind,user_id))
    return 8
settings=SimpleNamespace(SYNC_TOKEN='actual-private-token')
INSECURE_SYNC_TOKEN='public-placeholder'
source=Path('modules/finance/imports.py').read_text(encoding='utf-8-sig')
tree=ast.parse(source)
items=[copy.deepcopy(x) for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='webhook_ingest_transactions']
if len(items)!=1: raise AssertionError('Expected one webhook function')
items[0].decorator_list=[]
mod=ast.Module(body=items,type_ignores=[]); ast.fix_missing_locations(mod)
namespace={
    'WebhookPayload':object,'Optional':Optional,'Header':lambda value=None:value,
    'Session':object,'Depends':lambda func:None,'get_db':lambda:None,
    'HTTPException':HTTPException,'IntegrityError':IntegrityError,
    'settings':settings,'INSECURE_SYNC_TOKEN':INSECURE_SYNC_TOKEN,'hmac':hmac,
    '_resolve_sync_user':_resolve_sync_user,'Account':Account,'AccountType':AccountType,
    'Transaction':Transaction,'TransactionType':TransactionType,'_is_ignored':_is_ignored,
    '_positive_amount':_positive_amount,'datetime':datetime,'resolve_category':resolve_category,
    'lock_owned_accounts':lock_owned_accounts,
    'validate_postwrite_investments':validate_postwrite_investments,
}
exec(compile(mod,'modules/finance/imports.py','exec'),namespace)
webhook=namespace['webhook_ingest_transactions']
def item(sid='a',kind='INCOME',amount=50,date='2026-11-01',description='Salary'):
    return SimpleNamespace(source_id=sid,type=kind,amount=amount,date=date,description=description)
def call(db,*items,token='actual-private-token'):
    return webhook(SimpleNamespace(transactions=list(items)),x_sync_token=token,db=db)
class WebhookAtomicity(unittest.TestCase):
    def test_single_income(self):
        db=FakeDB(); result=call(db,item())
        self.assertEqual((result['imported'],db.accounts[0].ledger,db.commits),(1,D(1050),1))
        self.assertLess(db.events.index('lock'),db.events.index('savepoint'))
        self.assertLess(db.events.index('flush'),db.events.index('validate'))
    def test_single_expense(self):
        db=FakeDB(); result=call(db,item(kind='EXPENSE',amount=100))
        self.assertEqual((result['imported'],db.accounts[0].ledger),(1,D(900)))
    def test_prefer_axis_bank(self):
        db=FakeDB([bank(7,name='Other Bank'),bank(8,name='Axis Bank')])
        call(db,item()); self.assertEqual(db.locked_ids,[8]); self.assertEqual(db.pending[0].account_id,8)
    def test_fallback_bank(self):
        db=FakeDB([bank(7,name='Other Bank')]); call(db,item())
        self.assertEqual(db.locked_ids,[7])
    def test_cross_user_bank_skipped_at_selection(self):
        db=FakeDB([bank(7,owner=2),bank(8,owner=1,name='Other Bank')]); call(db,item())
        self.assertEqual(db.locked_ids,[8])
    def test_missing_bank(self):
        db=FakeDB([bank(kind=AccountType.INVESTMENT)])
        with self.assertRaises(HTTPException) as err: call(db,item())
        self.assertEqual(err.exception.status_code,400); self.assertNotIn('lock',db.events)
    def test_change_from_bank_after_lock_rejected(self):
        db=FakeDB(lock_kind=AccountType.INVESTMENT)
        with self.assertRaises(HTTPException) as err: call(db,item())
        self.assertEqual(err.exception.status_code,409)
        self.assertEqual(len(db.pending),0)
    def test_placeholder_token_rejected(self):
        old=settings.SYNC_TOKEN; settings.SYNC_TOKEN=INSECURE_SYNC_TOKEN
        try:
            with self.assertRaises(HTTPException) as err: call(FakeDB(),item())
            self.assertEqual(err.exception.status_code,503)
        finally: settings.SYNC_TOKEN=old
    def test_bad_token(self):
        db=FakeDB()
        with self.assertRaises(HTTPException) as err: call(db,item(),token='bad')
        self.assertEqual(err.exception.status_code,401)
        self.assertNotIn('lock',db.events)
    def test_missing_token(self):
        with self.assertRaises(HTTPException) as err: call(FakeDB(),item(),token=None)
        self.assertEqual(err.exception.status_code,401)
    def test_duplicate_in_batch(self):
        db=FakeDB(); self.assertEqual(call(db,item('x'),item('x'))['imported'],1)
        self.assertEqual(len(db.pending),1)
    def test_duplicate_in_db(self):
        db=FakeDB(existing={'x'}); self.assertEqual(call(db,item('x'))['imported'],0)
    def test_ignored_source(self):
        db=FakeDB(ignored={'x'}); self.assertEqual(call(db,item('x'))['imported'],0)
    def test_duplicate_savepoint_integrity_error(self):
        db=FakeDB(fail_source='x'); self.assertEqual(call(db,item('x'),item('y'))['imported'],1)
        self.assertEqual([tx.source_id for tx in db.pending],['y'])
    def test_duplicate_id_not_seen_when_savepoint_skips(self):
        db=FakeDB(fail_source='x'); self.assertEqual(call(db,item('x'),item('x'))['imported'],0)
        self.assertEqual(db.events.count('savepoint'),2)
    def test_invalid_amount_skip(self):
        db=FakeDB(); self.assertEqual(call(db,item('x',amount=0),item('y'))['imported'],1)
    def test_invalid_date_skip(self):
        db=FakeDB(); self.assertEqual(call(db,item('x',date='2026-99-01'),item('y'))['imported'],1)
    def test_empty_batch(self):
        db=FakeDB(); self.assertEqual(call(db)['imported'],0)
        self.assertEqual((db.events.count('lock'),db.commits),(1,1))
    def test_unexpected_flush_failure_rolls_back_batch(self):
        db=FakeDB(fail_flush=True)
        with self.assertRaises(RuntimeError): call(db,item())
        self.assertEqual((db.rollbacks,len(db.pending),db.accounts[0].ledger),(1,0,D(1000)))
    def test_unexpected_guard_failure_rolls_back_batch(self):
        db=FakeDB(fail_guard=True)
        with self.assertRaises(RuntimeError): call(db,item())
        self.assertEqual(db.rollbacks,1)
    def test_commit_failure_rolls_back(self):
        db=FakeDB(fail_commit=True)
        with self.assertRaises(RuntimeError): call(db,item())
        self.assertEqual((db.rollbacks,len(db.pending)),(1,0))
    def test_category_failure_rolls_back(self):
        db=FakeDB(fail_category=True)
        with self.assertRaises(RuntimeError): call(db,item())
        self.assertEqual(db.rollbacks,1)
    def test_category_resolver_user_and_income_type(self):
        db=FakeDB(); call(db,item())
        self.assertEqual(db.categories,[('Salary','INCOME',1)])
    def test_category_resolver_expense(self):
        db=FakeDB(); call(db,item(kind='EXPENSE'))
        self.assertEqual(db.categories,[('Salary','EXPENSE',1)])
    def test_negative_bank_balance_not_blocked_as_investment(self):
        db=FakeDB([bank(ledger='0')]); result=call(db,item(kind='EXPENSE',amount=99))
        self.assertEqual(result['imported'],1); self.assertEqual(db.accounts[0].ledger,D(-99))
    def test_no_market_value_mutation(self):
        db=FakeDB(); db.accounts[0].current_market_value=D(10000)
        call(db,item()); self.assertEqual(db.accounts[0].current_market_value,D(10000))
    def test_flush_and_guard_one_per_accepted_row(self):
        db=FakeDB(); call(db,item('x'),item('y'))
        self.assertEqual(db.events.count('flush'),2)
        self.assertEqual(db.events.count('validate'),2)
    def test_external_lock_and_validation_order(self):
        db=FakeDB(); call(db,item('x'))
        seq=['lock','savepoint','add','flush','validate','commit']
        for a,b in zip(seq,seq[1:]): self.assertLess(db.events.index(a),db.events.index(b))
    def test_no_transaction_if_token_invalid(self):
        db=FakeDB()
        with self.assertRaises(HTTPException): call(db,item(),token='not-authorized')
        self.assertEqual(db.pending,[])
    def test_row_duplicate_count_has_no_success(self):
        db=FakeDB(fail_source='first'); result=call(db,item('first'))
        self.assertEqual(result,{'status':'success','imported':0})
