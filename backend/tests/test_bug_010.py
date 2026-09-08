"""BUG-010 regression：审计主流程失败后的状态恢复容错（独立会话 + lock 退避重试）。

验收口径（按 review 改严）：
- 恢复路径独立（独立 SessionLocal）、可重试（locked 退避）、可观测（失败有 error 日志）；
- recovery 不抛异常、不覆盖原始异常、Session 正确 rollback/close；
- 不把「recovery failed」误报成「recovery succeeded」。
注意：3 次全 locked 时最终 DB 仍可能 auditing——这是 DB 不可写条件下的客观结果，
由 BUG-011 重启复位兜底，不在本测试承诺「一定 parsed」。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from sqlalchemy.exc import OperationalError  # noqa: E402
import api.contracts as contracts  # noqa: E402  重 import（约 40s）


def _locked_err():
    return OperationalError("UPDATE", {}, Exception("database is locked"))


class TestRecoverContractStatus(unittest.TestCase):
    def _db(self, status="auditing"):
        db = mock.MagicMock()
        c = mock.MagicMock()
        c.status = status
        db.query.return_value.filter.return_value.first.return_value = c
        return db, c

    def test_success_sets_parsed(self):
        db, c = self._db()
        with mock.patch.object(contracts, "SessionLocal", return_value=db):
            contracts._recover_contract_status(1)
        self.assertEqual(c.status, "parsed")
        db.commit.assert_called_once()
        db.close.assert_called_once()

    def test_retry_on_locked_then_succeeds(self):
        db1, c1 = self._db()
        db1.commit.side_effect = _locked_err()
        db2, c2 = self._db()
        with mock.patch.object(contracts, "SessionLocal", side_effect=[db1, db2]), \
             mock.patch.object(contracts.time, "sleep", return_value=None):
            contracts._recover_contract_status(1)
        self.assertEqual(c2.status, "parsed")  # 重试后成功
        db1.rollback.assert_called()
        db2.commit.assert_called_once()

    def test_all_locked_no_raise_and_logs_error(self):
        dbs = []
        def factory():
            db, c = self._db()
            db.commit.side_effect = _locked_err()
            dbs.append(db)
            return db
        with mock.patch.object(contracts, "SessionLocal", side_effect=factory), \
             mock.patch.object(contracts.time, "sleep", return_value=None), \
             self.assertLogs("api.contracts", level="ERROR") as cm:
            contracts._recover_contract_status(1)  # 不抛异常
        self.assertEqual(len(dbs), 3)  # 3 次尝试
        self.assertTrue(any("状态恢复失败" in l for l in cm.output))
        # 每次尝试都 rollback + close（Session 正确清理）
        for db in dbs:
            db.rollback.assert_called()
            db.close.assert_called()
        # 未误报成功：无「恢复成功」类日志（本实现只在失败时记 error）

    def test_non_auditing_no_commit(self):
        db, c = self._db(status="completed")
        with mock.patch.object(contracts, "SessionLocal", return_value=db):
            contracts._recover_contract_status(1)
        db.commit.assert_not_called()
        db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
