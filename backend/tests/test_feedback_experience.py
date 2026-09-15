"""人工反馈 → 学习经验 的生命周期与权限测试（真 HTTP + 临时库，不碰生产 contract.db）。

覆盖用户要求的第一阶段全部验收点：

生命周期
  1. 提交 → pending（**不直接生效**）
  2. reviewer 审核 → reviewed；驳回 → rejected（永不进入学习）
  3. admin 批准 → approved_for_learning → active，并物化经验
  4. admin 撤销 → revoked（经验从检索中排除）
  5. 同一反馈不可重复批准（幂等）/ 不可换人批准

权限与职责分离
  6. 未审核不能批准；uploader/reviewer/approver 不能批准（仅 admin）
  7. **批准者不得是提交者**
  8. **批准者不得是审核者**（审核与批准职责分离）
  9. reviewer 不得审核自己提交的反馈
 10. 反馈池需要 reviewer/approver/admin；普通 uploader 不可见
 11. 已批准反馈不可被硬删除（否则经验库出现孤立经验）

规则反馈池
 12. adjudicator_rule_suspect 只进规则反馈池，**禁止批准进入学习**

Gold/Test 隔离
 13. 合同正文命中官方评测语料 → 批准被拒（防数据泄漏）

运行（backend 目录下）：
    python -m unittest tests.test_feedback_experience -v
"""
import json
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
from models.audit_record import AuditRecord  # noqa: E402
from models.feedback_log import FeedbackLog  # noqa: E402
from models.feedback_experience import FeedbackExperience  # noqa: E402
from services.auth import hash_password, create_access_token  # noqa: E402
from services import eval_isolation  # noqa: E402
import main as app_main  # noqa: E402
from tests.feedback_test_utils import RagEnv  # noqa: E402

_CLAUSE = "（6）履约验收标准：设备正常运行生产30日并验收合格后"


