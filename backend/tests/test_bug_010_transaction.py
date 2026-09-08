"""BUG-010 单事务数据保全回归：报告构建异常时，旧记录不被 supersede、无新记录、状态恢复 parsed。

对比 HEAD（旧行为）：记录循环后有 db.commit()，失败时 supersede + 新记录已提交、旧记录被改写。
修复后：mark superseded + insert records + report + status 合并一个事务，失败 rollback 全回滚。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


_RISK = {"risk_type": "R01", "level": "high", "clause_text": "违约金",
         "reason": "r", "suggestion": "s", "detection_method": "evidence",
         "confidence": 0.9}


class TestSingleTransactionRollback(unittest.TestCase):
    def test_report_build_failure_preserves_old_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = create_engine(f"sqlite:///{Path(tmp) / 't.db'}", connect_args={"timeout": 30})
            Base.metadata.create_all(eng)
            S = sessionmaker(bind=eng)

            # 前置：合同（auditing）+ 一条旧 valid 记录
            s = S()
            c = Contract(user_id=1, file_name="t", parsed_text="合同正文",
                         status="auditing", audit_mode="precise")
            s.add(c)
            s.commit()
            cid = c.id
            old = AuditRecord(contract_id=cid, audit_batch="b1", risk_type="R02",
                              risk_level="high", clause_text="旧条款", detection_method="rule",
                              result_status="valid")
            s.add(old)
            s.commit()
            s.close()

            with mock.patch.object(contracts, "run_rules", return_value=[_RISK]), \
                 mock.patch.object(contracts, "extract_evidence_detailed",
                                   return_value={"evidence": {}, "status": "ok",
                                                 "failed_chunks": 0, "total_chunks": 1}), \
                 mock.patch.object(contracts, "adjudicate_risks", return_value=[_RISK]), \
                 mock.patch.object(contracts, "build_recommendations", return_value=[_RISK]), \
                 mock.patch.object(contracts, "compare_clauses", return_value=None), \
                 mock.patch.object(contracts, "enrich_confidences", return_value=[_RISK]), \
                 mock.patch.object(contracts, "_build_risk_rows", side_effect=RuntimeError("report boom")), \
                 mock.patch.object(contracts, "SessionLocal", side_effect=lambda: S()):
                contracts._run_audit(cid)

            # 断言：旧记录仍 valid、无新记录、状态恢复 parsed
            s = S()
            recs = s.query(AuditRecord).filter(AuditRecord.contract_id == cid).all()
            self.assertEqual(len(recs), 1, "单事务回滚：不应有新增记录")
            self.assertEqual(recs[0].audit_batch, "b1")
            self.assertEqual(recs[0].result_status, "valid", "旧记录不得被 supersede")
            c2 = s.query(Contract).filter(Contract.id == cid).first()
            self.assertEqual(c2.status, "parsed", "状态应由 recovery 恢复为 parsed")
            s.close()
            eng.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
