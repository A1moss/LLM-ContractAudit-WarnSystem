"""BUG-042 regression：get_audit_result 按严重程度排序（high→medium→low），非字典序。
修复前 risk_level.desc() 是字典序 medium>low>high，高风险沉底。
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


class TestBug042SortOrder(unittest.TestCase):
    def test_high_medium_low_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = create_engine(f"sqlite:///{Path(tmp) / 't.db'}")
            Base.metadata.create_all(eng)
            S = sessionmaker(bind=eng)
            s = S()
            c = Contract(user_id=1, file_name="t", parsed_text="x", status="completed", audit_mode="precise")
            s.add(c)
            s.commit()
            cid = c.id
            for level in ("low", "medium", "high"):  # 故意乱序插入
                s.add(AuditRecord(contract_id=cid, audit_batch="b1", risk_type="R01",
                                  risk_level=level, clause_text="x", detection_method="rule",
                                  result_status="valid"))
            s.commit()
            user = mock.MagicMock()
            user.role = "admin"
            res = contracts.get_audit_result(cid, db=s, current_user=user)
            levels = [r["risk_level"] for r in res["data"]["items"]]
            self.assertEqual(levels, ["high", "medium", "low"])
            s.close()
            eng.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
