"""BUG-036 回归测试：统一 timezone-aware UTC 语义（应用层 naive UTC 写入，与 DB 方言解耦）。

覆盖：
1. utcnow_naive() 返回 naive UTC；
2. ORM 写入/读取（SQLite）时间值 ≈ 当前 UTC 且无 tzinfo（即无 8h 偏移）；
3. _iso 序列化补 "Z"。

MySQL 方言验证：本环境无 MySQL 实例，无法实跑；但修复是应用层写 UTC、不依赖
DB 的 server_default，按构造对 MySQL（NOW()=本地时间）同样成立。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base, utcnow_naive  # noqa: E402
from models.template import Template  # noqa: E402
from api.templates import _iso  # noqa: E402


class TestBug036Timezone(unittest.TestCase):
    def test_utcnow_naive_returns_naive_utc(self):
        now = utcnow_naive()
        self.assertIsNone(now.tzinfo)  # naive
        ref = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertLess(abs((now - ref).total_seconds()), 5)

    def test_orm_write_read_is_naive_utc(self):
        # 写入/读取往返：SQLite 下应拿到 naive UTC，而非本地时间（本地时间会差 8h）
        with tempfile.TemporaryDirectory() as tmp:
            eng = create_engine(f"sqlite:///{Path(tmp) / 't.db'}")
            Base.metadata.create_all(eng)
            S = sessionmaker(bind=eng)
            s = S()
            t = Template(name="t", contract_type="买卖", clauses={})
            s.add(t)
            s.commit()
            s.refresh(t)
            self.assertIsNotNone(t.created_at)
            self.assertIsNone(t.created_at.tzinfo)  # 无 tzinfo（naive UTC）
            ref = utcnow_naive()
            # 若写成 local naive（东八区），这里会差 ~8h=28800s，测试会失败
            self.assertLess(abs((t.created_at - ref).total_seconds()), 5)
            s.close()
            eng.dispose()  # 释放 SQLite 文件句柄，避免 Windows 下临时目录清理失败

    def test_iso_serializes_utc_with_z(self):
        t = datetime(2026, 9, 8, 12, 0, 0)
        self.assertEqual(_iso(t), "2026-09-08T12:00:00Z")
        self.assertIsNone(_iso(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