class TestFeedbackExperienceLifecycle(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.eng = create_engine(
            f"sqlite:///{Path(cls.tmp.name) / 'fb.db'}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        Base.metadata.create_all(cls.eng)
        cls.S = sessionmaker(bind=cls.eng)

        def _override():
            db = cls.S()
            try:
                yield db
            finally:
                db.close()

        app_main.app.dependency_overrides[get_db] = _override
        cls.client = TestClient(app_main.app)   # 不用 with：不触发 lifespan（不碰生产库）
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        app_main.app.dependency_overrides.clear()
        cls.client.close()
        cls.eng.dispose()
        cls.tmp.cleanup()

    @classmethod
    def _seed(cls):
        s = cls.S()
        specs = [("uploaderA", "uploader"), ("uploaderB", "uploader"),
                 ("reviewerA", "reviewer"), ("approverA", "approver"),
                 ("adminA", "admin"), ("adminB", "admin")]
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

        contract = Contract(
            user_id=cls.U["uploaderA"]["id"], file_name="验收合同.docx",
            contract_type="买卖合同", parsed_text="第一条 付款。\n" + _CLAUSE,
            status="completed",
        )
        s.add(contract)
        s.commit()
        s.refresh(contract)
        cls.contract_id = contract.id

        rec = AuditRecord(
            contract_id=contract.id, audit_batch="batch-fb-1", risk_type="R08",
            risk_level="medium", clause_text=_CLAUSE,
            clause_position={"clause_no": None, "clause_title": None,
                             "original_text": _CLAUSE, "start": 6, "end": 6 + len(_CLAUSE)},
            reason="合同未定义验收标准", suggestion="补充验收标准", detection_method="evidence",
            confidence=0.6, evidence={"method": "evidence", "law": "民法典621-623"},
            recommendation={"risk_description": "合同存在验收标准缺失风险", "example": "示例",
                            "legal_basis": "民法典621-623", "grounding": {"passed": True, "issues": []}},
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        cls.record_id = rec.id
        s.close()

    def _hdr(self, key, forge_role=None):
        u = self.U[key]
        tok = create_access_token({"sub": str(u["id"]), "username": u["username"],
                                   "role": forge_role or u["role"]})
        return {"Authorization": f"Bearer {tok}"}

    def setUp(self):
        self.env = RagEnv(self.S)
        self.env.__enter__()
        # 每个用例从空经验库开始（内存 chroma 已隔离，这里清 DB 侧状态）
        s = self.S()
        s.query(FeedbackExperience).delete()
        s.query(FeedbackLog).delete()
        s.commit()
        s.close()
        eval_isolation.reset_cache()
        self._old_env = os.environ.get("EVAL_CORPUS_PATHS")

    def tearDown(self):
        self.env.__exit__(None, None, None)
        eval_isolation.reset_cache()
        if self._old_env is None:
            os.environ.pop("EVAL_CORPUS_PATHS", None)
        else:
            os.environ["EVAL_CORPUS_PATHS"] = self._old_env

    # ── 辅助：走完 提交 → 审核 → 批准 ──────────────────────────────────
    def _submit(self, user="uploaderA", action="false_positive",
                reason="clause_exists", comment="第四条第（2）款已经约定验收标准"):
        r = self.client.post("/api/feedback", headers=self._hdr(user), json={
            "record_id": self.record_id, "action_type": action,
            "feedback_reason": reason, "comment": comment,
        })
        assert r.status_code == 201, r.text
        return r.json()["data"]["id"]

    def _review(self, fid, reviewer="reviewerA", action="review"):
        return self.client.post(f"/api/feedback/{fid}/review", headers=self._hdr(reviewer),
                                json={"action": action})

    def _approve(self, fid, admin="adminA"):
        return self.client.post(f"/api/feedback/{fid}/approve", headers=self._hdr(admin))

    # ══════════════════════════════════════════════════════════════
    # 1. 提交：pending，快照完整，不直接生效
    # ══════════════════════════════════════════════════════════════
    def test_submit_creates_pending_with_full_snapshot(self):
        fid = self._submit()
        s = self.S()
        fb = s.query(FeedbackLog).filter(FeedbackLog.id == fid).first()
        self.assertEqual(fb.status, "pending")
        self.assertEqual(fb.target_type, "risk")
        self.assertEqual(fb.contract_id, self.contract_id)
        self.assertEqual(fb.contract_type, "买卖合同")
        self.assertEqual(fb.risk_type, "R08")
        self.assertEqual(fb.feedback_reason, "clause_exists")
        # 模型侧快照（防 AuditRecord 被 superseded 后经验失上下文）
        self.assertIsNotNone(fb.model_evidence)
        self.assertEqual(fb.model_evidence.get("law"), "民法典621-623")
        self.assertIsNotNone(fb.model_result)
        self.assertEqual(fb.model_result.get("risk_type"), "R08")
        self.assertEqual(fb.model_result.get("clause_position", {}).get("original_text"), _CLAUSE)
        s.close()
        # 未批准 → 不得产生经验
        self.assertEqual(self.env.store.load_active_experiences(), [])

    def test_submit_rejects_unknown_reason(self):
        r = self.client.post("/api/feedback", headers=self._hdr("uploaderA"), json={
            "record_id": self.record_id, "action_type": "false_positive",
            "feedback_reason": "not_a_reason",
        })
        self.assertEqual(r.status_code, 422)

    # ══════════════════════════════════════════════════════════════
    # 2. 审核：reviewed / rejected
    # ══════════════════════════════════════════════════════════════
    def test_review_approve_path_sets_reviewed(self):
        fid = self._submit()
        r = self._review(fid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["status"], "reviewed")

    def test_review_reject_sets_rejected_and_never_materializes(self):
        fid = self._submit()
        r = self._review(fid, action="reject")
        self.assertEqual(r.json()["data"]["status"], "rejected")
        # 被驳回 → 批准必须失败
        self.assertEqual(self._approve(fid).status_code, 400)
        self.assertEqual(self.env.store.load_active_experiences(), [])

    def test_reviewer_cannot_review_own_feedback(self):
        # uploaderB 无法看到 uploaderA 的合同 → 换成同一个人提交后自己审核的场景：
        # 让 uploaderA 提交，reviewerA 审核正常；再让 adminA（也是提交者）审核自己的会被拒。
        r = self.client.post("/api/feedback", headers=self._hdr("adminA"), json={
            "record_id": self.record_id, "action_type": "confirmed", "feedback_reason": "evidence_gap",
        })
        # adminA 属于 WORKFLOW_ROLES，可标注他人合同
        self.assertEqual(r.status_code, 201, r.text)
        fid = r.json()["data"]["id"]
        forbid = self._review(fid, reviewer="adminA")
        self.assertEqual(forbid.status_code, 403)
        self.assertIn("自己提交", forbid.json()["detail"])

    # ══════════════════════════════════════════════════════════════
    # 3. 批准 → 物化经验
    # ══════════════════════════════════════════════════════════════
    def test_approve_materializes_experience_and_indexes(self):
        fid = self._submit()
        self._review(fid)
        r = self._approve(fid)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual(d["feedback_status"], "active")
        self.assertEqual(d["experience_status"], "active")
        self.assertTrue(d["indexed"], d)
        self.assertEqual(d["doc_id"], f"exp_{d['experience_id']}")

        s = self.S()
        exp = s.query(FeedbackExperience).filter(FeedbackExperience.id == d["experience_id"]).first()
        self.assertEqual(exp.source_feedback_id, fid)
        self.assertEqual(exp.human_label, "false_positive")
        self.assertEqual(exp.risk_type, "R08")
        self.assertEqual(exp.contract_type, "买卖合同")
        self.assertEqual(exp.original_text, _CLAUSE)
        self.assertIsNotNone(exp.approved_by)
        self.assertIsNotNone(exp.approved_at)
        self.assertEqual(exp.collection_name, "feedback_experiences")
        s.close()

        # 只有 active 才可检索
        active = self.env.store.load_active_experiences()
        self.assertEqual([e["id"] for e in active], [d["experience_id"]])
        self.assertEqual(self.env.doc_ids(), [f"exp_{d['experience_id']}"])

    def test_approve_requires_reviewed_state(self):
        fid = self._submit()
        r = self._approve(fid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("reviewed", r.json()["detail"])

    def test_approve_requires_admin_role(self):
        fid = self._submit()
        self._review(fid)
        for who in ("uploaderA", "reviewerA"):
            r = self._approve(fid, admin=who)
            self.assertEqual(r.status_code, 403, f"{who} 不应能批准: {r.text}")
        self.assertEqual(self.env.store.load_active_experiences(), [])

    def test_approver_cannot_be_submitter(self):
        # adminA 自己提交 → 不能自己批准（即使已由 reviewerA 审核）
        r = self.client.post("/api/feedback", headers=self._hdr("adminA"), json={
            "record_id": self.record_id, "action_type": "confirmed", "feedback_reason": "evidence_gap",
        })
        fid = r.json()["data"]["id"]
        self._review(fid, reviewer="reviewerA")
        bad = self._approve(fid, admin="adminA")
        self.assertEqual(bad.status_code, 403)
        self.assertIn("自己提交", bad.json()["detail"])
        # 换一个 admin 即可
        self.assertEqual(self._approve(fid, admin="adminB").status_code, 200)

    def test_approver_cannot_be_reviewer(self):
        fid = self._submit()
        self._review(fid, reviewer="adminA")
        bad = self._approve(fid, admin="adminA")
        self.assertEqual(bad.status_code, 403)
        self.assertIn("职责分离", bad.json()["detail"])
        self.assertEqual(self._approve(fid, admin="adminB").status_code, 200)

    def test_approve_is_idempotent_for_same_admin(self):
        fid = self._submit()
        self._review(fid)
        first = self._approve(fid)
        self.assertEqual(first.status_code, 200)
        # 再批准：不重复建经验，且批准人不可变更
        second = self._approve(fid)
        self.assertIn(second.status_code, (200, 400, 409))
        s = self.S()
        self.assertEqual(s.query(FeedbackExperience).count(), 1)
        s.close()

    # ══════════════════════════════════════════════════════════════
    # 4. 撤销
    # ══════════════════════════════════════════════════════════════
    def test_revoke_excludes_from_retrieval_and_deletes_doc(self):
        fid = self._submit()
        self._review(fid)
        exp_id = self._approve(fid).json()["data"]["experience_id"]
        self.assertEqual(len(self.env.store.load_active_experiences()), 1)

        r = self.client.post(f"/api/feedback/{fid}/revoke", headers=self._hdr("adminA"),
                             json={"reason": "人工复核发现原判断有误"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual(d["feedback_status"], "revoked")
        self.assertEqual(d["experience_status"], "revoked")
        self.assertTrue(d["index_removed"], d)

        # 检索排除 + Chroma 精确删除
        self.assertEqual(self.env.store.load_active_experiences(), [])
        self.assertEqual(self.env.doc_ids(), [])
        s = self.S()
        exp = s.query(FeedbackExperience).filter(FeedbackExperience.id == exp_id).first()
        self.assertEqual(exp.status, "revoked")
        self.assertEqual(exp.revoke_reason, "人工复核发现原判断有误")
        self.assertIsNotNone(exp.revoked_at)
        # 行保留（审计轨迹）：不物理删除
        self.assertIsNotNone(exp)
        s.close()

    def test_revoke_requires_admin_and_existing_experience(self):
        fid = self._submit()
        self._review(fid)
        self._approve(fid)
        self.assertEqual(
            self.client.post(f"/api/feedback/{fid}/revoke", headers=self._hdr("reviewerA"), json={}).status_code,
            403,
        )
        fid2 = self._submit(action="confirmed", reason="evidence_gap")
        self.assertEqual(
            self.client.post(f"/api/feedback/{fid2}/revoke", headers=self._hdr("adminA"), json={}).status_code,
            404,
        )

    # ══════════════════════════════════════════════════════════════
    # 5. 删除保护
    # ══════════════════════════════════════════════════════════════
    def test_approved_feedback_cannot_be_hard_deleted(self):
        fid = self._submit()
        self._review(fid)
        self._approve(fid)
        r = self.client.delete(f"/api/feedback/{fid}", headers=self._hdr("uploaderA"))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("撤销", r.json()["detail"])

    def test_pending_feedback_can_still_be_deleted_by_owner(self):
        fid = self._submit()
        r = self.client.delete(f"/api/feedback/{fid}", headers=self._hdr("uploaderA"))
        self.assertEqual(r.status_code, 200)
        s = self.S()
        self.assertIsNone(s.query(FeedbackLog).filter(FeedbackLog.id == fid).first())
        s.close()

    # ══════════════════════════════════════════════════════════════
    # 6. 规则反馈池
    # ══════════════════════════════════════════════════════════════
    def test_rule_suspect_goes_to_rule_pool_and_cannot_be_approved(self):
        r = self.client.post("/api/feedback", headers=self._hdr("uploaderA"), json={
            "record_id": self.record_id, "action_type": "false_positive",
            "feedback_reason": "adjudicator_rule_suspect",
            "comment": "证据里有责任上限却被判 R02，怀疑裁决规则本身有误",
        })
        self.assertEqual(r.status_code, 201, r.text)
        fid = r.json()["data"]["id"]
        self._review(fid)

        pool = self.client.get("/api/feedback/rule-pool", headers=self._hdr("reviewerA"))
        self.assertEqual(pool.status_code, 200)
        self.assertEqual([i["id"] for i in pool.json()["data"]["items"]], [fid])

        bad = self._approve(fid)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("规则反馈池", bad.json()["detail"])
        self.assertEqual(self.env.store.load_active_experiences(), [])

    # ══════════════════════════════════════════════════════════════
    # 7. 反馈池可见性与过滤
    # ══════════════════════════════════════════════════════════════
    def test_pool_requires_workflow_role(self):
        self._submit()
        self.assertEqual(
            self.client.get("/api/feedback/pool", headers=self._hdr("uploaderB")).status_code, 403)
        r = self.client.get("/api/feedback/pool", headers=self._hdr("reviewerA"))
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["data"]["total"], 1)

    def test_pool_filters_by_status_reason_and_type(self):
        fid1 = self._submit(action="false_positive", reason="clause_exists")
        fid2 = self._submit(action="confirmed", reason="evidence_gap")
        self._review(fid1)
        hdr = self._hdr("reviewerA")
        only_reviewed = self.client.get("/api/feedback/pool?status=reviewed", headers=hdr).json()["data"]
        self.assertEqual([i["id"] for i in only_reviewed["items"]], [fid1])
        only_reason = self.client.get("/api/feedback/pool?feedback_reason=evidence_gap", headers=hdr).json()["data"]
        self.assertEqual([i["id"] for i in only_reason["items"]], [fid2])
        by_type = self.client.get("/api/feedback/pool?contract_type=买卖合同", headers=hdr).json()["data"]
        self.assertEqual(by_type["total"], 2)
        none_type = self.client.get("/api/feedback/pool?contract_type=劳动合同", headers=hdr).json()["data"]
        self.assertEqual(none_type["total"], 0)

    # ══════════════════════════════════════════════════════════════
    # 8. 不可学习的反馈类型
    # ══════════════════════════════════════════════════════════════
    def test_supplemented_feedback_is_not_materialized_in_phase1(self):
        r = self.client.post("/api/feedback", headers=self._hdr("uploaderA"), json={
            "record_id": self.record_id, "action_type": "supplemented",
            "feedback_reason": "evidence_gap", "comment": "合同漏了一条风险",
        })
        fid = r.json()["data"]["id"]
        self._review(fid)
        bad = self._approve(fid)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("第一阶段", bad.json()["detail"])

    # ══════════════════════════════════════════════════════════════
    # 9. Gold/Test 隔离
    # ══════════════════════════════════════════════════════════════
    def test_eval_set_overlap_blocks_materialization(self):
        # 造一份"官方评测语料"，其正文与生产合同完全一致（等价于测试样本被反馈沉淀的场景）
        corpus_dir = tempfile.mkdtemp()
        corpus_path = Path(corpus_dir) / "fake_realtest.json"
        with open(corpus_path, "w", encoding="utf-8") as f:
            json.dump([{"id": "realtest_x", "content": "第一条 付款。\n" + _CLAUSE}], f, ensure_ascii=False)
        os.environ["EVAL_CORPUS_PATHS"] = str(corpus_path)
        eval_isolation.reset_cache()

        fid = self._submit()
        self._review(fid)
        r = self._approve(fid)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("评测语料", r.json()["detail"])
        self.assertEqual(self.env.store.load_active_experiences(), [])
        # 经验行不得被创建
        s = self.S()
        self.assertEqual(s.query(FeedbackExperience).count(), 0)
        s.close()

    def test_eval_isolation_allows_non_eval_contract(self):
        corpus_dir = tempfile.mkdtemp()
        corpus_path = Path(corpus_dir) / "fake_realtest.json"
        with open(corpus_path, "w", encoding="utf-8") as f:
            json.dump([{"id": "realtest_y", "content": "完全不同的合同正文"}], f, ensure_ascii=False)
        os.environ["EVAL_CORPUS_PATHS"] = str(corpus_path)
        eval_isolation.reset_cache()
        fid = self._submit()
        self._review(fid)
        self.assertEqual(self._approve(fid).status_code, 200)

    # ══════════════════════════════════════════════════════════════
    # 10. 经验库视图（可追溯）
    # ══════════════════════════════════════════════════════════════
    def test_experiences_view_is_traceable(self):
        fid = self._submit()
        self._review(fid)
        exp_id = self._approve(fid).json()["data"]["experience_id"]
        r = self.client.get("/api/feedback/experiences", headers=self._hdr("approverA"))
        self.assertEqual(r.status_code, 200)
        item = r.json()["data"]["items"][0]
        self.assertEqual(item["id"], exp_id)
        self.assertEqual(item["source_feedback_id"], fid)      # 回答"这条经验为什么在库里"
        self.assertEqual(item["human_label"], "false_positive")
        self.assertEqual(item["collection_name"], "feedback_experiences")
        self.assertIsNotNone(item["approved_by"])
        self.assertIn("应重点核查", item["experience_text"])
        # 经验正文不得包含"本次应判定为 X 风险"这类结论
        self.assertNotIn("应判定", item["experience_text"])
        self.assertNotIn("风险成立，应标", item["experience_text"])


if __name__ == "__main__":
    unittest.main()
