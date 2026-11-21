
"""PH2.5 recurring execution atomicity, isolated AST route behavior.

No network, no external database, no application imports.
"""
import ast
import datetime as _dt
import importlib.util
import unittest
from calendar import monthrange
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

D = Decimal

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)

class TransactionType:
    TRANSFER = 'TRANSFER'
    INCOME = 'INCOME'
    EXPENSE = 'EXPENSE'

class RecurringFrequency:
    WEEKLY = 'WEEKLY'
    MONTHLY = 'MONTHLY'
    YEARLY = 'YEARLY'

class RecurringTransaction:
    id = 0
    user_id = 0

class Transaction:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

class FakeAccount:
    def __init__(self, id, typ, balance, snapshot=None, anchor=None, owner=1):
        self.id = id
        self.type = typ
        self.ledger = D(str(balance))
        self.current_market_value = snapshot
        self.valuation_ledger_anchor = anchor
        self.user_id = owner

class FakeQuery:
    def __init__(self, db):
        self.db = db
    def filter(self, *predicates):
        self.db.events.append('schedule_filter')
        return self
    def with_for_update(self):
        self.db.events.append('schedule_lock')
        return self
    def first(self):
        return self.db.rec

class FakeDB:
    def __init__(self, rec, accounts, flush_error=False, commit_error=False):
        self.rec = rec
        self.accounts = {a.id: a for a in accounts}
        self.original_ledger = {a.id: a.ledger for a in accounts}
        self.original_date = rec.next_date if rec else None
        self.flush_error = flush_error
        self.commit_error = commit_error
        self.events = []
        self.pending = None
        self.commits = 0
    def query(self, model):
        self.events.append('query_schedule')
        if model is not RecurringTransaction:
            raise AssertionError('Unexpected query')
        return FakeQuery(self)
    def add(self, tx):
        self.events.append('add')
        self.pending = tx
    def flush(self):
        self.events.append('flush')
        if self.flush_error:
            raise RuntimeError('Simulated flush failure')
        tx = self.pending
        val = D(str(tx.amount))
        if tx.type == TransactionType.TRANSFER:
            self.accounts[tx.from_account_id].ledger -= val
            self.accounts[tx.to_account_id].ledger += val
        else:
            delta = val if tx.type == TransactionType.INCOME else -val
            self.accounts[tx.account_id].ledger += delta
    def commit(self):
        self.events.append('commit')
        if self.commit_error:
            raise RuntimeError('Simulated commit failure')
        self.commits += 1
    def rollback(self):
        self.events.append('rollback')
        for aid, ledger in self.original_ledger.items():
            self.accounts[aid].ledger = ledger
        if self.rec:
            self.rec.next_date = self.original_date
        self.pending = None
    def refresh(self, tx):
        self.events.append('refresh')

def validate_recurring_refs(db, user_id, tx_type, account_id, to_id, category_id):
    db.events.append('validate_refs')
    selected = [account_id, to_id] if tx_type == TransactionType.TRANSFER else [account_id]
    if tx_type == TransactionType.TRANSFER and (to_id is None or account_id == to_id):
        raise HTTPException(400, 'Invalid recurring transfer accounts')
    if tx_type != TransactionType.TRANSFER and category_id is None:
        raise HTTPException(400, 'Category required')
    for account_id in selected:
        acc = db.accounts.get(account_id)
        if acc is None or acc.user_id != user_id:
            raise HTTPException(404, 'Selected account not found')
        if acc.type == 'GOAL_RESERVE':
            raise HTTPException(409, 'Goal account protected')

def lock_owned_accounts(db, user_id, ids):
    db.events.append('account_lock')
    db.lock_order = sorted(set(ids))
    result = []
    for id in db.lock_order:
        acc = db.accounts.get(id)
        if acc is None or acc.user_id != user_id:
            raise HTTPException(404, 'Account not found')
        result.append(acc)
    return result

spec = importlib.util.spec_from_file_location('ph25_recurring_valuation', 'modules/finance/valuation.py')
valuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(valuation)

def validate_postwrite_investments(db, user_id, locked):
    db.events.append('validate_postwrite')
    if 'flush' not in db.events:
        raise AssertionError('Postwrite guard ran before flush')
    for acc in locked:
        if acc.type != 'INVESTMENT':
            continue
        value = valuation.effective_investment_value(
            acc.ledger, acc.current_market_value,
            acc.valuation_ledger_anchor,
        )
        try:
            valuation.require_nonnegative_investment_value(value)
        except ValueError as exc:
            raise HTTPException(409, 'Investment underflow') from exc

