"""PH2.5 membership model and migration source contract (no DB)."""
import ast
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

MODEL = Path("modules/finance/models.py")
MIGRATION = Path("alembic/versions/b4f8c2a7d910_ph25_valuation_membership.py")
PARENT = Path("alembic/versions/a0d91bc6e275_ph25_valuation_ledger_anchor.py")

class RecordingOps:
    def __init__(self): self.events = []
    def create_table(self, *args, **kwargs): self.events.append(("create_table",args,kwargs))
    def create_index(self, *args, **kwargs): self.events.append(("create_index",args,kwargs))
    def execute(self, sql): self.events.append(("execute",str(sql),{}))
    def drop_index(self, *args, **kwargs): self.events.append(("drop_index",args,kwargs))
    def drop_table(self, *args, **kwargs): self.events.append(("drop_table",args,kwargs))

def migration_module():
    spec = importlib.util.spec_from_file_location("ph25_membership_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def model_class(name):
    tree=ast.parse(MODEL.read_text(encoding="utf-8-sig"))
    result=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name]
    if len(result)!=1: raise AssertionError("Unexpected model class: "+name)
    return result[0]

class MembershipMigrationContract(unittest.TestCase):
    def test_revision_chain(self):
        mig=migration_module()
        self.assertEqual(mig.revision,"b4f8c2a7d910")
        self.assertEqual(mig.down_revision,"a0d91bc6e275")
        self.assertIn("valuation_ledger_anchor",PARENT.read_text(encoding="utf-8-sig"))

    def test_account_relationship(self):
        account=model_class("Account")
        self.assertIn("valuation_members", {t.id for n in account.body if isinstance(n,ast.Assign)
                        for t in n.targets if isinstance(t,ast.Name)})

    def test_member_fields_and_uniqueness(self):
        member=model_class("InvestmentValuationMember")
        fields={t.id for n in member.body if isinstance(n,ast.Assign)
                for t in n.targets if isinstance(t,ast.Name)}
        self.assertTrue({"account_id","transaction_id","baseline_contribution"}<=fields)
        source=MODEL.read_text(encoding="utf-8-sig")
        self.assertIn("uq_ivm_account_transaction",source)

    def test_member_transaction_id_has_no_fk(self):
        member=model_class("InvestmentValuationMember")
        tx_field=next(n for n in member.body if isinstance(n,ast.Assign)
                      and any(isinstance(t,ast.Name) and t.id=="transaction_id" for t in n.targets))
        args=ast.dump(tx_field)
        self.assertNotIn("ForeignKey",args)

    def test_migration_upgrade_operations(self):
        mig=migration_module(); rec=RecordingOps()
        with patch.object(mig,"op",rec): mig.upgrade()
        self.assertEqual([e[0] for e in rec.events], ["create_table","create_index","execute"])
        self.assertEqual(rec.events[0][1][0],"investment_valuation_members")
        self.assertEqual(rec.events[1][1][0],"ix_ivm_transaction_id")

    def test_migration_has_only_account_fk(self):
        mig=migration_module(); rec=RecordingOps()
        with patch.object(mig,"op",rec): mig.upgrade()
        fks=[a for a in rec.events[0][1][1:] if a.__class__.__name__=="ForeignKeyConstraint"]
        self.assertEqual(len(fks),1)
        self.assertEqual(fks[0].ondelete,"CASCADE")
        self.assertIn("accounts.id",str(list(fks[0].elements)[0].target_fullname))

    def test_backfill_signed_account_contributions(self):
        mig=migration_module(); rec=RecordingOps()
        with patch.object(mig,"op",rec): mig.upgrade()
        sql=rec.events[2][1]
        for token in ("'INCOME'", "'EXPENSE'", "'TRANSFER'", "UNION ALL",
                      "t.from_account_id", "t.to_account_id", "-t.amount",
                      "SUM(contributions.signed_amount)"):
            with self.subTest(token=token): self.assertIn(token,sql)

    def test_backfill_is_user_scoped_and_only_anchored(self):
        mig=migration_module(); rec=RecordingOps()
        with patch.object(mig,"op",rec): mig.upgrade()
        sql=rec.events[2][1]
        for token in ("a.user_id = contributions.user_id", "a.type = 'INVESTMENT'",
                      "a.current_market_value IS NOT NULL",
                      "a.valuation_ledger_anchor IS NOT NULL"):
            with self.subTest(token=token): self.assertIn(token,sql)

    def test_backfill_includes_zero_and_does_not_modify_snapshots(self):
        mig=migration_module(); rec=RecordingOps()
        with patch.object(mig,"op",rec): mig.upgrade()
        sql=rec.events[2][1].upper()
        self.assertNotIn("HAVING",sql)
        self.assertNotIn("UPDATE ACCOUNTS",sql)
        self.assertNotIn("DELETE FROM TRANSACTIONS",sql)

    def test_downgrade_only_removes_membership_table(self):
        mig=migration_module(); rec=RecordingOps()
        with patch.object(mig,"op",rec): mig.downgrade()
        self.assertEqual([e[0] for e in rec.events],["drop_index","drop_table"])
        self.assertEqual(rec.events[-1][1][0],"investment_valuation_members")

if __name__ == "__main__": unittest.main()
