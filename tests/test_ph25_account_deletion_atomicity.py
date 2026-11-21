"""Isolated PH2.5 account deletion contract; no app imports or DB access."""
import ast
import copy
from pathlib import Path
import unittest

ROUTER = Path(__file__).resolve().parents[1] / 'modules/finance/router.py'
SOURCE = ROUTER.read_text(encoding='utf-8-sig')
NODE = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == 'delete_account')
FUNC = ast.get_source_segment(SOURCE, NODE)

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))

class Field:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return ('eq', self.name, value)

class GoalContribution:
    id = Field('GoalContribution.id')
    account_id = Field('GoalContribution.account_id')

class Transaction:
    id = Field('Transaction.id')
    user_id = Field('Transaction.user_id')
    account_id = Field('Transaction.account_id')
    from_account_id = Field('Transaction.from_account_id')
    to_account_id = Field('Transaction.to_account_id')

class RecurringTransaction:
    id = Field('RecurringTransaction.id')
    user_id = Field('RecurringTransaction.user_id')
    account_id = Field('RecurringTransaction.account_id')
    to_account_id = Field('RecurringTransaction.to_account_id')

class AccountType:
    GOAL_RESERVE = 'GOAL_RESERVE'
    INVESTMENT = 'INVESTMENT'
    BANK = 'BANK'

class Query:
    def __init__(self, db, field):
        self.db = db
        self.field = field
        self.where = []
    def filter(self, *conditions):
        self.where.extend(conditions)
        return self
    def first(self):
        key = self.field.name.split('.')[0]
        self.db.events.append('check_'+key)
        return object() if self.db.references.get(key, False) else None

class DummyDB:
    def __init__(self, account):
        self.account = account
        self.events = []
        self.references = {}
        self.fail = None
        self.deleted = []
        self.commits = 0
    def query(self, field):
        return Query(self, field)
    def delete(self, account):
        self.events.append('delete')
        if self.fail == 'delete': raise RuntimeError('delete failed')
        self.deleted.append(account)
    def flush(self):
        self.events.append('flush')
        if self.fail == 'flush': raise RuntimeError('flush failed')
    def commit(self):
        self.events.append('commit')
        if self.fail == 'commit': raise RuntimeError('commit failed')
        self.commits += 1
    def rollback(self):
        self.events.append('rollback')

class User:
    id = 19

def Depends(_): return None

def get_current_user(): pass

def get_db(): pass

def or_(*args): return ('or', args)

def lock_owned_accounts(db, user_id, ids):
    db.events.append('lock')
    if db.fail == 'lock': raise RuntimeError('lock failed')
    if user_id != 19 or tuple(ids) != (db.account.id,):
        raise RuntimeError('ownership or ID mismatch')
    if db.fail == 'missing': raise HTTPException(404, 'Account not found')
    return [db.account]

# Execute decorator-free FUNCTION only, preserving actual route in the app.
CLEAN_NODE = copy.deepcopy(NODE)
CLEAN_NODE.decorator_list = []
CTX = {
    'User': User,
    'Depends': Depends,
    'get_current_user': get_current_user,
    'get_db': get_db,
    'Session': DummyDB,
    'HTTPException': HTTPException,
    'AccountType': AccountType,
    'GoalContribution': GoalContribution,
    'Transaction': Transaction,
    'RecurringTransaction': RecurringTransaction,
    'or_': or_,
    'lock_owned_accounts': lock_owned_accounts,
}
exec(compile(ast.fix_missing_locations(ast.Module(body=[CLEAN_NODE], type_ignores=[])),
             '<isolated_account_deletion>', 'exec'), CTX)
DELETE = CTX['delete_account']

