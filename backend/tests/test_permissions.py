"""多用户权限专项测试（临时 SQLite 库 + TestClient 真 HTTP，不碰生产 contract.db）。

覆盖本轮权限修复的全部要求：
① 普通用户隔离：uploader 只能读自己的，读他人 404
②③ reviewer / approver / admin 全局合同池查看能力保持
④ 注册提权：恶意 POST role=admin/reviewer/approver → 一律建成 uploader
⑤ 上传权限：uploader/admin 允许，reviewer/approver 403
⑥ 自审：reviewer 不得复核自己上传的合同（历史上传），他人合同允许
⑦ 自验：approver 不得验收自己上传的合同，他人合同允许
⑧ 交叉工作流：reviewer 不得 approve、approver 不得 review、uploader 两者皆不可
⑨ 权限边界在后端：伪造 token 里的 role 无效

运行（backend 目录下）：
    python -m unittest tests.test_permissions -v
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
from fastapi.testclient import TestClient  # noqa: E402

from database import Base, get_db  # noqa: E402
from models.user import User  # noqa: E402
from models.contract import Contract  # noqa: E402
from services.auth import hash_password, create_access_token  # noqa: E402
import main as app_main  # noqa: E402
import api.contracts as contracts  # noqa: E402


class TestPermissions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.eng = create_engine(f"sqlite:///{Path(cls.tmp.name) / 'perm.db'}",
                                connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(cls.eng)
        cls.S = sessionmaker(bind=cls.eng)
        cls._seed()

        def _override():
            db = cls.S()
            try:
                yield db
            finally:
                db.close()

        app_main.app.dependency_overrides[get_db] = _override
        cls.client = TestClient(app_main.app)

    @classmethod
    def tearDownClass(cls):
        app_main.app.dependency_overrides.clear()
        cls.client.close()
        cls.eng.dispose()
        cls.tmp.cleanup()

    @classmethod
    def _seed(cls):
        """建种子数据，并把需要的字段取成纯数据（避免 session 关闭后 ORM 对象 detached）。"""
        s = cls.S()
        specs = [("uploaderA", "uploader"), ("uploaderB", "uploader"),
                 ("reviewerA", "reviewer"), ("reviewerB", "reviewer"),
                 ("approverA", "approver"), ("adminA", "admin")]
        objs = {}
        for name, role in specs:
            u = User(username=name, email=f"{name}@e.com",
                     hashed_password=hash_password("pw123456"), role=role)
            s.add(u)
            objs[name] = u
        s.commit()
        for u in objs.values():
            s.refresh(u)
        cls.U = {n: {"id": u.id, "username": n, "role": r} for (n, r), u in zip(specs, objs.values())}

        def mk(owner, fname, status):
            c = Contract(user_id=cls.U[owner]["id"], file_name=fname, parsed_text="正文", status=status)
            s.add(c)
            return c

        contracts_ = {
            "A": mk("uploaderA", "A上传合同.docx", "completed"),
            "B": mk("uploaderB", "B上传合同.docx", "completed"),
            "revOwn": mk("reviewerA", "reviewerA历史上传.docx", "completed"),
            "appOwn": mk("approverA", "approverA历史上传.docx", "reviewed"),
            "appOther": mk("uploaderB", "B待验收.docx", "reviewed"),
        }
        s.commit()
        for c in contracts_.values():
            s.refresh(c)
        cls.ids = {k: c.id for k, c in contracts_.items()}
        s.close()

    def _hdr(self, user_key, forge_role=None):
        u = self.U[user_key]
        tok = create_access_token({"sub": str(u["id"]), "username": u["username"],
                                   "role": forge_role or u["role"]})
        return {"Authorization": f"Bearer {tok}"}

    # ── ④ 注册提权 ────────────────────────────────────────────────
    def test_register_role_cannot_be_self_selected(self):
        for i, evil in enumerate(["uploader", "reviewer", "approver", "admin"]):
            name = f"newbie{i}"
            r = self.client.post("/api/auth/register", json={
                "username": name, "email": f"{name}@e.com", "password": "pw123456", "role": evil,
            })
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["data"]["user"]["role"], "uploader",
                             f"客户端传 role={evil} 竟然建成 {r.json()['data']['user']['role']}")
            s = self.S()
            db_role = s.query(User).filter(User.username == name).first().role
            s.close()
            self.assertEqual(db_role, "uploader", f"DB 里 role={evil} 未被强制为 uploader")

    # ── ① 普通用户隔离 ─────────────────────────────────────────────
    def test_uploader_isolation(self):
        self.assertEqual(self.client.get(f"/api/contracts/{self.ids['A']}",
                                         headers=self._hdr("uploaderA")).status_code, 200)
        self.assertEqual(self.client.get(f"/api/contracts/{self.ids['B']}",
                                         headers=self._hdr("uploaderA")).status_code, 404)
        self.assertEqual(self.client.get(f"/api/contracts/{self.ids['B']}",
                                         headers=self._hdr("uploaderB")).status_code, 200)
        self.assertEqual(self.client.get(f"/api/contracts/{self.ids['A']}",
                                         headers=self._hdr("uploaderB")).status_code, 404)

        r = self.client.get("/api/contracts", headers=self._hdr("uploaderA"))
        names = [i["file_name"] for i in r.json()["data"]["items"]]
        self.assertIn("A上传合同.docx", names)
        self.assertNotIn("B上传合同.docx", names)

    # ── ②③ 工作流角色全局可见 ──────────────────────────────────────
    def test_workflow_roles_keep_global_visibility(self):
        for who in ("reviewerA", "reviewerB", "approverA", "adminA"):
            for label in ("A", "B"):
                r = self.client.get(f"/api/contracts/{self.ids[label]}", headers=self._hdr(who))
                self.assertEqual(r.status_code, 200,
                                 f"{who} 读 {label} 的合同应可读，实际 {r.status_code}")

    # ── ⑤ 上传权限 ────────────────────────────────────────────────
    def _upload(self, user_key, forge_role=None):
        with mock.patch.object(contracts, "UPLOAD_DIR", self.tmp.name), \
             mock.patch.object(contracts, "detect_and_parse", return_value={"full_text": "合同正文"}), \
             mock.patch.object(contracts, "classify_contract",
                               return_value={"contract_type": "买卖合同", "confidence": 0.9,
                                             "is_outsourcing": False}), \
             mock.patch.object(contracts, "extract_elements", return_value={}):
            return self.client.post(
                "/api/contracts/upload",
                files={"file": ("t.docx", b"fake-docx-bytes", "application/octet-stream")},
                headers=self._hdr(user_key, forge_role))

    def test_upload_permission(self):
        self.assertEqual(self._upload("uploaderA").status_code, 200, "uploader 应可上传")
        self.assertEqual(self._upload("adminA").status_code, 200, "admin 按既有规则保留上传能力")
        self.assertEqual(self._upload("reviewerA").status_code, 403, "reviewer 不应能上传")
        self.assertEqual(self._upload("approverA").status_code, 403, "approver 不应能上传")

    # ── ⑥ 自审 ────────────────────────────────────────────────────
    def test_reviewer_cannot_review_own_contract(self):
        r = self.client.post(f"/api/contracts/{self.ids['revOwn']}/review?action=approve",
                             headers=self._hdr("reviewerA"))
        self.assertEqual(r.status_code, 403)
        self.assertIn("不能复核自己上传的合同", r.json()["detail"])

    def test_reviewer_can_review_others_contract(self):
        r = self.client.post(f"/api/contracts/{self.ids['A']}/review?action=approve",
                             headers=self._hdr("reviewerA"))
        self.assertEqual(r.status_code, 200, r.text)

    # ── ⑦ 自验 ────────────────────────────────────────────────────
    def test_approver_cannot_approve_own_contract(self):
        r = self.client.post(f"/api/contracts/{self.ids['appOwn']}/approve",
                             headers=self._hdr("approverA"))
        self.assertEqual(r.status_code, 403)
        self.assertIn("不能验收自己上传的合同", r.json()["detail"])

    def test_approver_can_approve_others_contract(self):
        r = self.client.post(f"/api/contracts/{self.ids['appOther']}/approve",
                             headers=self._hdr("approverA"))
        self.assertEqual(r.status_code, 200, r.text)

    # ── ⑧ 交叉工作流 ──────────────────────────────────────────────
    def test_cross_workflow_role_separation(self):
        target = self.ids["B"]
        self.assertEqual(self.client.post(f"/api/contracts/{target}/approve",
                                          headers=self._hdr("reviewerA")).status_code, 403)
        self.assertEqual(self.client.post(f"/api/contracts/{target}/review",
                                          headers=self._hdr("approverA")).status_code, 403)
        self.assertEqual(self.client.post(f"/api/contracts/{target}/review",
                                          headers=self._hdr("uploaderA")).status_code, 403)
        self.assertEqual(self.client.post(f"/api/contracts/{target}/approve",
                                          headers=self._hdr("uploaderA")).status_code, 403)
        self.assertEqual(self.client.post(f"/api/contracts/{target}/review").status_code, 401)
        self.assertEqual(self.client.post(f"/api/contracts/{target}/approve").status_code, 401)

    # ── ⑨ 权限边界在后端：伪造 token role 无效 ─────────────────────
    def test_forged_token_role_is_ignored(self):
        r = self.client.get(f"/api/contracts/{self.ids['B']}",
                            headers=self._hdr("uploaderA", forge_role="admin"))
        self.assertEqual(r.status_code, 404, "后端必须按 DB 角色判权，token 里的 role 不可信")
        r = self.client.post(
            "/api/contracts/upload",
            files={"file": ("t.docx", b"x", "application/octet-stream")},
            headers=self._hdr("reviewerA", forge_role="uploader"))
        self.assertEqual(r.status_code, 403, "伪造 uploader 的 token 不应绕过上传限制")


if __name__ == "__main__":
    unittest.main(verbosity=2)
