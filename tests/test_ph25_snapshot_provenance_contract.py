import unittest
from decimal import Decimal as D
from modules.finance.snapshot_provenance import transaction_contributions as c, capture_account_membership as capture, rebase_included_anchor as rebase, require_safe_account_reassignment as check_move

def tx(n, typ, amount, **kwargs):
    return {'id':n,'type':typ,'amount':D(amount),**kwargs}

class ProvenanceContract(unittest.TestCase):
    def test_income_positive(self): self.assertEqual(c('INCOME',D('200'),account_id=1),{1:D('200')})
    def test_expense_negative(self): self.assertEqual(c('EXPENSE',D('50'),account_id=1),{1:D('-50')})
    def test_transfer_signed(self): self.assertEqual(c('TRANSFER',D('25'),from_account_id=1,to_account_id=2,account_id=1),{1:D('-25'),2:D('25')})
    def test_transfer_null_source_not_fallback(self): self.assertEqual(c('TRANSFER',D('25'),account_id=1,to_account_id=2),{2:D('25')})
    def test_missing_regular_account(self): self.assertEqual(c('EXPENSE',D('25')), {})
    def test_zero_value_member(self): self.assertEqual(capture(2,[tx(4,'TRANSFER','0',from_account_id=1,to_account_id=2)]),{4:D('0')})
    def test_membership_signed_by_account(self):
        rows = [tx(4,'TRANSFER','2000',from_account_id=1,to_account_id=2),tx(5,'INCOME','300',account_id=2)]
        self.assertEqual(capture(1,rows),{4:D('-2000')})
        self.assertEqual(capture(2,rows),{4:D('2000'),5:D('300')})
    def test_duplicate_member_rejected(self):
        rows=[tx(4,'INCOME','10',account_id=2)]*2
        with self.assertRaises(ValueError): capture(2,rows)
    def test_invalid_amounts_and_ids(self):
        for amount in ('NaN','Infinity','-1',None):
            with self.subTest(amount=amount),self.assertRaises(ValueError): c('INCOME',amount,account_id=2)
        for accid in ('2',True,0):
            with self.subTest(accid=accid),self.assertRaises(ValueError): c('INCOME','1',account_id=accid)
    def test_included_edit_preserves_snapshot(self):
        self.assertEqual(D('12500')+(D('13000')-rebase(D('12000'),included=True,before=D('2000'),after=D('3000'))),D('12500'))
    def test_included_delete_preserves_snapshot(self):
        self.assertEqual(D('12500')+(D('10000')-rebase(D('12000'),included=True,before=D('2000'),after=D('0'))),D('12500'))
    def test_new_transfer_not_rebased(self):
        self.assertEqual(rebase(D('12000'),included=False,before=0,after=1000),D('12000'))
    def test_backdated_new_not_included(self):
        frozen={101:D('2000')}
        self.assertNotIn(202,frozen)
        self.assertEqual(rebase(D('12000'),included=202 in frozen,before=0,after=1000),D('12000'))
    def test_repeat_edits_delta_from_current(self):
        anchor=rebase(D('12000'),included=True,before=2000,after=3000)
        anchor=rebase(anchor,included=True,before=3000,after=3500)
        self.assertEqual(anchor,D('13500'))
        self.assertEqual(D('12500')+(D('13500')-anchor),D('12500'))
    def test_date_only_edit_no_rebase(self): self.assertEqual(rebase(D('12000'),included=True,before=2000,after=2000),D('12000'))
    def test_revaluation_replaces_membership(self):
        original=capture(2,[tx(101,'INCOME','100',account_id=2)])
        new=capture(2,[tx(101,'INCOME','100',account_id=2),tx(202,'INCOME','200',account_id=2)])
        self.assertEqual(original,{101:D('100')}); self.assertEqual(new,{101:D('100'),202:D('200')})
    def test_move_with_valued_account_blocked(self):
        with self.assertRaises(ValueError): check_move({1:D('-100'),2:D('100')},{1:D('-100'),3:D('100')},{2})
    def test_move_without_valued_account_allowed(self): check_move({1:D('-100'),2:D('100')},{1:D('-100'),3:D('100')},set())
    def test_amount_edit_keeps_membership_accounts(self): check_move({1:D('-100'),2:D('100')},{1:D('-200'),2:D('200')},{2})
    def test_missing_anchor_and_included_rejected(self):
        with self.assertRaises(ValueError): rebase(None,included=True,before=100,after=0)
        self.assertIsNone(rebase(None,included=False,before=0,after=100))

if __name__=='__main__': unittest.main()
