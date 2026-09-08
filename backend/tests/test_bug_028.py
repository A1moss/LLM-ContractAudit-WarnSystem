"""BUG-028 生命周期专项回归：审核结果有效性（result_status）转换。

覆盖：
A. 新审核完成 → records = valid；
B. reject → valid → rejected；
C. re-audit → 旧 valid → superseded、旧 rejected 保持 rejected、新 records = valid。
关键：rejected 记录不得在后续重新审核时被改成 superseded/valid（「曾被驳回」是业务事实）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base  # noqa: E402
from models.contract import Contract  # noqa: E402
from models.audit_record import AuditRecord  # noqa: E402
import api.contracts as contracts  # noqa: E402


def _mk(db, cid, batch, status="valid"):
    r = AuditRecord(contract_id=cid, audit_batch=batch, risk_type="R02",
                    risk_level="high", clause_text="x", detection_method="rule",
                    result_status=status)
    db.add(r)
    return r


class TestBug028Lifecycle(unittest.TestCase):
    def _engine(self, tmp):
        eng = create_engine(f"sqlite:///{Path(tmp) / 't.db'}")
        Base.metadata.create_all(eng)
        return eng

    def _contract(self, s):
        c = Contract(user_id=1, file_name="t", parsed_text="x", status="completed", audit_mode="precise")
        s.add(c)
        s.commit()
        return c.id

    def test_reject_marks_valid_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp)
            S = sessionmaker(bind=eng)
            s = S()
            cid = self._contract(s)
            _mk(s, cid, "b1", "valid"); s.commit()
            contracts._mark_audit_records(s, cid, "valid", "rejected"); s.commit()
            r = s.query(AuditRecord).filter(AuditRecord.audit_batch == "b1").first()
            self.assertEqual(r.result_status, "rejected")
            s.close(); eng.dispose()

    def test_reaudit_preserves_rejected_and_adds_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp)
            S = sessionmaker(bind=eng)
            s = S()
            cid = self._contract(s)
            _mk(s, cid, "b1", "valid"); s.commit()
            # B. reject
            contracts._mark_audit_records(s, cid, "valid", "rejected"); s.commit()
            # C. re-audit
            contracts._mark_audit_records(s, cid, "valid", "superseded"); s.commit()
            _mk(s, cid, "b2", "valid"); s.commit()
            recs = {r.audit_batch: r.result_status
                    for r in s.query(AuditRecord).filter(AuditRecord.contract_id == cid).all()}
            self.assertEqual(recs["b1"], "rejected")   # 旧 rejected 保持
            self.assertEqual(recs["b2"], "valid")      # 新 valid
            s.close(); eng.dispose()

    def test_reaudit_marks_old_valid_superseded(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp)
            S = sessionmaker(bind=eng)
            s = S()
            cid = self._contract(s)
            _mk(s, cid, "b1", "valid"); s.commit()
            contracts._mark_audit_records(s, cid, "valid", "superseded"); s.commit()
            _mk(s, cid, "b2", "valid"); s.commit()
            recs = {r.audit_batch: r.result_status
                    for r in s.query(AuditRecord).filter(AuditRecord.contract_id == cid).all()}
            self.assertEqual(recs["b1"], "superseded")  # 曾有效被替代
            self.assertEqual(recs["b2"], "valid")
            s.close(); eng.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
