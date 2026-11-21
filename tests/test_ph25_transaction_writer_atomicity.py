"""PH2.5: lock/reconcile/flush/guard/commit route integration contracts.

Read source only; database behavior is exercised by historical reconciliation
and membership store SQLite suites. PostgreSQL concurrency still pending.
"""
import ast
import unittest
from pathlib import Path

P = Path('modules/finance/router.py')
SRC = P.read_text(encoding='utf-8-sig')
TREE = ast.parse(SRC)


def body(name):
    functions = [n for n in TREE.body if isinstance(n, ast.FunctionDef)
                 and n.name == name]
    if len(functions) != 1:
        raise AssertionError('Missing or duplicated route '+name)
    return ast.get_source_segment(SRC, functions[0])


class PH25TransactionWriterIntegration(unittest.TestCase):
    def test_update_row_lock(self):
        self.assertEqual(body('update_transaction').count('with_for_update().first()'), 1)

    def test_delete_row_lock(self):
        self.assertEqual(body('delete_transaction').count('with_for_update().first()'), 1)

    def test_update_order_and_single_commit(self):
        s=body('update_transaction')
        steps=('lock_owned_accounts(', 'with_for_update().first()',
               'reconcile_valuation_transaction_change(', 'db.flush()',
               'validate_postwrite_investments(', 'db.commit()')
        self.assertEqual([s.index(x) for x in steps], sorted(s.index(x) for x in steps))
        self.assertEqual(s.count('db.commit()'), 1)

    def test_delete_order_and_single_commit(self):
        s=body('delete_transaction')
        steps=('lock_owned_accounts(', 'with_for_update().first()',
               'reconcile_valuation_transaction_change(', 'db.flush()',
               'validate_postwrite_investments(', 'db.commit()')
        self.assertEqual([s.index(x) for x in steps], sorted(s.index(x) for x in steps))
        self.assertEqual(s.count('db.commit()'), 1)

    def test_update_full_rollback(self):
        s=body('update_transaction')
        self.assertIn('except IntegrityError:', s)
        self.assertIn('except Exception:', s)
        self.assertGreaterEqual(s.count('db.rollback()'),2)

    def test_delete_full_rollback(self):
        s=body('delete_transaction')
        self.assertIn('except Exception:', s)
        self.assertIn('except IntegrityError:', s)
        self.assertGreaterEqual(s.count('db.rollback()'),2)

    def test_update_preserves_ph24_attribution(self):
        s=body('update_transaction')
        self.assertIn('requested_period =',s)
        self.assertIn('income_period is only allowed for income transactions',s)

    def test_goal_protection_before_reconciliation(self):
        for name in ('update_transaction','delete_transaction'):
            s=body(name)
            self.assertLess(s.index('is_goal_reserve_transaction('),
                            s.index('reconcile_valuation_transaction_change('))
            self.assertIn('GoalContribution.reversal_transaction_id', s)

    def test_import_ignore_inside_delete_transaction(self):
        s=body('delete_transaction')
        self.assertIn('IgnoredSourceId(',s)
        self.assertLess(s.index('IgnoredSourceId('),s.index('db.delete(tx)'))
        self.assertLess(s.index('db.delete(tx)'),s.index('db.flush()'))

    def test_reject_legacy_missing_source_edits(self):
        s=body('update_transaction')
        self.assertIn('tx.from_account_id is None',s)
        self.assertIn('Historical transfer missing source',s)
        self.assertLess(s.index('Historical transfer missing source'),
                        s.index('for field, value in update_data.items()'))

    def test_no_nontransfer_extra_transfer_accounts(self):
        s=body('update_transaction')
        self.assertIn('Transfer accounts require a transfer',s)

    def test_snapshot_before_update_mutation(self):
        s=body('update_transaction')
        self.assertLess(s.index('before_state ='),s.index('setattr(tx, field, value)'))
        self.assertIn('after_state = dict(before_state)',s)

    def test_delete_keeps_provenance_membership(self):
        s=body('delete_transaction')
        self.assertIn('before_state, None, locked_accounts',s)
        self.assertNotIn('db.delete(InvestmentValuationMember',s)

    def test_no_previous_investment_shift(self):
        for name in ('update_transaction','delete_transaction'):
            self.assertNotIn('_shift_investment_value(',body(name))


# Execute the actual AST-extracted route functions against a deliberately small
# fake DB. This checks call flow and rollback, not PostgreSQL row-lock behavior.
from decimal import Decimal as D
from types import SimpleNamespace
import copy
import importlib.util

_prov_path = Path('modules/finance/snapshot_provenance.py')
_prov_spec = importlib.util.spec_from_file_location('ph25_writer_prov',_prov_path)
_prov = importlib.util.module_from_spec(_prov_spec)
_prov_spec.loader.exec_module(_prov)


class FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code=status_code


class FakeType:
    TRANSFER='TRANSFER'
    INCOME='INCOME'
    EXPENSE='EXPENSE'


class _Column:
    def __eq__(self, other): return self


class Transaction:
    id=_Column()
    user_id=_Column()


class GoalContribution:
    id=object()
    transaction_id=_Column()
    reversal_transaction_id=_Column()


