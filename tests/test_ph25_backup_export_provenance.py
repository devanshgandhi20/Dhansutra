"""PH2.5 backup export: isolated real SQLAlchemy/SQLite tests, no application imports/Neon."""
import ast
import json
import unittest
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, Numeric, String, create_engine, or_
from sqlalchemy.orm import Session, declarative_base

Base=declarative_base()
class AccountType(Enum):
    INVESTMENT='INVESTMENT'; BANK='BANK'
class TransactionType(Enum):
    INCOME='INCOME'; EXPENSE='EXPENSE'; TRANSFER='TRANSFER'
class Frequency(Enum):
    MONTHLY='MONTHLY'
class Account(Base):
    __tablename__='accounts'
    id=Column(Integer,primary_key=True)
    user_id=Column(Integer,nullable=False)
    name=Column(String)
    type=Column(String)
    starting_balance=Column(Numeric(14,2))
    current_market_value=Column(Numeric(14,2),nullable=True)
    valuation_ledger_anchor=Column(Numeric(14,2),nullable=True)
    anchor_date=Column(Date,nullable=True)
class InvestmentValuationMember(Base):
    __tablename__='investment_valuation_members'
    id=Column(Integer,primary_key=True)
    account_id=Column(Integer,ForeignKey('accounts.id'))
    transaction_id=Column(Integer)
    baseline_contribution=Column(Numeric(14,2))
class Category(Base):
    __tablename__='categories'
    id=Column(Integer,primary_key=True)
    user_id=Column(Integer,nullable=True)
    name=Column(String)
    type=Column(String)
    icon=Column(String)
    is_system=Column(Boolean,default=False)
class Transaction(Base):
    __tablename__='transactions'
    id=Column(Integer,primary_key=True)
    user_id=Column(Integer)
    type=Column(String)
    amount=Column(Numeric(14,2))
    date=Column(Date)
    income_period=Column(String,nullable=True)
    description=Column(String)
    category_id=Column(Integer,nullable=True)
    account_id=Column(Integer,nullable=True)
    from_account_id=Column(Integer,nullable=True)
    to_account_id=Column(Integer,nullable=True)
    source=Column(String)
    source_id=Column(String,nullable=True)
class FinancialGoal(Base):
    __tablename__='goals'
    id=Column(Integer,primary_key=True)
    user_id=Column(Integer)
    name=Column(String)
    target_amount=Column(Numeric(14,2))
    saved_amount=Column(Numeric(14,2))
    target_date=Column(Date,nullable=True)
class GoalContribution(Base):
    __tablename__='contributions'
    id=Column(Integer,primary_key=True)
    goal_id=Column(Integer,ForeignKey('goals.id'))
    account_id=Column(Integer)
    amount=Column(Numeric(14,2))
    date=Column(Date)
    note=Column(String)
class Budget(Base):
    __tablename__='budgets'
    id=Column(Integer,primary_key=True)
    user_id=Column(Integer)
    category_id=Column(Integer)
    month=Column(String)
    limit_amount=Column(Numeric(14,2))
class RecurringTransaction(Base):
    __tablename__='recurring'
    id=Column(Integer,primary_key=True)
    user_id=Column(Integer)
    description=Column(String)
    amount=Column(Numeric(14,2))
    type=Column(String)
    account_id=Column(Integer)
    to_account_id=Column(Integer,nullable=True)
    category_id=Column(Integer,nullable=True)
    frequency=Column(String)
    next_date=Column(Date)

class Response:
    def __init__(self,content,media_type,headers):
        self.body=content.encode('utf-8')
        self.media_type=media_type
        self.headers=headers

# Real app types are enums; mock selected .type/.frequency fields to expose .value.
# SQLAlchemy columns carry string values, so adapt result objects in the fixture query.
class EnumCompatibleQuery:
    def __init__(self,db,model): self.db=db; self.model=model; self.query=db.query(model)
    def filter(self,*a): self.query=self.query.filter(*a); return self
    def join(self,*a): self.query=self.query.join(*a); return self
    def order_by(self,*a): self.query=self.query.order_by(*a); return self
    def all(self):
        values=self.query.all()
        if self.model in (Account,Transaction,Category,RecurringTransaction):
            # Don't modify live SQLAlchemy attributes; proxy enum fields only.
            vals=[]
            for obj in values:
                data={col.name:getattr(obj,col.name) for col in obj.__table__.columns}
                if 'type' in data: data['type']=SimpleNamespace(value=data['type'])
                if 'frequency' in data: data['frequency']=SimpleNamespace(value=data['frequency'])
                vals.append(SimpleNamespace(**data))
            return vals
        return values