src = Path('modules/finance/router.py').read_text(encoding='utf-8-sig')
tree = ast.parse(src)
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('execute_recurring', 'calculate_next_date')]
if len(functions) != 2:
    raise AssertionError('Required recurring route/date calculator not unique')
for f in functions:
    f.decorator_list = []
module = ast.Module(body=functions, type_ignores=[])
ast.fix_missing_locations(module)
ns = {
    'RecurringTransaction': RecurringTransaction,
    'RecurringFrequency': RecurringFrequency,
    'TransactionType': TransactionType,
    'Transaction': Transaction,
    'HTTPException': HTTPException,
    'Optional': __import__('typing').Optional,
    '_dt': _dt,
    'monthrange': monthrange,
    'Query': lambda *args, **kwargs: None,
    'Depends': lambda value: None,
    'get_current_user': lambda: None,
    'get_db': lambda: None,
    'User': object,
    'Session': object,
    'validate_recurring_refs': validate_recurring_refs,
    'lock_owned_accounts': lock_owned_accounts,
    'validate_postwrite_investments': validate_postwrite_investments,
}
exec(compile(module, 'modules/finance/router.py', 'exec'), ns)
execute = ns['execute_recurring']
user = SimpleNamespace(id=1)
DUE = _dt.date(2026, 11, 1)

