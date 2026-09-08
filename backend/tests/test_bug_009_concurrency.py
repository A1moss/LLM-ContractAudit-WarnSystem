"""BUG-009 并发回归：trigger_audit 原子乐观锁 + get_audit_result 最新批次过滤。

覆盖：
1. trigger_audit 幂等守卫（已 auditing → 409）+ 原子占位（update 0 行 → 409、1 行 → 成功）；
2. get_audit_result 只返回最新 audit_batch（双 valid 批次 → 只返回后者；A superseded + B valid → 只返回 B）；
3. 真实并发（两线程 + WAL + busy_timeout）：两个并发 trigger_audit → 一个成功、一个 409。
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base  # noqa: E402
from models.contract import Contract  # noqa: E402
from models.audit_record import AuditRecord  # noqa: E402
import api.contracts as contracts  # noqa: E402


def _mk(s, cid, batch, status="valid"):
    r = AuditRecord(contract_id=cid, audit_batch=batch, risk_type="R02",
                    risk_level="high", clause_text="x", detection_method="rule",
                    result_status=status)
    s.add(r)
    return r


class TestTriggerAuditAtomicLock(unittest.TestCase):
    """trigger_audit 幂等守卫 + 原子占位（mock db，不触真实并发）。"""

    def _contract(self, status="parsed"):
        c = mock.MagicMock()
        c.parsed_text = "x"
        c.status = status
        c.user_id = 1
        return c

    def _user(self):
        u = mock.MagicMock()
        u.id = 1
        u.role = "uploader"
        return u

    def test_already_auditing_409(self):
        db = mock.MagicMock()
        db.query.return_value.filter.return_value.first.return_value = self._contract("auditing")
        with self.assertRaises(HTTPException) as cm:
            contracts.trigger_audit(1, background_tasks=mock.MagicMock(), db=db, current_user=self._user())
        self.assertEqual(cm.exception.status_code, 409)

    def test_atomic_update_zero_rows_409(self):
        db = mock.MagicMock()
        db.query.return_value.filter.return_value.first.return_value = self._contract("parsed")
        db.query.return_value.filter.return_value.update.return_value = 0  # 并发下另一请求已占位
        with self.assertRaises(HTTPException) as cm:
            contracts.trigger_audit(1, background_tasks=mock.MagicMock(), db=db, current_user=self._user())
        self.assertEqual(cm.exception.status_code, 409)

    def test_atomic_update_one_row_success(self):
        db = mock.MagicMock()
        db.query.return_value.filter.return_value.first.return_value = self._contract("parsed")
        db.query.return_value.filter.return_value.update.return_value = 1
        bg = mock.MagicMock()
        res = contracts.trigger_audit(1, background_tasks=bg, db=db, current_user=self._user())
        self.assertEqual(res["code"], 0)
        bg.add_task.assert_called_once()
        db.commit.assert_called()


class TestGetAuditResultLatestBatch(unittest.TestCase):
    """get_audit_result 只返回最新 audit_batch（真实临时 SQLite）。"""

    def _engine(self, tmp):
        eng = create_engine(f"sqlite:///{Path(tmp) / 't.db'}")
        Base.metadata.create_all(eng)
        return eng

    def _contract(self, s):
        c = Contract(user_id=1, file_name="t", parsed_text="x", status="completed", audit_mode="precise")
        s.add(c)
        s.commit()
        return c.id

    def _user(self):
        u = mock.MagicMock()
        u.role = "admin"  # WORKFLOW_ROLES → 可查看全部
        return u

    def test_two_valid_batches_only_latest(self):
        # 用例 1：batch A valid + batch B valid → 只返回 B
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp)
            S = sessionmaker(bind=eng)
            s = S()
            cid = self._contract(s)
            _mk(s, cid, "b1", "valid")
            _mk(s, cid, "b2", "valid")
            s.commit()
            res = contracts.get_audit_result(cid, db=s, current_user=self._user())
            batches = {r["audit_batch"] for r in res["data"]["items"]}
            self.assertEqual(batches, {"b2"})
            s.close()
            eng.dispose()

    def test_reaudit_superseded_old_batch_only_latest(self):
        # 用例 2：A valid → 重审 → A superseded + B valid → 仍只返回 B
        with tempfile.TemporaryDirectory() as tmp:
            eng = self._engine(tmp)
            S = sessionmaker(bind=eng)
            s = S()
            cid = self._contract(s)
            _mk(s, cid, "b1", "valid")
            s.commit()
            contracts._mark_audit_records(s, cid, "valid", "superseded")
            s.commit()
            _mk(s, cid, "b2", "valid")
            s.commit()
            res = contracts.get_audit_result(cid, db=s, current_user=self._user())
            batches = {r["audit_batch"] for r in res["data"]["items"]}
            self.assertEqual(batches, {"b2"})
            self.assertTrue(res["data"]["has_current_result"])
            s.close()
            eng.dispose()


class TestConcurrentTriggerAudit(unittest.TestCase):
    """真实并发：两线程 + WAL + busy_timeout，两个并发 trigger_audit → 一个成功、一个 409。"""

    def test_concurrent_only_one_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = create_engine(f"sqlite:///{Path(tmp) / 't.db'}", connect_args={"timeout": 30})
            with eng.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
            Base.metadata.create_all(eng)
            S = sessionmaker(bind=eng)
            s = S()
            c = Contract(user_id=1, file_name="t", parsed_text="x", status="parsed", audit_mode="precise")
            s.add(c)
            s.commit()
            cid = c.id
            s.close()

            user = mock.MagicMock()
            user.id = 1
            user.role = "uploader"
            bg = mock.MagicMock()
            results = []
            barrier = threading.Barrier(2)

            def worker():
                s = S()
                try:
                    barrier.wait(timeout=10)
                    contracts.trigger_audit(cid, background_tasks=bg, db=s, current_user=user)
                    results.append("ok")
                except HTTPException as e:
                    results.append(str(e.status_code))
                finally:
                    s.close()

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)

            self.assertEqual(sorted(results), ["409", "ok"])

            s = S()
            c2 = s.query(Contract).filter(Contract.id == cid).first()
            s.close()
            self.assertEqual(c2.status, "auditing")  # 只被一个请求占位
            eng.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