class IgnoredSourceId:
    id=object()
    user_id=_Column()
    source_id=_Column()
    def __init__(self, **kw): self.__dict__.update(kw)


class FakeData:
    def __init__(self, **kw): self.data=kw
    def model_dump(self, exclude_unset=False): return dict(self.data)


class FakeQuery:
    def __init__(self, db, kind): self.db,self.kind=db,kind
    def filter(self, *conditions): return self
    def with_for_update(self):
        self.db.events.append('tx_lock')
        if self.db.mutate_on_tx_lock is not None:
            self.db.mutate_on_tx_lock(self.db.tx)
        return self
    def populate_existing(self):
        return self
    def first(self):
        if self.kind is Transaction:
            return None if self.db.deleted else self.db.tx
        if self.kind is GoalContribution.id:
            return (1,) if self.db.goal_linked else None
        return None


class FakeDB:
    def __init__(self, typ='TRANSFER', amount='2000', included=True, source=2):
        self.tx=SimpleNamespace(id=101,user_id=1,type=typ,amount=D(amount),
                                account_id=source,from_account_id=source,to_account_id=1,
                                income_period=None,source_id=None,description='first')
        self.accounts={
            1:SimpleNamespace(id=1,user_id=1,type='INVESTMENT',current_market_value=D('12500'),valuation_ledger_anchor=D('12000')),
            2:SimpleNamespace(id=2,user_id=1,type='BANK',current_market_value=None,valuation_ledger_anchor=None),
        }
        self.members={101} if included else set()
        self.events=[]
        self.mutate_on_tx_lock=None
        self.locked_account_ids=[]
        self.goal_linked=False
        self.deleted=False
        self.added=[]
        self.commits=0
        self.fail_validation=False
        self.original=(copy.deepcopy(self.tx.__dict__),D('12000'))

    def query(self, kind): return FakeQuery(self, kind)
    def add(self,obj): self.added.append(obj)
    def delete(self,obj):
        self.events.append('delete')
        self.deleted=True
    def flush(self): self.events.append('flush')
    def commit(self):
        self.events.append('commit')
        self.commits+=1
    def rollback(self):
        self.events.append('rollback')
        self.tx.__dict__.clear()
        self.tx.__dict__.update(self.original[0])
        self.accounts[1].valuation_ledger_anchor=self.original[1]
        self.deleted=False
        self.added.clear()
    def refresh(self, tx): self.events.append('refresh')


def _get_owned_account(db,user_id,account_id,label=''):
    a=db.accounts.get(account_id)
    if a is None or a.user_id!=user_id: raise FakeHTTPException(404,'Account not found')
    return a


def _lock_accounts(db,user_id,account_ids):
    db.events.append('account_lock')
    assert list(account_ids)==sorted(set(account_ids))
    db.locked_account_ids=list(account_ids)
    return [_get_owned_account(db,user_id,a) for a in account_ids]


def _reconcile(db,user_id,transaction_id,before,after,locked_accounts):
    db.events.append('reconcile')
    if transaction_id not in db.members: return {}
    a=db.accounts[1]
    def contribution(state):
        if state is None: return D(0)
        return _prov.transaction_contributions(
            state['type'],state['amount'],account_id=state['account_id'],
            from_account_id=state['from_account_id'],to_account_id=state['to_account_id']
        ).get(1,D(0))
    a.valuation_ledger_anchor+=contribution(after)-contribution(before)
    return {1:a.valuation_ledger_anchor}


def _validate(db,user_id,accounts):
    db.events.append('validate')
    if db.fail_validation: raise FakeHTTPException(409,'Underflow')


def _compile_route(name):
    node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
    import copy as _copy
    function=_copy.deepcopy(node)
    function.decorator_list=[]
    module=ast.Module(body=[function],type_ignores=[])
    ast.fix_missing_locations(module)
    scope={
      'Transaction':Transaction,'GoalContribution':GoalContribution,
      'IgnoredSourceId':IgnoredSourceId,'TransactionType':FakeType,
      'HTTPException':FakeHTTPException,'IntegrityError':type('IntegrityError',(Exception,),{}),
      'Session':object,'User':object,'TransactionUpdate':object,
      'Depends':lambda fn: None,'get_db':lambda: None,'get_current_user':lambda: None,
      'status':SimpleNamespace(HTTP_404_NOT_FOUND=404,HTTP_409_CONFLICT=409),
      'or_':lambda *args:None,'is_goal_reserve_transaction':lambda *args:False,
      'get_owned_account':_get_owned_account,'reject_goal_reserve_account':lambda *args:None,
      'get_usable_category':lambda *args:None,
      'transaction_contributions':_prov.transaction_contributions,
      'lock_owned_accounts':_lock_accounts,
      'reconcile_valuation_transaction_change':_reconcile,
      'validate_postwrite_investments':_validate,
    }
    exec(compile(module,str(P),'exec'),scope)
    return scope[name]


EDIT=_compile_route('update_transaction')
DELETE=_compile_route('delete_transaction')
USER=SimpleNamespace(id=1)