class AccountDeletionTests(unittest.TestCase):
    def make(self, account_type='BANK', members=None, references=None, fail=None):
        from types import SimpleNamespace
        account = SimpleNamespace(id=5, type=account_type, valuation_members=members or [])
        db = DummyDB(account)
        db.references = references or {}
        db.fail = fail
        return db
    def execute(self, db):
        return DELETE(account_id=5, current_user=User(), db=db)
    def names(self, db): return [s for s in db.events]
    def test_empty_bank_can_delete(self):
        db=self.make(); self.assertIsNone(self.execute(db))
        self.assertEqual(db.events, ['lock','check_GoalContribution','check_Transaction','check_RecurringTransaction','delete','flush','commit'])
        self.assertEqual(db.deleted,[db.account]); self.assertEqual(db.commits,1)
    def test_reference_queries_follow_lock(self):
        db=self.make(); self.execute(db)
        self.assertLess(db.events.index('lock'),db.events.index('check_GoalContribution'))
    def test_goal_reserve_rejected(self):
        db=self.make(account_type='GOAL_RESERVE')
        with self.assertRaises(HTTPException) as e:self.execute(db)
        self.assertEqual(e.exception.status_code,409);self.assertEqual(db.events,['lock'])
    def test_historical_goal_contributions_rejected(self):
        db=self.make(references={'GoalContribution':True})
        with self.assertRaises(HTTPException) as e:self.execute(db)
        self.assertEqual(e.exception.status_code,409);self.assertNotIn('delete',db.events)
    def test_account_transaction_rejected(self):
        db=self.make(references={'Transaction':True})
        with self.assertRaises(HTTPException):self.execute(db)
        self.assertNotIn('delete',db.events)
    def test_recurring_reference_rejected(self):
        db=self.make(references={'RecurringTransaction':True})
        with self.assertRaises(HTTPException):self.execute(db)
        self.assertNotIn('delete',db.events)
    def test_tombstone_valued_investment_rejected(self):
        db=self.make('INVESTMENT',[{'transaction_id':81,'baseline_contribution':-30}])
        with self.assertRaises(HTTPException) as e:self.execute(db)
        self.assertEqual(e.exception.status_code,409)
        self.assertIn('valuation history',e.exception.detail)
        self.assertNotIn('delete',db.events)
    def test_explicit_zero_valued_with_members_rejected(self):
        db=self.make('INVESTMENT',[{'transaction_id':81,'baseline_contribution':0}])
        with self.assertRaises(HTTPException):self.execute(db)
    def test_valued_investment_without_members_remains_deletable(self):
        db=self.make('INVESTMENT',[]);self.execute(db);self.assertEqual(db.commits,1)
    def test_plain_bank_with_no_members_deletable(self):
        db=self.make('BANK');self.execute(db);self.assertEqual(db.commits,1)
    def test_missing_account_does_not_scan(self):
        db=self.make(fail='missing')
        with self.assertRaises(HTTPException) as e:self.execute(db)
        self.assertEqual(e.exception.status_code,404);self.assertEqual(db.events,['lock'])
    def test_lock_failure_no_write(self):
        db=self.make(fail='lock')
        with self.assertRaises(RuntimeError):self.execute(db)
        self.assertEqual(db.events,['lock'])
    def test_delete_error_rolled_back(self):
        db=self.make(fail='delete')
        with self.assertRaises(RuntimeError):self.execute(db)
        self.assertIn('rollback',db.events);self.assertNotIn('commit',db.events)
    def test_flush_error_rolled_back(self):
        db=self.make(fail='flush')
        with self.assertRaises(RuntimeError):self.execute(db)
        self.assertEqual(db.events[-2:],['flush','rollback']);self.assertNotIn('commit',db.events)
    def test_commit_error_rolled_back(self):
        db=self.make(fail='commit')
        with self.assertRaises(RuntimeError):self.execute(db)
        self.assertEqual(db.events[-2:],['commit','rollback']);self.assertEqual(db.commits,0)
    def test_success_uses_single_commit(self):
        db=self.make();self.execute(db);self.assertEqual(db.events.count('commit'),1)
    def test_membership_guard_after_reference_scans(self):
        db=self.make('INVESTMENT',[object()])
        with self.assertRaises(HTTPException):self.execute(db)
        self.assertIn('check_RecurringTransaction',db.events)
    def test_no_automatic_transaction_mutations(self):
        self.assertNotIn('db.query(Transaction).delete(',FUNC)
        self.assertNotIn('db.query(Transaction).update(',FUNC)
    def test_source_uses_shared_lock(self):
        self.assertIn('lock_owned_accounts(',FUNC)
        self.assertNotIn('account = get_owned_account(',FUNC)
    def test_source_has_rollback_and_flush(self):
        self.assertIn('db.rollback()',FUNC);self.assertIn('db.flush()',FUNC)
    def test_source_does_not_force_delete_members(self):
        self.assertNotIn('db.delete(member)',FUNC)
        self.assertNotIn('account.valuation_members.clear()',FUNC)
    def test_decorators_preserved_on_app_route(self):
        # Preserve any original routing decorator in real file; compile stripped copy only.
        self.assertEqual(CTX['delete_account'].__name__,'delete_account')

if __name__=='__main__': unittest.main()