class RecurringAtomicity(unittest.TestCase):
    def bank(self, id=10, balance='20000', owner=1):
        return FakeAccount(id,'BANK',balance,owner=owner)
    def investment(self, id=20, balance='3000', snapshot=None, anchor=None):
        return FakeAccount(id,'INVESTMENT',balance,snapshot,anchor)
    def make(self, typ='TRANSFER', amount='2000', source=10, to=20, category=1, due=DUE, frequency='MONTHLY'):
        return SimpleNamespace(id=50,user_id=1,type=typ,account_id=source,to_account_id=to,category_id=category,amount=D(amount),next_date=due,frequency=frequency,description='Recurring event')
    def invoke(self, db, expected=DUE):
        return execute(50, expected_date=expected, current_user=user, db=db)
    def test_transfer_locks_schedule_then_sorted_accounts(self):
        db=FakeDB(self.make(source=20,to=10,amount='300'),[self.bank(),self.investment()])
        self.invoke(db)
        self.assertLess(db.events.index('schedule_lock'), db.events.index('account_lock'))
        self.assertEqual(db.lock_order,[10,20])
        self.assertEqual(db.events[-1],'refresh')
    def test_bank_to_investment_transfer(self):
        inv=self.investment()
        db=FakeDB(self.make(),[self.bank(),inv])
        tx=self.invoke(db)
        self.assertEqual(tx.type, TransactionType.TRANSFER)
        self.assertEqual(tx.source,'RECURRING')
        self.assertEqual(tx.from_account_id,10)
        self.assertEqual(tx.to_account_id,20)
        self.assertEqual(inv.ledger,D('5000'))
        self.assertEqual(db.accounts[10].ledger,D('18000'))
        self.assertEqual(db.rec.next_date,_dt.date(2026,12,1))
        self.assertEqual(db.commits,1)
    def test_revaluation_snapshot_unchanged(self):
        inv=self.investment(snapshot=D('3500'),anchor=D('3000'))
        db=FakeDB(self.make(),[self.bank(),inv])
        self.invoke(db)
        self.assertEqual(inv.current_market_value,D('3500'))
        self.assertEqual(inv.valuation_ledger_anchor,D('3000'))
        self.assertEqual(valuation.effective_investment_value(inv.ledger,inv.current_market_value,inv.valuation_ledger_anchor), D('5500'))
    def test_investment_outgoing_underflow_rolls_back(self):
        inv=self.investment(id=10,balance='400',snapshot=D('400'),anchor=D('400'))
        db=FakeDB(self.make(amount='500'),[inv,self.bank(20)])
        with self.assertRaises(HTTPException) as raised:
            self.invoke(db)
        self.assertEqual(raised.exception.status_code,409)
        self.assertEqual(inv.ledger,D('400'))
        self.assertEqual(db.rec.next_date,DUE)
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')
    def test_explicit_zero_underflow_rollback(self):
        inv=self.investment(id=10,balance='1000',snapshot=D('0'),anchor=D('1000'))
        db=FakeDB(self.make(amount='1'),[inv,self.bank(20)])
        with self.assertRaises(HTTPException):
            self.invoke(db)
        self.assertEqual(db.rec.next_date,DUE)
        self.assertEqual(inv.current_market_value,D('0'))
    def test_recurring_income(self):
        db=FakeDB(self.make(typ='INCOME',source=20),[self.investment()])
        self.invoke(db)
        self.assertEqual(db.accounts[20].ledger,D('5000'))
        self.assertEqual(db.lock_order,[20])
    def test_recurring_expense(self):
        db=FakeDB(self.make(typ='EXPENSE',amount='1000',source=20),[self.investment()])
        self.invoke(db)
        self.assertEqual(db.accounts[20].ledger,D('2000'))
        self.assertEqual(db.commits,1)
    def test_recurring_expense_underflow(self):
        db=FakeDB(self.make(typ='EXPENSE',amount='4000',source=20),[self.investment()])
        with self.assertRaises(HTTPException):
            self.invoke(db)
        self.assertEqual(db.accounts[20].ledger,D('3000'))
        self.assertEqual(db.rec.next_date,DUE)
        self.assertEqual(db.commits,0)
    def test_missing_destination_rejected(self):
        db=FakeDB(self.make(to=None),[self.bank()])
        with self.assertRaises(HTTPException):
            self.invoke(db)
        self.assertNotIn('add',db.events)
    def test_goal_reserve_protected(self):
        goal=FakeAccount(20,'GOAL_RESERVE','0')
        db=FakeDB(self.make(),[self.bank(),goal])
        with self.assertRaises(HTTPException) as raised:
            self.invoke(db)
        self.assertEqual(raised.exception.status_code,409)
        self.assertNotIn('add',db.events)
    def test_foreign_account_rejected(self):
        db=FakeDB(self.make(),[self.bank(),self.bank(20,owner=2)])
        with self.assertRaises(HTTPException):
            self.invoke(db)
        self.assertNotIn('add',db.events)
    def test_stale_expected_date_rejected(self):
        db=FakeDB(self.make(),[self.bank(),self.investment()])
        with self.assertRaises(HTTPException) as raised:
            self.invoke(db,expected=_dt.date(2026,10,1))
        self.assertEqual(raised.exception.status_code,409)
        self.assertNotIn('add',db.events)
    def test_flush_failure_rollback(self):
        db=FakeDB(self.make(),[self.bank(),self.investment()],flush_error=True)
        with self.assertRaises(RuntimeError):
            self.invoke(db)
        self.assertEqual(db.events[-1],'rollback')
        self.assertEqual(db.rec.next_date,DUE)
        self.assertEqual(db.commits,0)
    def test_commit_failure_rollback(self):
        db=FakeDB(self.make(),[self.bank(),self.investment()],commit_error=True)
        with self.assertRaises(RuntimeError):
            self.invoke(db)
        self.assertEqual(db.events[-1],'rollback')
        self.assertEqual(db.rec.next_date,DUE)
        self.assertEqual(db.accounts[10].ledger,D('20000'))
        self.assertEqual(db.commits,0)
    def test_postwrite_guard_precedes_schedule_change(self):
        db=FakeDB(self.make(),[self.bank(),self.investment()])
        self.invoke(db)
        self.assertLess(db.events.index('validate_postwrite'),db.events.index('commit'))
        self.assertLess(db.events.index('flush'),db.events.index('validate_postwrite'))
    def test_missing_recurring_schedule(self):
        db=FakeDB(None,[self.bank(),self.investment()])
        with self.assertRaises(HTTPException) as raised:
            self.invoke(db)
        self.assertEqual(raised.exception.status_code,404)
        self.assertNotIn('add',db.events)
    def test_month_end_scheduling(self):
        db=FakeDB(self.make(due=_dt.date(2026,1,31)),[self.bank(),self.investment()])
        execute(50,expected_date=_dt.date(2026,1,31),current_user=user,db=db)
        self.assertEqual(db.rec.next_date,_dt.date(2026,2,28))
    def test_weekly_scheduling(self):
        db=FakeDB(self.make(frequency='WEEKLY'),[self.bank(),self.investment()])
        self.invoke(db)
        self.assertEqual(db.rec.next_date,DUE+_dt.timedelta(days=7))

class RecurringStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        n = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='execute_recurring')
        cls.text=ast.get_source_segment(src,n)
    def test_uses_recurring_row_lock(self):
        self.assertIn('with_for_update()',self.text)
    def test_keeps_due_date_conflict(self):
        self.assertIn('expected_date != rec.next_date',self.text)
    def test_no_legacy_snapshot_shift(self):
        self.assertNotIn('_shift_investment_value(',self.text)
    def test_only_one_commit(self):
        self.assertEqual(self.text.count('db.commit()'),1)
    def test_account_lock_and_postwrite_guard(self):
        self.assertIn('lock_owned_accounts(',self.text)
        self.assertIn('validate_postwrite_investments(',self.text)

if __name__=='__main__':
    unittest.main()

