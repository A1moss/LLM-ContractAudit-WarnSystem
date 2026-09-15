"""静启动（后台预热）测试：不阻塞启动、幂等、失败不影响服务、健康检查可查。

覆盖：
1. DISABLE_WARMUP=1 → 不启动预热
2. 幂等：重复调用只启一次；预热在后台线程里跑（调用立即返回）
3. 预热失败只记状态、不抛异常（WARMUP_STRICT 例外）
4. /api/health 暴露预售状态
5. lifespan 里确实接了 start_warmup（改坏了能被测出来）

运行（backend 目录下）：
    python -m unittest tests.test_warmup -v
"""
import asyncio
import gc
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from services import warmup  # noqa: E402
import main as app_main  # noqa: E402


class TestWarmup(unittest.TestCase):

    def setUp(self):
        # 每个用例重置模块级幂等标志与状态
        warmup._started = False
        warmup.STATE.update({"rag": "idle", "ocr": "idle", "rag_seconds": None, "ocr_seconds": None})
        os.environ.pop("DISABLE_WARMUP", None)
        os.environ.pop("WARMUP_OCR", None)

    def tearDown(self):
        os.environ.pop("DISABLE_WARMUP", None)
        os.environ.pop("WARMUP_OCR", None)
        warmup._started = False

    def test_disabled_by_env(self):
        os.environ["DISABLE_WARMUP"] = "1"
        self.assertFalse(warmup.start_warmup())
        self.assertEqual(warmup.STATE["rag"], "idle")

    def test_idempotent_and_non_blocking(self):
        """start_warmup 立即返回（不阻塞），且重复调用只启一次。"""
        started = threading.Event()

        def fake_warm_rag():
            started.set()
            warmup.STATE["rag"] = "ready"

        with mock.patch.object(warmup, "_warm_rag", side_effect=fake_warm_rag):
            t0 = time.perf_counter()
            first = warmup.start_warmup()
            elapsed = time.perf_counter() - t0
            second = warmup.start_warmup()

            self.assertTrue(first)
            self.assertFalse(second)          # 幂等
            self.assertLess(elapsed, 0.5)     # 立即返回，不等预热跑完
            self.assertTrue(started.wait(5))  # 预热确实在后台线程里跑了
            self.assertEqual(warmup.STATE["rag"], "ready")

    def test_rag_failure_recorded_not_raised(self):
        """预热失败：状态记 failed、不抛异常（服务照常可用）。"""
        with mock.patch.dict(sys.modules, {"ai.rag.vector_store": None}):
            warmup._warm_rag()          # 不应抛
        self.assertEqual(warmup.STATE["rag"], "failed")

    def test_rag_failure_strict_raises(self):
        os.environ["WARMUP_STRICT"] = "1"
        try:
            with mock.patch.dict(sys.modules, {"ai.rag.vector_store": None}):
                with self.assertRaises(Exception):
                    warmup._warm_rag()
        finally:
            os.environ.pop("WARMUP_STRICT", None)
            warmup.STATE["rag"] = "idle"

    def test_health_exposes_warmup(self):
        res = app_main.health()
        self.assertEqual(res["status"], "ok")
        self.assertIn("warmup", res)
        self.assertIn("rag", res["warmup"])
        self.assertIn("ocr", res["warmup"])

    def test_lifespan_starts_warmup(self):
        """lifespan 必须接上静启动（防回归：把 start_warmup 删掉会被测出来）。

        ## 为什么必须隔离数据库（本用例的数据库隔离说明）

        `lifespan` 里有真实的 DDL/DML 副作用：

            Base.metadata.create_all(bind=engine)   # 建表
            _ensure_columns()                       # ALTER TABLE 补列
            UPDATE contracts SET status='parsed' WHERE status='auditing'
            role_bootstrap.bootstrap_admin(db)      # 可能提升账号角色

        这些副作用**读的是 `main` 模块里的 `engine` / `SessionLocal` 全局名**，
        默认指向 `database.engine`（即真实开发库 `backend/contract.db`）。
        因此本用例若不隔离，跑一次单测就会改动真实库的 schema/数据。

        隔离方式（方案 B：只换数据库依赖，不改生产逻辑）：
        * `mock.patch.object(app_main, "engine", <临时 engine>)`
        * `mock.patch.object(app_main, "SessionLocal", <临时 sessionmaker>)`
          —— `lifespan`、`_ensure_columns()`（内部按 `engine.url` 解析路径、
          并用 `_migrate_fk_column_type` 重建表）全部随之落到临时库；
        * 生产 `lifespan` 的代码路径与行为**一行未改**，只是把它依赖的库换成临时库；
        * 用例结束 `engine.dispose()` + 删除临时目录，不留残留文件。

        同时把 `warmup_service.start_warmup` 与 `role_bootstrap.bootstrap_admin` 打桩，
        语义与改造前完全一致（断言两者各被调用一次）。
        """
        tmp = tempfile.TemporaryDirectory()
        eng = None
        try:
            db_path = Path(tmp.name) / "warmup_lifespan.db"
            eng = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False, "timeout": 30},
            )
            TempSession = sessionmaker(bind=eng)

            async def _run():
                with mock.patch.object(app_main, "engine", eng), \
                     mock.patch.object(app_main, "SessionLocal", TempSession), \
                     mock.patch.object(app_main.warmup_service, "start_warmup") as sw, \
                     mock.patch.object(app_main.role_bootstrap, "bootstrap_admin") as sb:
                    async with app_main.lifespan(app_main.app):
                        pass
                    sw.assert_called_once()
                    sb.assert_called_once()

            asyncio.run(_run())

            # 隔离生效的正面证据：建表 DDL 落在【临时库】里（真实库因此完全不受影响）
            self.assertIn("contracts", inspect(eng).get_table_names())
            self.assertTrue(db_path.exists())
        finally:
            if eng is not None:
                eng.dispose()
            gc.collect()   # Windows：确保 sqlite 句柄释放后再删临时库
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
