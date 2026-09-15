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
import os
import sys
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

        同时把 lifespan 里另一个副作用 role_bootstrap.bootstrap_admin 打桩：
        本用例会真实执行 lifespan，若不隔离，而恰好 .env 配了 BOOTSTRAP_ADMIN_USERNAME，
        就会在**真实库**里提升账号。与 warmup 行为无关，纯测试隔离。
        """
        async def _run():
            with mock.patch.object(app_main.warmup_service, "start_warmup") as sw, \
                 mock.patch.object(app_main.role_bootstrap, "bootstrap_admin") as sb:
                async with app_main.lifespan(app_main.app):
                    pass
                sw.assert_called_once()
                sb.assert_called_once()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
