"""用户个人 DeepSeek Key 专项测试（C-008）。

覆盖：
  1. 个人 Key 优先于 .env 系统默认 Key
  2. 无个人 Key → 回退 .env 默认 Key
  3. 两者都没有 → 明确配置错误（提示去「个人信息」配置，且不含 Key 本身）
  4. 用户隔离：A 用 A 的、B 用 B 的
  5. 并发/交错调用：多线程各自绑定，绝不串 Key（含真实 chat() 走 per-context client）
  6. 删除个人 Key → 自动回退默认 Key
  7. API 脱敏：profile / users 均不返回完整 Key
  8. 中间件真实绑定：带 token 的 HTTP 请求会绑定该用户的 Key（无个人 Key 则绑定 None）
  9. 加密存储：库里不是明文；前端不再把真实 Key 存 localStorage

全部使用临时 SQLite，不碰 backend/contract.db；不发起任何真实网络调用。

运行（backend 目录下）：
    python -m unittest tests.test_user_api_key -v
"""
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database import Base, get_db  # noqa: E402
from models.user import User  # noqa: E402
from services.auth import hash_password, create_access_token  # noqa: E402
from services import user_secret  # noqa: E402
from ai import llm_client, llm_context  # noqa: E402
import main as app_main  # noqa: E402

ENV_KEY = "sk-env-default-key-000000000000"
KEY_A = "sk-user-A-key-111111111111"
KEY_B = "sk-user-B-key-222222222222"


def _temp_db(tmpdir):
    eng = create_engine(f"sqlite:///{Path(tmpdir) / 'k.db'}",
                        connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)


_PW_HASH = None


def _pw_hash():
    global _PW_HASH
    if _PW_HASH is None:
        _PW_HASH = hash_password("pw123456")
    return _PW_HASH


class KeyResolutionBase(unittest.TestCase):
    """Key 解析（不涉及 HTTP）的公共脚手架。"""

    def setUp(self):
        self._orig_default = llm_client.DEFAULT_API_KEY
        self._orig_clients = dict(llm_client._clients)
        llm_client._clients.clear()

    def tearDown(self):
        llm_client.DEFAULT_API_KEY = self._orig_default
        llm_client._clients.clear()
        llm_client._clients.update(self._orig_clients)
        llm_context.reset_user_api_key(llm_context.set_user_api_key(None))

    def _with_user_key(self, key):
        return llm_context.set_user_api_key(key)


class TestKeyResolution(KeyResolutionBase):

    def test_1_user_key_wins_over_env(self):
        llm_client.DEFAULT_API_KEY = ENV_KEY
        tok = self._with_user_key(KEY_A)
        try:
            key, source = llm_client.resolve_api_key()
            self.assertEqual((key, source), (KEY_A, "user"), "有个人 Key 时必须用个人 Key")
        finally:
            llm_context.reset_user_api_key(tok)

    def test_2_fallback_to_env_when_no_user_key(self):
        llm_client.DEFAULT_API_KEY = ENV_KEY
        tok = self._with_user_key(None)
        try:
            self.assertEqual(llm_client.resolve_api_key(), (ENV_KEY, "env"))
        finally:
            llm_context.reset_user_api_key(tok)

    def test_2b_blank_user_key_falls_back_to_env(self):
        """个人 Key 为空白串时也应视为未配置 → 回退默认。"""
        llm_client.DEFAULT_API_KEY = ENV_KEY
        tok = self._with_user_key("   ")
        try:
            self.assertEqual(llm_client.resolve_api_key(), (ENV_KEY, "env"))
        finally:
            llm_context.reset_user_api_key(tok)

    def test_3_no_key_at_all_raises_clear_error(self):
        llm_client.DEFAULT_API_KEY = ""
        tok = self._with_user_key(None)
        try:
            self.assertEqual(llm_client.resolve_api_key(), (None, "none"))
            with self.assertRaises(llm_client.LLMConfigError) as ctx:
                llm_client.require_api_key()
            msg = str(ctx.exception)
            self.assertIn("个人信息", msg, "报错要指引用户去个人信息页配置")
            self.assertIn("DEEPSEEK_API_KEY", msg, "报错要指出系统默认 Key 的变量名")
            self.assertNotIn(ENV_KEY, msg)
            self.assertNotIn(KEY_A, msg)
        finally:
            llm_context.reset_user_api_key(tok)

    def test_3b_chat_raises_before_any_network_call(self):
        """没有任何 Key 时，chat() 必须直接抛明确错误，而不是把空 Key 发出去。"""
        llm_client.DEFAULT_API_KEY = ""
        tok = self._with_user_key(None)
        try:
            with mock.patch.object(llm_client, "OpenAI") as fake:
                with self.assertRaises(llm_client.LLMConfigError):
                    llm_client.llm_client.chat("hi")
                fake.assert_not_called()
        finally:
            llm_context.reset_user_api_key(tok)

    def test_4_two_users_are_isolated(self):
        llm_client.DEFAULT_API_KEY = ENV_KEY
        t1 = llm_context.set_user_api_key(KEY_A)
        try:
            self.assertEqual(llm_client.resolve_api_key()[0], KEY_A)
        finally:
            llm_context.reset_user_api_key(t1)
        t2 = llm_context.set_user_api_key(KEY_B)
        try:
            self.assertEqual(llm_client.resolve_api_key()[0], KEY_B)
        finally:
            llm_context.reset_user_api_key(t2)


