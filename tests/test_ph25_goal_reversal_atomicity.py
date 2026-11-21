"""Isolated PH2.5 goal reversal tests; no DB, FastAPI import or network access."""
import ast
import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROUTER=Path(__file__).resolve().parents[1]/'modules/finance/router.py'
CODE=ROUTER.read_text(encoding='utf-8-sig')
NODE=next(n for n in ast.parse(CODE).body if isinstance(n, ast.FunctionDef) and n.name=='reverse_goal_contribution')
SRC=ast.get_source_segment(CODE,NODE)

class HTTPError(Exception):
    def __init__(self,code,detail=None):
        self.status_code=code;self.detail=detail
        super().__init__(detail)
class Field:
    def __init__(self,name): self.name=name
    def __eq__(self,value): return ('eq',self.name,value)
class Model:
    def __init__(self,**fields): self.__dict__.update(fields)
class Goal(Model):
    id=Field('goal.id');user_id=Field('goal.user_id')
class Contribution(Model):
    id=Field('contribution.id');goal_id=Field('contribution.goal_id')
class Tx(Model):
    id=Field('tx.id');user_id=Field('tx.user_id')
class Ac(Model):
    id=Field('account.id');user_id=Field('account.user_id');type=Field('account.type')
class AT:
    GOAL_RESERVE='GOAL_RESERVE';INVESTMENT='INVESTMENT';BANK='BANK';CREDIT_CARD='CREDIT_CARD'
class TT:
    TRANSFER='TRANSFER';EXPENSE='EXPENSE'
class Query:
    def __init__(self,db,cls):self.db=db;self.cls=cls
    def filter(self,*args):return self
    def with_for_update(self):
        self.db.events.append(('lock_goal' if self.cls is Goal else 'lock_contribution' if self.cls is Contribution else 'UNEXPECTED_SINGLE_ACCOUNT_LOCK',))
        if self.cls is Ac: raise RuntimeError('out-of-order reserve account lock')
        return self
    def first(self):
        if self.cls is Goal:return self.db.goal
        if self.cls is Contribution:return self.db.contribution
        if self.cls is Tx:return self.db.original
        if self.cls is Ac:return self.db.reserve
        raise RuntimeError('Unexpected ORM query')
class DB:
    def __init__(self):
        self.events=[];self.fail=None;self.new_id=1001
        self.goal=Goal(id=8,user_id=7,name='Emergency',saved_amount=Decimal('125'),closed_at=None)
        self.contribution=Contribution(id=91,goal_id=8,account_id=20,amount=Decimal('125'),date=date(2020,1,1),transaction_id=100,reversal_transaction_id=None,reversed_at=None)
        self.source=Ac(id=20,user_id=7,type=AT.INVESTMENT)
        self.reserve=Ac(id=4,user_id=7,type=AT.GOAL_RESERVE)
        self.original=Tx(id=100,user_id=7,type=TT.TRANSFER,account_id=20,from_account_id=20,to_account_id=4,amount=Decimal('125'),date=date(2020,1,1))
        self.reserve_balance=Decimal('200');self.written=[]
    def query(self, cls):return Query(self,cls)
    def add(self,entity):self.events.append(('add',));self.written.append(entity)
    def flush(self):
        self.events.append(('flush',))
        if self.fail=='flush':raise RuntimeError('flush failed')
        for x in self.written:
            if not getattr(x,'id',None):x.id=self.new_id;self.new_id+=1
    def commit(self):
        self.events.append(('commit',))
        if self.fail=='commit':raise RuntimeError('commit failed')
    def rollback(self):self.events.append(('rollback',))
    def refresh(self,item):self.events.append(('refresh',))
