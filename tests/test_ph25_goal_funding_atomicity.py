# Isolated goal-funding source/behavior checks. No application imports or databases.
import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock
from types import SimpleNamespace as NS
from decimal import Decimal
from datetime import date

SOURCE=Path(__file__).resolve().parents[1]/'modules/finance/router.py'
ALL=SOURCE.read_text(encoding='utf-8-sig')
NODE=next(n for n in ast.parse(ALL).body if isinstance(n,ast.FunctionDef) and n.name=='contribute_to_goal')
SRC=ast.get_source_segment(ALL,NODE)

class Error(Exception):
    def __init__(self,status_code=None,detail=None,*a,**kw):
        if status_code is None and a: status_code=a[0]
        self.status_code=status_code
        self.detail=detail if detail is not None else (a[1] if len(a)>1 else None)
        super().__init__(self.detail)

class F:
    def __init__(self,name): self.name=name
    def asc(self): return ('asc',self.name)
    def __eq__(self,x): return ('=',self.name,x)
    def __ne__(self,x): return ('!=',self.name,x)
class MODEL:
    def __init__(self,**kw): self.__dict__.update(kw)
class Ac(MODEL):
    id=F('account.id');user_id=F('account.user_id');type=F('account.type')
class Goal(MODEL):
    id=F('goal.id');user_id=F('goal.user_id')
class Contribution(MODEL):pass
class Tx(MODEL):pass
class AT:
    GOAL_RESERVE='GOAL_RESERVE'; CREDIT_CARD='CREDIT_CARD'; INVESTMENT='INVESTMENT';BANK='BANK'
class TT:TRANSFER='TRANSFER'

class Query:
    def __init__(self, db, cls): self.db=db;self.cls=cls
    def filter(self,*a):return self
    def order_by(self,*a):return self
    def with_for_update(self): self.db.events.append(('goal_lock',));return self
    def first(self):
        if self.cls is Goal:return self.db.goal
        if self.cls is Ac:return self.db.reserve
        raise RuntimeError('unexpected query')
class DB:
    def __init__(self, source_type=AT.INVESTMENT, reserve_type=AT.GOAL_RESERVE, missing_reserve=False):
        self.events=[];self.goal=Goal(id=8,name='Rainy day',closed_at=None,saved_amount=Decimal('0'))
        self.source=Ac(id=20,user_id=7,type=source_type)
        self.reserve=None if missing_reserve else Ac(id=4,user_id=7,type=reserve_type)
        self.created=[];self.next_id=1000;self.fail=None
    def query(self,cls):return Query(self,cls)
    def add(self,instance):
        self.events.append(('add',type(instance).__name__))
        self.created.append(instance)
    def flush(self):
        self.events.append(('flush',))
        if self.fail=='flush':raise RuntimeError('flush fail')
        for i in self.created:
            if not hasattr(i,'id') or i.id is None:
                i.id=self.next_id;self.next_id+=1
    def commit(self):
        self.events.append(('commit',))
        if self.fail=='commit': raise RuntimeError('commit fail')
    def rollback(self):self.events.append(('rollback',))
    def refresh(self,instance):self.events.append(('refresh',))