class TestConcurrencyIsolation(KeyResolutionBase):
    """本轮最关键：并发/交错调用绝不串 Key。"""

    def test_5_interleaved_resolution_never_crosses(self):
        llm_client.DEFAULT_API_KEY = ENV_KEY
        keys = [f"sk-user-{i}-{'x' * 20}" for i in range(4)]
        errors = []
        start = threading.Barrier(len(keys))

        def worker(k):
            tok = llm_context.set_user_api_key(k)
            try:
                start.wait(timeout=15)
                for _ in range(200):
                    got, src = llm_client.resolve_api_key()
                    if got != k or src != "user":
                        errors.append((k, got, src))
                        return
            finally:
                llm_context.reset_user_api_key(tok)

        ts = [threading.Thread(target=worker, args=(k,)) for k in keys]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        self.assertEqual(errors, [], f"出现串 Key：{errors}")

    def test_5b_chat_uses_per_context_client(self):
        """并发调用 chat()：每个线程拿到的是「自己 Key 建的 client」，不共享 singleton 状态。

        做法：把 OpenAI 打桩成「返回结果就是构造它的那个 api_key」，于是 chat() 的返回值
        直接暴露了本次实际使用的 Key。
        """
        llm_client.DEFAULT_API_KEY = ENV_KEY
        created = []

        def factory(*, api_key=None, **kwargs):
            created.append(api_key)
            client = mock.MagicMock()
            client.chat.completions.create.return_value = types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=api_key))]
            )
            return client

        keys = [KEY_A, KEY_B, None]      # None → 该线程应回退到默认 Key
        results = {}
        errors = []
        start = threading.Barrier(len(keys))

        def worker(label, k):
            tok = llm_context.set_user_api_key(k)
            try:
                start.wait(timeout=15)
                for _ in range(30):
                    used = llm_client.llm_client.chat("probe")
                    want = k if k else ENV_KEY
                    if used != want:
                        errors.append((label, want, used))
                        return
                results[label] = used
            finally:
                llm_context.reset_user_api_key(tok)

        with mock.patch.object(llm_client, "OpenAI", side_effect=factory):
            ts = [threading.Thread(target=worker, args=(f"t{i}", k)) for i, k in enumerate(keys)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=60)

        self.assertEqual(errors, [], f"并发下出现 Key 串用：{errors}")
        self.assertEqual(results.get("t0"), KEY_A)
        self.assertEqual(results.get("t1"), KEY_B)
        self.assertEqual(results.get("t2"), ENV_KEY)