class ExportDB:
    def __init__(self,db): self.session=db; self.queries=[]
    def query(self,model):
        self.queries.append(model.__name__)
        return EnumCompatibleQuery(self.session,model)

def load_export():
    source=Path('modules/finance/imports.py').read_text(encoding='utf-8-sig')
    root=ast.parse(source)
    nodes=[n for n in root.body if isinstance(n,ast.FunctionDef) and n.name=='export_json']
    assert len(nodes)==1
    node=nodes[0]
    node.decorator_list=[]
    node.args.defaults=[]
    for argument in node.args.args: argument.annotation = None
    node.returns = None
    code=compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),'export_json-only','exec')
    env={'Account':Account,'InvestmentValuationMember':InvestmentValuationMember,
         'Category':Category,'Transaction':Transaction,'FinancialGoal':FinancialGoal,
         'Budget':Budget,'RecurringTransaction':RecurringTransaction,'GoalContribution':GoalContribution,
         'or_':or_,'Response':Response,'json':json,'date':date,'datetime':datetime}
    exec(code,env)
    return env['export_json']

D=Decimal
class BackupProvenanceTests(unittest.TestCase):
    def setUp(self):
        engine=create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        self.engine=engine
        self.session=Session(engine)
        self.db=ExportDB(self.session)
        self.user=SimpleNamespace(id=7)
        self.export=load_export()
        self.session.add_all([
            Account(id=1,user_id=7,name='Investment Zero',type='INVESTMENT',starting_balance=D('100.00'),current_market_value=D('0.00'),valuation_ledger_anchor=D('500.00'),anchor_date=date(2026,1,1)),
            Account(id=2,user_id=7,name='Investment None',type='INVESTMENT',starting_balance=D('0.00'),current_market_value=None,valuation_ledger_anchor=None),
            Account(id=3,user_id=8,name='Other User',type='INVESTMENT',starting_balance=D('0.00'),current_market_value=D('888.88'),valuation_ledger_anchor=D('777.77')),
            Account(id=4,user_id=7,name='Investment Positive',type='INVESTMENT',starting_balance=D('500.00'),current_market_value=D('1234.56'),valuation_ledger_anchor=D('1000.05')),
            InvestmentValuationMember(account_id=1,transaction_id=81,baseline_contribution=D('-200.10')),
            InvestmentValuationMember(account_id=1,transaction_id=999999,baseline_contribution=D('1000.00')),
            InvestmentValuationMember(account_id=4,transaction_id=84,baseline_contribution=D('1.05')),
            InvestmentValuationMember(account_id=3,transaction_id=83,baseline_contribution=D('777.77')),
            Transaction(id=81,user_id=7,type='EXPENSE',amount=D('200.10'),date=date(2026,10,30),income_period=None,description='expense',account_id=1,source='MANUAL'),
            Transaction(id=82,user_id=7,type='INCOME',amount=D('45000.00'),date=date(2026,10,30),income_period='2026-11',description='early salary',account_id=2,source='MANUAL'),
            Transaction(id=83,user_id=8,type='INCOME',amount=D('5000.00'),date=date(2026,1,1),description='foreign',account_id=3,source='MANUAL'),
            Category(id=1,user_id=7,name='Salary',type='INCOME',icon='IN',is_system=False),
            Category(id=2,user_id=8,name='Private',type='EXPENSE',icon='XX',is_system=False),
            Category(id=3,user_id=None,name='System',type='EXPENSE',icon='SYS',is_system=True),
            FinancialGoal(id=1,user_id=7,name='Target',target_amount=D('500'),saved_amount=D('10')),
            FinancialGoal(id=2,user_id=8,name='Secret',target_amount=D('200'),saved_amount=D('0')),
            GoalContribution(id=1,goal_id=1,account_id=2,amount=D('10'),date=date(2026,1,1),note='yes'),
            GoalContribution(id=2,goal_id=2,account_id=3,amount=D('25'),date=date(2026,1,1),note='no'),
            Budget(id=1,user_id=7,category_id=1,month='2026-11',limit_amount=D('400')),
            RecurringTransaction(id=1,user_id=7,description='Plan',amount=D('5'),type='EXPENSE',account_id=2,frequency='MONTHLY',next_date=date(2026,12,1)),
        ])
        self.session.commit()
    def tearDown(self):
        self.session.close();self.engine.dispose()
    def archive(self,user=7):
        response=self.export(SimpleNamespace(id=user),self.db)
        return json.loads(response.body),response
    def test_explicit_zero_is_not_null(self):
        a,_=self.archive();self.assertEqual(a['accounts'][0]['current_market_value'],0.0)
    def test_null_market_value_remains_null(self):
        a,_=self.archive();self.assertIsNone(next(x for x in a['accounts'] if x['id']==2)['current_market_value'])
    def test_positive_snapshot_unchanged(self):
        a,_=self.archive();self.assertEqual(next(x for x in a['accounts'] if x['id']==4)['current_market_value'],1234.56)
    def test_exact_anchor_decimal_string(self):
        a,_=self.archive();self.assertEqual(next(x for x in a['accounts'] if x['id']==4)['valuation_ledger_anchor'],'1000.05')
    def test_null_anchor_remains_null(self):
        a,_=self.archive();self.assertIsNone(next(x for x in a['accounts'] if x['id']==2)['valuation_ledger_anchor'])
    def test_zero_market_value_anchor_preserved(self):
        a,_=self.archive();self.assertEqual(next(x for x in a['accounts'] if x['id']==1)['valuation_ledger_anchor'],'500.00')
    def test_membership_keeps_deleted_transaction_tombstone(self):
        a,_=self.archive();self.assertIn({'account_id':1,'transaction_id':999999,'baseline_contribution':'1000.00'},a['investment_valuation_members'])
    def test_signed_membership_exact(self):
        a,_=self.archive();self.assertIn({'account_id':1,'transaction_id':81,'baseline_contribution':'-200.10'},a['investment_valuation_members'])
    def test_owned_members_only(self):
        a,_=self.archive();self.assertNotIn(3,[v['account_id'] for v in a['investment_valuation_members']])
    def test_owned_accounts_only(self):
        a,_=self.archive();self.assertEqual({1,2,4},{v['id'] for v in a['accounts']})
    def test_other_user_members_not_disclosed(self):
        a,_=self.archive(user=8);self.assertEqual([83],[v['transaction_id'] for v in a['investment_valuation_members']])
    def test_other_user_account_not_disclosed(self):
        a,_=self.archive(user=8);self.assertEqual([3],[v['id'] for v in a['accounts']])
    def test_income_period_preserved(self):
        a,_=self.archive();self.assertEqual(next(v for v in a['transactions'] if v['id']==82)['income_period'],'2026-11')
    def test_source_and_ids_preserved(self):
        a,_=self.archive();self.assertEqual(next(v for v in a['transactions'] if v['id']==81)['source'],'MANUAL')
    def test_other_users_transactions_not_exported(self):
        a,_=self.archive();self.assertEqual({81,82},{x['id'] for x in a['transactions']})
    def test_contributions_owner_scoped(self):
        a,_=self.archive();self.assertEqual([1],[c['id'] for c in a['goal_contributions']])
    def test_categories_user_and_system_scoped(self):
        a,_=self.archive();self.assertEqual({1,3},{c['id'] for c in a['categories']})
    def test_legacy_fields_preserved(self):
        a,_=self.archive();self.assertIn('budgets',a);self.assertIn('recurring_transactions',a);self.assertIn('goals',a)
    def test_anchor_date_and_start_preserved(self):
        a,_=self.archive();self.assertEqual(a['accounts'][0]['starting_balance'],100.0);self.assertEqual(a['accounts'][0]['anchor_date'],'2026-01-01')
    def test_backup_schema_version(self):
        a,_=self.archive();self.assertEqual(a['backup_schema_version'],2)
    def test_json_response_contract(self):
        _,r=self.archive();self.assertEqual(r.media_type,'application/json');self.assertIn('attachment; filename=dhansutra_backup_',r.headers['Content-Disposition'])
    def test_deterministic_member_order(self):
        a,_=self.archive();self.assertEqual([(1,81),(1,999999),(4,84)],[(m['account_id'],m['transaction_id']) for m in a['investment_valuation_members']])
    def test_query_uses_membership_model(self):
        self.archive();self.assertIn('InvestmentValuationMember',self.db.queries)
    def test_read_only_export_does_not_mutate_records(self):
        self.archive();self.assertEqual(self.session.query(InvestmentValuationMember).count(),4)
    def test_empty_second_user_export(self):
        a,_=self.archive(user=99);self.assertEqual(a['accounts'],[]);self.assertEqual(a['investment_valuation_members'],[])

if __name__ == '__main__': unittest.main()