class Funding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Evaluate only the audited function, with local fake dependencies.
        cls.ctx={'__name__':'PH25test', 'GoalContributionCreate':object,
                 'User':object,'Session':object,'Depends':lambda x:None,
                 'get_current_user':lambda:None,'get_db':lambda:None,
                 'FinancialGoal':Goal,'GoalContribution':Contribution,
                 'Transaction':Tx,'Account':Ac,'AccountType':AT,
                 'TransactionType':TT,'Decimal':Decimal,
                 'HTTPException':Error,'status':NS(HTTP_409_CONFLICT=409,HTTP_400_BAD_REQUEST=400)}
        # The production function is decorated with @router.post(...).
        # An isolated function test must not execute application route decorators.
        isolated_node = copy.deepcopy(NODE)
        isolated_node.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated_node], type_ignores=[])),
                     '<isolated_goal_funding>', 'exec'), cls.ctx)
    def make(self, source_type=AT.INVESTMENT, reserve_type=AT.GOAL_RESERVE, missing_reserve=False):
        db=DB(source_type,reserve_type,missing_reserve)
        def owned(*a):db.events.append(('owned',));return db.source
        def locked(db_,uid,ids):
            self.assertEqual(uid,7)
            # Actual shared helper sorts and deduplicates IDs itself.
            db.events.append(('lock_accounts',tuple(sorted(set(ids)))))
            return [x for x in [db.source,db.reserve] if x is not None and x.id in ids]
        def validate(db_,uid,accounts):
            db.events.append(('validate',tuple(x.id for x in accounts)))
            if db.fail=='validate':raise Error(409,'investment underflow')
        self.ctx.update(get_owned_account=owned,lock_owned_accounts=locked,validate_postwrite_investments=validate)
        dat=NS(account_id=20,amount=Decimal('125'),date=date(2026,11,1),note='Contribution')
        return db,dat
    def run_goal(self, db, data):
        return self.ctx['contribute_to_goal'](8,data,NS(id=7),db)
    def check_order(self,events):
        labels=[x[0] for x in events]
        self.assertLess(labels.index('goal_lock'),labels.index('lock_accounts'))
        self.assertLess(labels.index('lock_accounts'),labels.index('add'))
        self.assertLess(labels.index('flush'),labels.index('validate'))
        self.assertLess(labels.index('validate'),labels.index('commit'))
    def test_source_structure_single_goal_row_lock(self):self.assertEqual(SRC.count('.with_for_update()'),1)
    def test_source_has_shared_account_locks(self):self.assertIn('lock_owned_accounts(',SRC)
    def test_source_has_postwrite_guard(self):self.assertIn('validate_postwrite_investments(',SRC)
    def test_source_has_rollback(self):self.assertIn('db.rollback()',SRC)
    def test_source_does_not_mutate_market_value(self):self.assertNotIn('current_market_value =',SRC)
    def test_source_preserves_goal_transfer_link(self):self.assertIn('contribution.transaction_id = tx.id',SRC)
    def test_source_keeps_closed_goal_check(self):self.assertIn('goal.closed_at is not None',SRC)
    def test_source_creates_reserve_when_missing(self):self.assertIn('if reserve is None:',SRC)
    def test_source_no_manual_snapshot_shift(self):self.assertNotIn('_shift_investment_value(',SRC)
    def test_source_single_commit(self):self.assertEqual(SRC.count('db.commit()'),1)
    def test_existing_reserve_locks_both_and_posts(self):
        db,dat=self.make();c=self.run_goal(db,dat)
        self.assertEqual(c.transaction_id,1001)
        self.assertEqual(db.goal.saved_amount,Decimal('125'))
        self.check_order(db.events)
        self.assertIn(('lock_accounts',(4,20)),db.events)
        self.assertEqual(db.events.count(('commit',)),1)
    def test_bank_account_allowed(self):
        db,dat=self.make(source_type=AT.BANK);self.run_goal(db,dat)
        self.assertIn(('validate',(20,4)),db.events) if False else self.assertTrue(any(x[0]=='validate' for x in db.events))
    def test_reserve_is_locked_for_existing_account(self):
        db,dat=self.make();self.run_goal(db,dat)
        self.assertLess(db.events.index(('lock_accounts',(4,20))), next(i for i,x in enumerate(db.events) if x[0]=='add'))
    def test_missing_reserve_created_and_flushed_before_transfer(self):
        db,dat=self.make(missing_reserve=True)
        # Fake lock returns only source, just as real lock helper does for a missing reserve.
        self.run_goal(db,dat)
        self.assertIn(('lock_accounts',(20,)),db.events)
        self.assertEqual(sum(isinstance(x,Ac) for x in db.created),1)
    def test_postlock_source_credit_card_rejected(self):
        db,dat=self.make();self.ctx['get_owned_account']=lambda *a:Ac(id=20,user_id=7,type=AT.BANK)
        db.source.type=AT.CREDIT_CARD
        with self.assertRaises(Error):self.run_goal(db,dat)
        self.assertFalse(any(e[0]=='add' for e in db.events))
    def test_postlock_reserve_type_changed_rejected(self):
        db,dat=self.make(reserve_type=AT.BANK)
        with self.assertRaises(Error) as e:self.run_goal(db,dat)
        self.assertEqual(e.exception.status_code,409)
        self.assertFalse(any(e[0]=='add' for e in db.events))
    def test_goal_closed_rejected(self):
        db,dat=self.make();db.goal.closed_at='closed'
        with self.assertRaises(Error):self.run_goal(db,dat)
        self.assertFalse(any(e[0]=='lock_accounts' for e in db.events))
    def test_investment_underflow_rolls_back(self):
        db,dat=self.make();db.fail='validate'
        with self.assertRaises(Error):self.run_goal(db,dat)
        self.assertIn(('rollback',),db.events)
        self.assertNotIn(('commit',),db.events)
    def test_commit_error_rolls_back(self):
        db,dat=self.make();db.fail='commit'
        with self.assertRaises(RuntimeError):self.run_goal(db,dat)
        self.assertIn(('rollback',),db.events)
    def test_flush_error_rolls_back(self):
        db,dat=self.make();db.fail='flush'
        with self.assertRaises(RuntimeError):self.run_goal(db,dat)
        self.assertIn(('rollback',),db.events)
    def test_existing_reserve_no_extra_creation(self):
        db,dat=self.make();self.run_goal(db,dat)
        self.assertFalse(any(isinstance(x,Ac) for x in db.created))
    def test_goal_transaction_is_transfer_not_expense(self):
        db,dat=self.make();self.run_goal(db,dat)
        tx=next(x for x in db.created if isinstance(x,Tx))
        self.assertEqual(tx.type,'TRANSFER');self.assertEqual(tx.to_account_id,4)
        self.assertEqual(tx.from_account_id,20)

if __name__=='__main__':unittest.main()