class Reverse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx={'__name__':'ph25_reversal_tests','GoalContributionReversalCreate':object,
                 'User':object,'Session':object,'Depends':lambda _:None,
                 'get_current_user':lambda:None,'get_db':lambda:None,
                 'FinancialGoal':Goal,'GoalContribution':Contribution,'Account':Ac,'Transaction':Tx,
                 'AccountType':AT,'TransactionType':TT,'Decimal':Decimal,'date':date,'datetime':datetime,
                 'HTTPException':HTTPError}
        isolated=copy.deepcopy(NODE)
        isolated.decorator_list=[]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated],type_ignores=[])),
                     '<isolated_goal_reversal>','exec'),cls.ctx)
    def make(self):
        db=DB();self.event_ctx=db
        def owned(_,user,account_id,*args):
            db.events.append(('read_source',))
            if user!=7 or account_id!=db.source.id:raise HTTPError(404,'Source not found')
            return db.source
        def locked(_,user,ids):
            if user!=7:raise AssertionError('Wrong user')
            ids=tuple(sorted(set(ids)))
            db.events.append(('lock_accounts',ids))
            if db.fail=='lock':raise HTTPError(404,'Account missing')
            if db.fail=='reserve_changes':db.reserve.type=AT.BANK
            if db.fail=='source_changes':db.source.type=AT.GOAL_RESERVE
            if db.fail=='reserve_disappears':return [db.source]
            return sorted([x for x in [db.source,db.reserve] if x.id in ids],key=lambda x:x.id)
        def balances(_,user):
            if user!=7:raise AssertionError('Wrong user')
            db.events.append(('balance_read',))
            return [NS(id=db.reserve.id,current_balance=db.reserve_balance)]
        def validation(_,user,accounts):
            db.events.append(('validate',tuple(sorted(x.id for x in accounts))))
            if user!=7:raise AssertionError('Wrong user')
            if db.fail=='guard':raise HTTPError(409,'Invalid effective investment value')
        self.ctx.update(get_owned_account=owned,lock_owned_accounts=locked,
                        FinanceService=NS(calculate_account_balances=balances),
                        validate_postwrite_investments=validation)
        return db,NS(date=date.today(),note='Release')
    def reverse(self,db,data):return self.ctx['reverse_goal_contribution'](8,91,data,NS(id=7),db)
    def event_names(self,db):return [e[0] for e in db.events]
    def order(self,db):
        events=self.event_names(db)
        names=['lock_goal','lock_contribution','lock_accounts','balance_read','add','flush','validate','commit']
        self.assertEqual([events.index(x) for x in names],sorted(events.index(x) for x in names))
    def test_source_keeps_goal_and_contribution_locks(self):self.assertEqual(SRC.count('.with_for_update()'),2)
    def test_source_shared_account_lock(self):self.assertIn('lock_owned_accounts(',SRC)
    def test_source_validate_postwrite(self):self.assertIn('validate_postwrite_investments(',SRC)
    def test_source_rollback(self):self.assertIn('db.rollback()',SRC)
    def test_source_single_commit(self):self.assertEqual(SRC.count('db.commit()'),1)
    def test_source_no_market_value_mutation(self):self.assertNotIn('current_market_value =',SRC)
    def test_source_preserves_legacy_guard(self):self.assertIn('if contribution.transaction_id is None:',SRC)
    def test_source_preserves_reversal_link(self):self.assertIn('contribution.reversal_transaction_id = reversal_tx.id',SRC)
    def test_source_preserves_reserve_balance_guard(self):self.assertIn('reserve_balance is None or reserve_balance < amount',SRC)
    def test_success_existing_reserve(self):
        db,data=self.make();r=self.reverse(db,data)
        self.assertIs(r,db.contribution)
        self.assertEqual(db.goal.saved_amount,Decimal('0'))
        self.assertEqual(r.reversal_transaction_id,1001)
        self.assertIsNotNone(r.reversed_at)
        self.assertEqual(len(db.written),1)
        self.order(db)
        self.assertIn(('lock_accounts',(4,20)),db.events)
    def test_reverse_transfer_direction(self):
        db,data=self.make();self.reverse(db,data)
        t=db.written[0]
        self.assertEqual((t.from_account_id,t.to_account_id,t.account_id,t.type),(4,20,4,TT.TRANSFER))
    def test_investment_source_credited_without_snapshot_shift(self):
        db,data=self.make();self.reverse(db,data)
        self.assertEqual(db.source.type,AT.INVESTMENT)
        self.assertIn(('validate',(4,20)),db.events)
    def test_noninvestment_source_reverses(self):
        db,data=self.make();db.source.type=AT.BANK
        self.reverse(db,data)
        self.assertIn(('commit',),db.events)
    def test_goal_closed_no_write(self):
        db,data=self.make();db.goal.closed_at=datetime.utcnow()
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('add',self.event_names(db))
    def test_duplicate_reversal_rejected(self):
        db,data=self.make();db.contribution.reversal_transaction_id=900
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('lock_accounts',self.event_names(db))
    def test_legacy_unlinked_rejected(self):
        db,data=self.make();db.contribution.transaction_id=None
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('lock_accounts',self.event_names(db))
    def test_incorrect_original_transfer_rejected(self):
        db,data=self.make();db.original.amount=Decimal('127')
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('add',self.event_names(db))
    def test_reserve_missing_rejected(self):
        db,data=self.make();db.reserve=None
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('lock_accounts',self.event_names(db))
    def test_reserve_insufficient_rejected(self):
        db,data=self.make();db.reserve_balance=Decimal('100')
        with self.assertRaises(HTTPError) as err:self.reverse(db,data)
        self.assertEqual(err.exception.status_code,409)
        self.assertNotIn('add',self.event_names(db))
    def test_goal_saved_amount_insufficient_rejected(self):
        db,data=self.make();db.goal.saved_amount=Decimal('120')
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('add',self.event_names(db))
    def test_rejected_reserve_type_after_lock(self):
        db,data=self.make();db.fail='reserve_changes'
        with self.assertRaises(HTTPError) as err:self.reverse(db,data)
        self.assertEqual(err.exception.status_code,409)
        self.assertNotIn('add',self.event_names(db))
    def test_rejected_source_type_after_lock(self):
        db,data=self.make();db.fail='source_changes'
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('add',self.event_names(db))
    def test_missing_locked_participant_rejected(self):
        db,data=self.make();db.fail='reserve_disappears'
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('balance_read',self.event_names(db))
    def test_lock_failure_prevents_write(self):
        db,data=self.make();db.fail='lock'
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('add',self.event_names(db))
    def test_flush_error_rolls_back(self):
        db,data=self.make();db.fail='flush'
        with self.assertRaises(RuntimeError):self.reverse(db,data)
        self.assertIn(('rollback',),db.events)
        self.assertNotIn(('commit',),db.events)
    def test_guard_error_rolls_back_without_commit(self):
        db,data=self.make();db.fail='guard'
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertIn(('rollback',),db.events)
        self.assertNotIn(('commit',),db.events)
    def test_commit_error_rolls_back(self):
        db,data=self.make();db.fail='commit'
        with self.assertRaises(RuntimeError):self.reverse(db,data)
        self.assertIn(('rollback',),db.events)
    def test_future_reversal_rejected(self):
        db,data=self.make();data.date=date.today()+timedelta(days=1)
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('add',self.event_names(db))
    def test_reversal_before_funding_rejected(self):
        db,data=self.make();db.contribution.date=date.today()
        data.date=date.today()-timedelta(days=1)
        with self.assertRaises(HTTPError):self.reverse(db,data)
        self.assertNotIn('add',self.event_names(db))
    def test_reversal_description_and_one_commit(self):
        db,data=self.make();data.note='release funds';self.reverse(db,data)
        self.assertTrue(db.written[0].description.startswith('Goal reversal #91'))
        self.assertEqual(db.events.count(('commit',)),1)

if __name__=='__main__': unittest.main()
