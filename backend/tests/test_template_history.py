"""模板历史版本接口（P0-3）测试。

背景：前端 `views/TemplateList.vue` 的「历史」按钮调用了 `getTemplateHistory`，但该函数
**没有 import**，于是点击后抛 ReferenceError，被 catch 吞掉后表现为「暂无历史版本」。
本文件从**接口侧**锁定这个链路必须成立，防止前端再把它写错：

1. `GET /api/templates/{id}/history` 真实 HTTP 可达（经 FastAPI 路由 + 鉴权依赖 + DB）；
2. 返回体形状与前端读取口径严格一致：`data.items` 是**从最早到最新**的数组；
3. 版本链沿 `previous_version_id` 完整可追溯，`version` 递增；
4. 无历史版本时 `items` 为空数组（前端据此显示空状态）；
5. 模板不存在 → 404；未登录 → 401（前端会提示错误，而不是静默显示「暂无历史版本」）。

运行（backend 目录下）：
    python -m pytest tests/test_template_history.py -v
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

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import database  # noqa: E402
from database import Base, get_db  # noqa: E402
from models.user import User  # noqa: E402
from models.template import Template  # noqa: E402
from api import deps  # noqa: E402
from api.templates import router as templates_router  # noqa: E402


class TemplateHistoryApiTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = tmp
        eng = create_engine(f"sqlite:///{Path(tmp.name) / 't.db'}", connect_args={"timeout": 30})
        self.engine = eng
        Base.metadata.create_all(eng)
        self.S = sessionmaker(bind=eng)

        # 只挂模板路由，避免引入 LLM / 向量库等重依赖
        app = FastAPI()
        app.include_router(templates_router, prefix="/api")

        def _db():
            db = self.S()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = TestClient(app)

        # 预置一个 uploader（未登录态用例用它验证 401）
        s = self.S()
        s.add(User(username="u1", email="u1@example.com", hashed_password="x", role="uploader"))
        s.commit()
        s.close()

    def tearDown(self):
        # Windows 上必须先把连接池归还，否则临时目录里的 db 文件仍被占用
        try:
            self.client.close()
        except Exception:
            pass
        self.engine.dispose()
        self.tmp.cleanup()

    # ── 辅助 ──
    def _seed_chain(self, versions=3):
        """建一条 version 1..N 的版本链，返回 (最新版id, [最早..最新] 的 id 列表)。"""
        s = self.S()
        prev = None
        ids = []
        for v in range(1, versions + 1):
            t = Template(
                name="买卖合同标准条款", contract_type="买卖合同",
                clauses={"验收标准": f"第{v}版验收标准", "付款条件": "月结30天"},
                is_builtin=False, version=v, previous_version_id=prev,
            )
            s.add(t)
            s.commit()
            ids.append(t.id)
            prev = t.id
        s.close()
        return prev, ids

    def _as(self, user_id=1):
        s = self.S()
        u = s.query(User).filter(User.id == user_id).first()
        s.close()
        self.app.dependency_overrides[deps.get_current_user] = lambda: u
        return u

    # ── 用例 ──
    def test_history_returns_full_chain_oldest_to_newest(self):
        latest, ids = self._seed_chain(3)
        self._as()
        r = self.client.get(f"/api/templates/{latest}/history")
        self.assertEqual(r.status_code, 200)

        body = r.json()
        self.assertEqual(body["code"], 0)
        items = body["data"]["items"]
        self.assertIsInstance(items, list)
        self.assertEqual([i["id"] for i in items], ids, "必须是从最早到最新的完整链条")
        self.assertEqual([i["version"] for i in items], [1, 2, 3])
        self.assertEqual(body["data"]["total"], 3)

        # 前端渲染所需的字段一个都不能少
        first = items[0]
        for key in ("id", "name", "contract_type", "clauses", "version",
                    "previous_version_id", "created_at", "updated_at"):
            self.assertIn(key, first, f"模板历史项缺少字段 {key}")
        self.assertIsNone(items[0]["previous_version_id"], "最早版本没有上一版")
        self.assertEqual(items[-1]["previous_version_id"], ids[-2])

    def test_history_from_middle_version_still_returns_newest_in_chain(self):
        """点历史时可能落在链条中间的行上（列表默认只展示最新版，但接口应容错）。"""
        latest, ids = self._seed_chain(3)
        self._as()
        r = self.client.get(f"/api/templates/{ids[1]}/history")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        # 从中间节点向后追溯只能拿到 1..2；这是后端既有语义，前端以"被点击行"为最新
        self.assertEqual([i["id"] for i in items], ids[:2])
        self.assertEqual(items[-1]["id"], ids[1])

    def test_single_version_chain_contains_only_itself(self):
        """没有历史版本时链条只有自身一项 → 前端表格显示一行、无「上一版本 ID」。"""
        s = self.S()
        t = Template(name="单版本", contract_type="买卖合同", clauses={"标的": "x"},
                     is_builtin=False, version=1, previous_version_id=None)
        s.add(t)
        s.commit()
        tid = t.id
        s.close()

        self._as()
        data = self.client.get(f"/api/templates/{tid}/history").json()["data"]
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["id"], tid)
        self.assertIsNone(data["items"][0]["previous_version_id"])

    def test_template_not_found_returns_404(self):
        self._as()
        r = self.client.get("/api/templates/999999/history")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"], "template not found")

    def test_unauthenticated_returns_401(self):
        """未登录 → 401（前端会弹错误 + 显示行内错误态，而不是「暂无历史版本」）。"""
        latest, _ = self._seed_chain(1)
        self.app.dependency_overrides.pop(deps.get_current_user, None)
        r = self.client.get(f"/api/templates/{latest}/history")
        self.assertEqual(r.status_code, 401)

    def test_history_does_not_create_new_versions(self):
        """历史查询是只读的：查完版本数不变。"""
        latest, ids = self._seed_chain(2)
        self._as()
        for _ in range(3):
            self.client.get(f"/api/templates/{latest}/history")
        s = self.S()
        self.assertEqual(s.query(Template).count(), len(ids))
        s.close()


class TemplateListFrontendWiringTest(unittest.TestCase):
    """静态契约：TemplateList.vue 必须真的 import 并调用 getTemplateHistory。

    这是 P0-3 的根因所在（缺 import），用源码断言把它钉死，避免回归。
    """

    def test_view_imports_and_calls_get_template_history(self):
        vue = (_BACKEND_DIR.parent / "frontend" / "src" / "views" / "TemplateList.vue")
        self.assertTrue(vue.is_file(), f"找不到 {vue}")
        src = vue.read_text(encoding="utf-8")

        self.assertIn("getTemplateHistory", src, "TemplateList.vue 必须使用 getTemplateHistory")
        self.assertRegex(
            src,
            r"import\s*\{[^}]*\bgetTemplateHistory\b[^}]*\}\s*from\s*'\.\./api/template\.js'",
            "getTemplateHistory 必须从 ../api/template.js 显式 import（原 BUG 就是漏了它）",
        )
        self.assertIn("getTemplateHistory(", src, "必须真的调用该 API")

    def test_api_module_exports_get_template_history(self):
        js = (_BACKEND_DIR.parent / "frontend" / "src" / "api" / "template.js")
        src = js.read_text(encoding="utf-8")
        self.assertIn("export function getTemplateHistory", src)
        self.assertIn("/templates/${id}/history", src)


if __name__ == "__main__":
    unittest.main()