class PH25TransactionWriterBehavior(unittest.TestCase):
    def test_included_transfer_edit_rebases_anchor(self):
        db=FakeDB()
        EDIT(101,FakeData(amount=D('3000')),db=db,current_user=USER)
        self.assertEqual(db.accounts[1].valuation_ledger_anchor,D('13000'))
        self.assertEqual(db.tx.amount,D('3000'))
        self.assertEqual(db.events,['account_lock','tx_lock','reconcile','flush','validate','commit','refresh'])

    def test_nonmember_transfer_edit_does_not_rebase(self):
        db=FakeDB(included=False)
        EDIT(101,FakeData(amount=D('3000')),db=db,current_user=USER)
        self.assertEqual(db.accounts[1].valuation_ledger_anchor,D('12000'))
        self.assertEqual(db.commits,1)

    def test_included_transfer_delete_rebases_before_removing(self):
        db=FakeDB()
        DELETE(101,current_user=USER,db=db)
        self.assertEqual(db.accounts[1].valuation_ledger_anchor,D('10000'))
        self.assertTrue(db.deleted)
        self.assertEqual(db.members,{101})
        self.assertEqual(db.events,['account_lock','tx_lock','reconcile','delete','flush','validate','commit'])

    def test_edit_validation_failure_rolls_back(self):
        db=FakeDB()
        db.fail_validation=True
        with self.assertRaises(FakeHTTPException):
            EDIT(101,FakeData(amount=D('3000')),db=db,current_user=USER)
        self.assertEqual(db.tx.amount,D('2000'))
        self.assertEqual(db.accounts[1].valuation_ledger_anchor,D('12000'))
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')

    def test_delete_validation_failure_rolls_back(self):
        db=FakeDB()
        db.fail_validation=True
        with self.assertRaises(FakeHTTPException):
            DELETE(101,current_user=USER,db=db)
        self.assertFalse(db.deleted)
        self.assertEqual(db.accounts[1].valuation_ledger_anchor,D('12000'))
        self.assertEqual(db.commits,0)

    def test_legacy_null_source_update_rejected(self):
        db=FakeDB(source=None)
        with self.assertRaises(FakeHTTPException) as exc:
            EDIT(101,FakeData(description='changed'),db=db,current_user=USER)
        self.assertEqual(exc.exception.status_code,409)
        self.assertEqual(db.commits,0)

    def test_goal_linked_delete_rejected_before_accounts(self):
        db=FakeDB()
        db.goal_linked=True
        with self.assertRaises(FakeHTTPException) as exc:
            DELETE(101,current_user=USER,db=db)
        self.assertEqual(exc.exception.status_code,409)
        self.assertNotIn('account_lock',db.events)

    def test_import_ignore_in_same_delete_commit(self):
        db=FakeDB()
        db.tx.source_id='gmail-foo'
        DELETE(101,current_user=USER,db=db)
        self.assertEqual(len(db.added),1)
        self.assertEqual(db.added[0].source_id,'gmail-foo')
        self.assertEqual(db.commits,1)

    def test_edit_race_on_row_during_account_lock_fails_closed(self):
        db=FakeDB()
        db.mutate_on_tx_lock=lambda tx: setattr(tx,'amount',D('2100'))
        with self.assertRaises(FakeHTTPException) as caught:
            EDIT(101,FakeData(amount=D('3000')),db=db,current_user=USER)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(db.events[:2],['account_lock','tx_lock'])
        self.assertNotIn('reconcile',db.events)
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')
        self.assertEqual(db.tx.amount,D('2000'))

    def test_delete_race_on_row_during_account_lock_fails_closed(self):
        db=FakeDB()
        db.mutate_on_tx_lock=lambda tx: setattr(tx,'from_account_id',3)
        with self.assertRaises(FakeHTTPException) as caught:
            DELETE(101,current_user=USER,db=db)
        self.assertEqual(caught.exception.status_code,409)
        self.assertNotIn('delete',db.events)
        self.assertNotIn('reconcile',db.events)
        self.assertEqual(db.commits,0)
        self.assertEqual(db.events[-1],'rollback')

    def test_edit_old_and_new_accounts_locked_before_transaction_row(self):
        db=FakeDB(included=False)
        db.accounts[3]=SimpleNamespace(id=3,user_id=1,type='BANK',
            current_market_value=None,valuation_ledger_anchor=None)
        EDIT(101,FakeData(from_account_id=3),db=db,current_user=USER)
        self.assertEqual(db.locked_account_ids,[1,2,3])
        self.assertEqual(db.events[:2],['account_lock','tx_lock'])
        self.assertEqual(db.tx.from_account_id,3)
        self.assertEqual(db.commits,1)

    def test_edit_goal_link_still_rejected_before_account_locks(self):
        db=FakeDB()
        db.goal_linked=True
        with self.assertRaises(FakeHTTPException) as caught:
            EDIT(101,FakeData(amount=D('2200')),db=db,current_user=USER)
        self.assertEqual(caught.exception.status_code,409)
        self.assertNotIn('account_lock',db.events)
        self.assertEqual(db.commits,0)

if __name__ == '__main__':
    unittest.main()