class TestUserKeyApi(KeyResolutionBase):
    """走路由真 HTTP：保存 / 查询 / 删除 / 脱敏 / 中间件绑定。"""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.eng, self.S = _temp_db(self.tmp.name)
        self._seed()

        def _override():
            db = self.S()
            try:
                yield db
            finally:
                db.close()

        app_main.app.dependency_overrides[get_db] = _override
        # 中间件直接用 SessionLocal() 查用户 Key，必须也指向测试库（否则会去查真实 contract.db）
        self._sl_patch = mock.patch.object(app_main, "SessionLocal", self.S)
        self._sl_patch.start()
        self.client = TestClient(app_main.app)

    def tearDown(self):
        app_main.app.dependency_overrides.clear()
        self.client.close()
        self._sl_patch.stop()
        self.eng.dispose()
        self.tmp.cleanup()
        super().tearDown()

    def _seed(self):
        s = self.S()
        specs = [("userA", "uploader"), ("userB", "uploader"), ("adminA", "admin")]
        objs = []
        for n, r in specs:
            u = User(username=n, email=f"{n}@e.com", hashed_password=_pw_hash(), role=r)
            s.add(u)
            objs.append(u)
        s.commit()
        for u in objs:
            s.refresh(u)
        self.U = {n: {"id": u.id, "username": n, "role": r} for (n, r), u in zip(specs, objs)}
        s.close()

    def _hdr(self, key):
        u = self.U[key]
        tok = create_access_token({"sub": str(u["id"]), "username": u["username"], "role": u["role"]})
        return {"Authorization": f"Bearer {tok}"}

    def _db_raw(self, user_key):
        """直接读库里的密文字段，用于验证「不是明文」。"""
        s = self.S()
        row = s.query(User.deepseek_api_key_enc).filter(User.id == self.U[user_key]["id"]).first()
        s.close()
        return row[0] if row else None

    # ── 6 + 7：保存 / 查询 / 删除 / 脱敏 ─────────────────────────────
    def test_6_7_save_query_delete_and_no_echo(self):
        h = self._hdr("userA")

        # 初始：未配置
        r = self.client.get("/api/auth/profile", headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["data"]["deepseek_key_configured"])

        # 保存
        r = self.client.put("/api/auth/profile/deepseek-key", json={"api_key": KEY_A}, headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["data"]["deepseek_key_configured"])

        # 查询：只回布尔，绝不回显 Key（响应全文里不能出现 Key）
        r = self.client.get("/api/auth/profile", headers=h)
        self.assertTrue(r.json()["data"]["deepseek_key_configured"])
        self.assertNotIn(KEY_A, r.text, "profile 接口不得回显 Key")

        # 存的是密文，不是明文
        enc = self._db_raw("userA")
        self.assertTrue(enc and KEY_A not in enc, "库里不应出现明文 Key")
        self.assertEqual(user_secret.decrypt_api_key(enc), KEY_A)

        # admin 的用户列表：只能看到「已配置」，看不到 Key
        r = self.client.get("/api/auth/users", headers=self._hdr("adminA"))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(KEY_A, r.text, "用户列表不得回显 Key")
        row_a = [i for i in r.json()["data"]["items"] if i["username"] == "userA"][0]
        self.assertTrue(row_a["deepseek_key_configured"])
        self.assertNotIn("deepseek_api_key_enc", row_a, "不得把密文字段也暴露出去")

        # 删除 → 回退默认
        r = self.client.delete("/api/auth/profile/deepseek-key", headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["data"]["deepseek_key_configured"])
        self.assertIsNone(self._db_raw("userA"))
        r = self.client.get("/api/auth/profile", headers=h)
        self.assertFalse(r.json()["data"]["deepseek_key_configured"])

    def test_6b_delete_restores_env_fallback(self):
        h = self._hdr("userA")
        self.client.put("/api/auth/profile/deepseek-key", json={"api_key": KEY_A}, headers=h)
        s = self.S()
        self.assertEqual(user_secret.get_user_api_key(s, self.U["userA"]["id"]), KEY_A)
        s.close()

        self.client.delete("/api/auth/profile/deepseek-key", headers=h)
        s = self.S()
        self.assertIsNone(user_secret.get_user_api_key(s, self.U["userA"]["id"]))
        s.close()

        # 该用户已无个人 Key → 解析应回退默认 Key
        llm_client.DEFAULT_API_KEY = ENV_KEY
        tok = llm_context.set_user_api_key(None)
        try:
            self.assertEqual(llm_client.resolve_api_key(), (ENV_KEY, "env"))
        finally:
            llm_context.reset_user_api_key(tok)

    def test_7b_only_self_can_set_and_other_user_unaffected(self):
        self.client.put("/api/auth/profile/deepseek-key", json={"api_key": KEY_A},
                        headers=self._hdr("userA"))
        # userB 未配置 → 仍是未配置；A 的 Key 不会"漏"给 B
        r = self.client.get("/api/auth/profile", headers=self._hdr("userB"))
        self.assertFalse(r.json()["data"]["deepseek_key_configured"])
        self.assertIsNone(self._db_raw("userB"))

        # B 自己保存后，两者各自独立
        self.client.put("/api/auth/profile/deepseek-key", json={"api_key": KEY_B},
                        headers=self._hdr("userB"))
        self.assertEqual(user_secret.decrypt_api_key(self._db_raw("userA")), KEY_A)
        self.assertEqual(user_secret.decrypt_api_key(self._db_raw("userB")), KEY_B)

    def test_7c_unauthenticated_cannot_touch_profile(self):
        self.assertEqual(self.client.get("/api/auth/profile").status_code, 401)
        r = self.client.put("/api/auth/profile/deepseek-key", json={"api_key": KEY_A})
        self.assertEqual(r.status_code, 401)

    # ── 8：中间件真实绑定 ─────────────────────────────────────────
    def test_8_middleware_binds_request_user_key(self):
        self.client.put("/api/auth/profile/deepseek-key", json={"api_key": KEY_A},
                        headers=self._hdr("userA"))
        bound = []

        def recorder(value):
            bound.append(value)
            return llm_context.set_user_api_key(value)

        with mock.patch.object(app_main, "set_user_api_key", side_effect=recorder):
            self.client.get("/api/auth/profile", headers=self._hdr("userA"))
            self.client.get("/api/auth/profile", headers=self._hdr("userB"))
            self.client.get("/api/auth/profile")            # 未登录 → 不绑定

        self.assertIn(KEY_A, bound, "带 userA token 的请求应绑定 userA 的个人 Key")
        self.assertIn(None, bound, "userB 未配置个人 Key 时应绑定 None（→ 回退默认 Key）")


class TestNoPlaintextInFrontend(unittest.TestCase):
    """前端不得再把真实 Key 存 localStorage / 硬编码。"""

    def test_9_frontend_no_local_key_storage(self):
        fe = _ROOT / "frontend" / "src"
        offenders = []
        for p in fe.rglob("*.vue"):
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "localStorage.setItem('deepseek_api_key'" in text or 'localStorage.setItem("deepseek_api_key"' in text:
                offenders.append(str(p.relative_to(_ROOT)))
        self.assertEqual(offenders, [], f"前端不应再把真实 Key 存 localStorage：{offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
