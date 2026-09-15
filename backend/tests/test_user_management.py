"""用户角色管理（第二阶段）专项测试：一次性 bootstrap 建首个管理员 + 最小角色管理接口。

全部使用**临时 SQLite**，不碰 backend/contract.db。

覆盖：
一、Bootstrap（services/role_bootstrap.py）
  1. 无 admin + 指定已存在账号 → 提升为 admin（且不改密码）
  2. 已有 admin → 完全不动任何角色
  2b. 管理员被降权后重启 → bootstrap 不会把他强制恢复成 admin
  3. 指定用户名不存在 → 跳过且**不自动建号**
  4. 未配置环境变量 → 跳过
  6. 幂等：提升后再跑一次 → 跳过
二、用户管理接口（/api/auth/users）
  7. 仅 admin 可访问：uploader/reviewer/approver 一律 403（读 + 写）；未登录 401
  8. admin 改角色：uploader→reviewer/approver、reviewer→uploader 均 200
  9. 授予 admin：admin 可把他人提升为 admin，且新 admin 立刻具备权限
  10. 防锁死：唯一 admin 自我降级 403；两个 admin 时 A 降 B 允许，且不变量"≥1 admin"始终成立
  11. 非法角色值 422；不存在的用户 404
  12. 伪造 token 里的 role=admin 无效（后端按 DB 角色判权）

运行（backend 目录下）：
    python -m unittest tests.test_user_management -v
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

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database import Base, get_db  # noqa: E402
from models.user import User  # noqa: E402
from services.auth import hash_password, create_access_token  # noqa: E402
from services import role_bootstrap as RB  # noqa: E402
import main as app_main  # noqa: E402


def _temp_db(tmpdir):
    eng = create_engine(f"sqlite:///{Path(tmpdir) / 'um.db'}",
                        connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)


_PW_HASH = None


def _pw_hash():
    """测试账号共用同一口令，哈希只算一次（bcrypt 很慢，逐个算会让套件慢十几秒）。"""
    global _PW_HASH
    if _PW_HASH is None:
        _PW_HASH = hash_password("pw123456")
    return _PW_HASH


class TestRoleBootstrap(unittest.TestCase):
    """一次性部署初始化：建立第一个管理员。"""

    def setUp(self):
        # ignore_cleanup_errors：Windows 下 SQLite 文件句柄释放略滞后，避免 tearDown 报 PermissionError
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.eng, self.S = _temp_db(self.tmp.name)
        os.environ.pop(RB.ENV_VAR, None)

    def tearDown(self):
        os.environ.pop(RB.ENV_VAR, None)
        self.eng.dispose()
        self.tmp.cleanup()

    def _seed(self, with_admin=False):
        s = self.S()
        specs = [("alice", "uploader"), ("bob", "uploader"), ("carol", "reviewer")]
        if with_admin:
            specs.append(("root", "admin"))
        for n, r in specs:
            s.add(User(username=n, email=f"{n}@e.com", hashed_password=_pw_hash(), role=r))
        s.commit()
        s.close()

    def _roles(self):
        s = self.S()
        out = {u.username: u.role for u in s.query(User).all()}
        s.close()
        return out

    def _hashes(self):
        s = self.S()
        out = {u.username: u.hashed_password for u in s.query(User).all()}
        s.close()
        return out

    def test_1_promotes_existing_account_when_no_admin(self):
        self._seed()
        before_pw = self._hashes()
        os.environ[RB.ENV_VAR] = "alice"
        s = self.S()
        result = RB.bootstrap_admin(s)
        s.close()
        self.assertEqual(result, RB.PROMOTED)
        self.assertEqual(self._roles()["alice"], "admin")
        self.assertEqual(self._hashes(), before_pw, "bootstrap 不得修改任何密码")

    def test_2_does_nothing_when_admin_already_exists(self):
        self._seed(with_admin=True)
        os.environ[RB.ENV_VAR] = "alice"
        before = self._roles()
        s = self.S()
        result = RB.bootstrap_admin(s)
        s.close()
        self.assertEqual(result, RB.SKIPPED_ADMIN_EXISTS)
        self.assertEqual(self._roles(), before, "已有 admin 时不得改动任何角色")

    def test_2b_demoted_admin_is_not_restored_on_restart(self):
        """管理员被降权后重启：bootstrap 不会把他强制恢复成 admin。"""
        self._seed()
        os.environ[RB.ENV_VAR] = "alice"
        s = self.S()
        RB.bootstrap_admin(s)                      # alice -> admin
        s.close()

        # 模拟管理员在后台被降权，同时保证系统仍有 1 个 admin
        s = self.S()
        s.query(User).filter(User.username == "alice").first().role = "reviewer"
        s.query(User).filter(User.username == "bob").first().role = "admin"
        s.commit()
        s.close()

        # 再次"启动"执行 bootstrap
        s = self.S()
        result = RB.bootstrap_admin(s)
        s.close()
        self.assertEqual(result, RB.SKIPPED_ADMIN_EXISTS)
        self.assertEqual(self._roles()["alice"], "reviewer", "重启不得把已降权的账号恢复为 admin")

    def test_3_unknown_username_is_skipped_without_creating_account(self):
        self._seed()
        os.environ[RB.ENV_VAR] = "ghost"
        s = self.S()
        result = RB.bootstrap_admin(s)
        s.close()
        self.assertEqual(result, RB.SKIPPED_USER_NOT_FOUND)
        self.assertNotIn("ghost", self._roles(), "不得自动创建账号（会出现密码未知的管理员）")

    def test_4_not_configured_is_skipped(self):
        self._seed()
        before = self._roles()
        s = self.S()
        result = RB.bootstrap_admin(s)
        s.close()
        self.assertEqual(result, RB.SKIPPED_NOT_CONFIGURED)
        self.assertEqual(self._roles(), before)

    def test_6_idempotent_second_run_skips(self):
        self._seed()
        os.environ[RB.ENV_VAR] = "alice"
        s = self.S()
        first = RB.bootstrap_admin(s)
        s.close()
        before = self._roles()
        s = self.S()
        second = RB.bootstrap_admin(s)
        s.close()
        self.assertEqual(first, RB.PROMOTED)
        self.assertEqual(second, RB.SKIPPED_ADMIN_EXISTS)
        self.assertEqual(self._roles(), before)


class TestUserManagementApi(unittest.TestCase):
    """最小用户管理接口：仅 admin 可用 + 防锁死。"""

    def setUp(self):
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
        self.client = TestClient(app_main.app)

    def tearDown(self):
        app_main.app.dependency_overrides.clear()
        self.client.close()
        self.eng.dispose()
        self.tmp.cleanup()

    def _seed(self):
        s = self.S()
        specs = [("uploaderA", "uploader"), ("reviewerA", "reviewer"),
                 ("approverA", "approver"), ("adminA", "admin"), ("uploaderB", "uploader")]
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

    def _hdr(self, key, forge_role=None):
        u = self.U[key]
        tok = create_access_token({"sub": str(u["id"]), "username": u["username"],
                                   "role": forge_role or u["role"]})
        return {"Authorization": f"Bearer {tok}"}

    def _set_role(self, key, role):
        s = self.S()
        s.query(User).filter(User.username == key).first().role = role
        s.commit()
        s.close()
        self.U[key]["role"] = role

    def _db_role(self, key):
        s = self.S()
        r = s.query(User).filter(User.username == key).first().role
        s.close()
        return r

    def _admin_count(self):
        s = self.S()
        n = s.query(User).filter(User.role == "admin").count()
        s.close()
        return n

    # ── 7. 仅 admin 可访问 ────────────────────────────────────────
    def test_7_non_admin_cannot_access_user_management(self):
        for who in ("uploaderA", "reviewerA", "approverA"):
            self.assertEqual(self.client.get("/api/auth/users", headers=self._hdr(who)).status_code,
                             403, f"{who} 不应能查看用户列表")
            r = self.client.put(f"/api/auth/users/{self.U['uploaderB']['id']}/role",
                                json={"role": "admin"}, headers=self._hdr(who))
            self.assertEqual(r.status_code, 403, f"{who} 不应能修改角色")
        self.assertEqual(self.client.get("/api/auth/users").status_code, 401, "未登录应 401")

    # ── 8. admin 正常改角色 ──────────────────────────────────────
    def test_8_admin_can_change_roles(self):
        h = self._hdr("adminA")
        self.assertEqual(self.client.get("/api/auth/users", headers=h).status_code, 200)

        for target, new_role in [("uploaderB", "reviewer"), ("uploaderB", "approver"),
                                 ("reviewerA", "uploader")]:
            r = self.client.put(f"/api/auth/users/{self.U[target]['id']}/role",
                                json={"role": new_role}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self._db_role(target), new_role)

    # ── 9. 授予 admin ───────────────────────────────────────────
    def test_9_admin_can_grant_admin_and_new_admin_works(self):
        r = self.client.put(f"/api/auth/users/{self.U['uploaderB']['id']}/role",
                            json={"role": "admin"}, headers=self._hdr("adminA"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._db_role("uploaderB"), "admin")
        # 新 admin 立刻具备权限（权限按 DB 角色判定，与 token 里的 role 声明无关）
        self.assertEqual(self.client.get("/api/auth/users",
                                         headers=self._hdr("uploaderB")).status_code, 200)

    # ── 10. 防锁死 ──────────────────────────────────────────────
    def test_10a_sole_admin_cannot_self_demote(self):
        for role in ("uploader", "reviewer", "approver"):
            r = self.client.put(f"/api/auth/users/{self.U['adminA']['id']}/role",
                                json={"role": role}, headers=self._hdr("adminA"))
            self.assertEqual(r.status_code, 403, f"唯一 admin 自我降级为 {role} 必须被拒绝")
        self.assertEqual(self._db_role("adminA"), "admin")
        self.assertEqual(self._admin_count(), 1)

    def test_10b_two_admins_can_demote_the_other_and_invariant_holds(self):
        # 造两个 admin：adminA、uploaderB
        self._set_role("uploaderB", "admin")
        self.assertEqual(self._admin_count(), 2)

        # A 降 B → 允许（A 仍是 admin）
        r = self.client.put(f"/api/auth/users/{self.U['uploaderB']['id']}/role",
                            json={"role": "reviewer"}, headers=self._hdr("adminA"))
        self.assertEqual(r.status_code, 200, "A 仍是 admin，降级 B 应允许")
        self.assertEqual(self._db_role("uploaderB"), "reviewer")
        self.assertEqual(self._db_role("adminA"), "admin")
        self.assertEqual(self._admin_count(), 1, "不变量：改动后仍保留 1 个 admin")

        # 此时只剩 adminA 一个 admin，他再自我降级 → 必须拒绝
        r = self.client.put(f"/api/auth/users/{self.U['adminA']['id']}/role",
                            json={"role": "reviewer"}, headers=self._hdr("adminA"))
        self.assertEqual(r.status_code, 403, "不能把系统里最后一个 admin 降级")
        self.assertGreaterEqual(self._admin_count(), 1, "任何修改后必须至少保留 1 个 admin")

    # ── 11. 非法入参 ────────────────────────────────────────────
    def test_11_invalid_role_and_missing_user(self):
        h = self._hdr("adminA")
        r = self.client.put(f"/api/auth/users/{self.U['uploaderB']['id']}/role",
                            json={"role": "root"}, headers=h)
        self.assertEqual(r.status_code, 422, "非法角色值应被拒绝")
        r = self.client.put("/api/auth/users/999999/role", json={"role": "reviewer"}, headers=h)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self._db_role("uploaderB"), "uploader", "非法请求不得改动数据")

    # ── 12. 伪造 role 无效 ──────────────────────────────────────
    def test_12_forged_role_is_ignored(self):
        # token 里的 role 伪造成 admin（localStorage 里的 role 后端根本读不到）
        h = self._hdr("uploaderA", forge_role="admin")
        self.assertEqual(self.client.get("/api/auth/users", headers=h).status_code, 403,
                         "token 里伪造的 role 必须无效（后端按 DB 角色判权）")
        r = self.client.put(f"/api/auth/users/{self.U['uploaderB']['id']}/role",
                            json={"role": "admin"}, headers=self._hdr("uploaderA", forge_role="admin"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self._db_role("uploaderB"), "uploader")


if __name__ == "__main__":
    unittest.main(verbosity=2)
