"""BUG-009 regression：SQLite WAL + busy_timeout（降低并发写锁冲突失败概率）。

只验证两项：journal_mode=WAL、busy_timeout=30s。不做并发写根因解决（SQLite 仍是单写者）。
"""
import os
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")

from sqlalchemy import text  # noqa: E402
import database  # noqa: E402


class TestBug009SqliteConfig(unittest.TestCase):
    def test_wal_mode_enabled(self):
        if not database._is_sqlite:
            self.skipTest("非 SQLite 环境")
        with database.engine.connect() as conn:
            jm = str(conn.execute(text("PRAGMA journal_mode")).scalar()).lower()
        self.assertEqual(jm, "wal")

    def test_busy_timeout_30s(self):
        if not database._is_sqlite:
            self.skipTest("非 SQLite 环境")
        with database.engine.connect() as conn:
            bt = conn.execute(text("PRAGMA busy_timeout")).scalar()
        self.assertEqual(bt, 30000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
