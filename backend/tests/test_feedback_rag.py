"""Feedback RAG 检索层 + 注入层测试（完全离线，不碰生产向量库/数据库/模型）。

覆盖用户要求的第一阶段全部验收点：

注入边界（最高优先级）
  1. `feedback_context=None`（默认）时 prompt **逐字节不变**（官方评测冻结的前提）
  2. 传入 str / callable 时 prompt 追加【人工历史参考】块，且块内含"不得输出风险判定"约束
  3. 经验注入失败（callable 抛异常）→ 不抛异常、prompt 退回未注入形态（审核不受影响）
  4. `extract_evidence()`（评测入口）**即使开关打开也不会注入**
  5. `adjudicate_risks` 签名与行为完全不变（裁决层零接触）

检索与索引
  6. 只返回 status='active' 的经验（pending/reviewed/未批准一律不可检索）
  7. contract_type / risk_type 过滤有效
  8. 撤销后（revoked）不再被检索
  9. 撤销后 Chroma 文档被**精确删除**，其它经验不受影响
 10. 稠密一路失败（Chroma 不可用）时 BM25 仍可召回（降级不失效）
 11. embedding 模型不可用时仍可召回
 12. 重建索引只动自己的集合，不触碰 laws / standard_clauses / contract_templates
 13. 注入块有长度上限；audit_summary 记录经验 id 与 index_version

运行（backend 目录下）：
    python -m unittest tests.test_feedback_rag -v
"""
import inspect
import gc
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

from database import Base  # noqa: E402
from models.feedback_experience import FeedbackExperience  # noqa: E402
from ai.auditor import evidence_extractor as ee  # noqa: E402
from ai.auditor.evidence_adjudicator import adjudicate_risks  # noqa: E402
from ai.rag import feedback_store  # noqa: E402
from tests.feedback_test_utils import RagEnv, DeterministicEmbedder, COLLECTION  # noqa: E402


class TestFeedbackInjectBoundary(unittest.TestCase):
    """注入层：默认不变、显式注入、失败降级、评测路径免疫。"""

    def setUp(self):
        self.chunk = "第一条 付款：验收合格后30日内支付。"
        self.prompts = []
        self.fake_llm = mock.MagicMock()

        def _chat(prompt, temperature=0.1):
            self.prompts.append(prompt)
            return "{}"

        self.fake_llm.chat.side_effect = _chat
        self._patcher = mock.patch.object(ee, "llm_client", self.fake_llm)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _expected_default_prompt(self, chunk):
        """历史（未注入）形态：必须逐字节一致。"""
        return ee.SYSTEM_PROMPT_EVIDENCE + "\n\n请抽取以下合同的事实：\n" + chunk

    def test_default_prompt_is_byte_identical(self):
        # 单块路径
        ee._extract_chunk(self.chunk)
        self.assertEqual(self.prompts[-1], self._expected_default_prompt(self.chunk))
        # 信封 API 路径（feedback_context 缺省）
        self.prompts.clear()
        ee.extract_evidence_detailed(self.chunk)
        self.assertEqual(self.prompts[-1], self._expected_default_prompt(self.chunk))
        # 兼容入口（评测脚本走这条）
        self.prompts.clear()
        ee.extract_evidence(self.chunk)
        self.assertEqual(self.prompts[-1], self._expected_default_prompt(self.chunk))

    def test_none_and_empty_block_leave_prompt_unchanged(self):
        for ctx in (None, "", "   "):
            self.prompts.clear()
            ee._extract_chunk(self.chunk, ctx)
            self.assertEqual(self.prompts[-1], self._expected_default_prompt(self.chunk), repr(ctx))

    def test_string_context_is_injected_with_constraints(self):
        block = "[历史人工结论] R08 曾被人工判定为误报。\n[应重点核查] 是否引用了国家/行业标准。"
        ee._extract_chunk(self.chunk, block)
        p = self.prompts[-1]
        self.assertTrue(p.startswith(ee.SYSTEM_PROMPT_EVIDENCE), "系统提示必须仍在最前")
        self.assertIn("【人工历史参考】", p)
        self.assertIn("应重点核查", p)
        # 必须带"不得输出风险判定 / 以当前合同原文为准"的硬约束
        self.assertIn("不得输出任何风险判定字段", p)
        self.assertIn("以**当前合同原文**为准", p)
        # 历史形态仍可作为子串（说明是"追加"而不是"改写"）
        self.assertIn("\n\n请抽取以下合同的事实：\n" + self.chunk, p)

    def test_callable_context_is_resolved_per_chunk(self):
        seen = []

        def provider(chunk):
            seen.append(chunk)
            return f"经验@:{chunk[:4]}"

        ee._extract_chunk("块甲", provider)
        ee._extract_chunk("块乙", provider)
        self.assertEqual(seen, ["块甲", "块乙"])
        self.assertIn("经验@:块甲", self.prompts[0])
        self.assertIn("经验@:块乙", self.prompts[1])

    def test_failing_context_degrades_to_default_prompt(self):
        def boom(_chunk):
            raise RuntimeError("chroma down")

        # 不抛异常
        ee._extract_chunk(self.chunk, boom)
        self.assertEqual(self.prompts[-1], self._expected_default_prompt(self.chunk))

        class BadObj:
            def __str__(self):
                raise RuntimeError("bad repr")

        ee._extract_chunk(self.chunk, BadObj())
        self.assertEqual(self.prompts[-1], self._expected_default_prompt(self.chunk))

    def test_eval_entry_ignores_enabled_flag(self):
        """评测入口 `extract_evidence` 不暴露 feedback_context：开关打开也不注入。"""
        with mock.patch.dict(os.environ, {"FEEDBACK_RAG_ENABLED": "true"}):
            self.prompts.clear()
            ee.extract_evidence(self.chunk)
            self.assertEqual(self.prompts[-1], self._expected_default_prompt(self.chunk))
            self.assertNotIn("人工历史参考", self.prompts[-1])

    def test_multi_chunk_injection_does_not_break_merge(self):
        long_text = ("第一条 甲" * 6000)   # 24000 字符 > MAX_CHARS(20000) → 触发多块并行
        provider = mock.MagicMock(return_value="[历史人工结论] 参考")
        res = ee.extract_evidence_detailed(long_text, feedback_context=provider)
        self.assertGreater(provider.call_count, 1, "多块时应对每块都解析一次参考")
        self.assertEqual(res["status"], "success")
        self.assertTrue(all("【人工历史参考】" in p for p in self.prompts))

    def test_multi_chunk_without_feedback_is_unchanged(self):
        long_text = ("第一条 甲" * 6000)
        ee.extract_evidence_detailed(long_text)
        self.assertTrue(all("【人工历史参考】" not in p for p in self.prompts))


class TestAdjudicatorUntouched(unittest.TestCase):
    """裁决层零接触：签名与行为必须完全不变。"""

    def test_signature_is_single_evidence_param(self):
        sig = inspect.signature(adjudicate_risks)
        self.assertEqual(list(sig.parameters), ["evidence"])

    def test_feedback_context_is_keyword_only(self):
        sig = inspect.signature(ee.extract_evidence_detailed)
        p = sig.parameters["feedback_context"]
        self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(p.default, "默认必须是 None（prompt 才能保持不变）")
        # 兼容入口没有该参数（评测路径无法被注入）
        self.assertNotIn("feedback_context", inspect.signature(ee.extract_evidence).parameters)

    def test_behavior_locked_for_reference_evidence(self):
        # 冻结样例：证据里存在"全部损失 + 无上限" → R02 必须命中；
        # 存在客观验收依据（引用国标）→ R08 不得命中。
        ev = {
            "contract_type": "买卖",
            "is_delivery_type": True,
            "R02_责任": {"absolute_wording": True, "absolute_text": "乙方赔偿甲方全部损失",
                         "has_cap": False, "scope": "全部损失", "liable_party": "乙方"},
            "R08_验收": {"objective_basis": True, "basis_evidence": "按 GB/T 19001 国家标准验收"},
        }
        types = {r["risk_type"] for r in adjudicate_risks(ev)}
        self.assertIn("R02", types)
        self.assertNotIn("R08", types)


class TestFeedbackStore(unittest.TestCase):
    """检索层：只检索 active、过滤、撤销、降级、重建隔离。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.eng = create_engine(f"sqlite:///{Path(cls.tmp.name) / 'rag.db'}",
                                connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(cls.eng)
        cls.S = sessionmaker(bind=cls.eng)
        cls._src_seq = 0

    @classmethod
    def tearDownClass(cls):
        cls.eng.dispose()
        gc.collect()          # Windows：确保 sqlite 文件句柄释放后再删临时目录
        try:
            cls.tmp.cleanup()
        except (PermissionError, NotADirectoryError):
            pass

    def setUp(self):
        self.env = RagEnv(self.S)
        self.env.__enter__()
        s = self.S()
        s.query(FeedbackExperience).delete()
        s.commit()
        s.close()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def _add(self, status="active", contract_type="买卖合同", risk_type="R08",
             query_text="（6）履约验收标准：设备正常运行生产30日并验收合格后",
             experience_text="[历史人工结论] R08 曾被人工判定为误报。\n[应重点核查] 是否存在验收依据。",
             label="false_positive"):
        type(self)._src_seq += 1
        s = self.S()
        exp = FeedbackExperience(
            # source_feedback_id 有唯一约束（一条反馈最多一条经验），测试里用自增计数器区分
            source_feedback_id=1000 + type(self)._src_seq,
            kind="risk_judgement", status=status,
            contract_type=contract_type, risk_type=risk_type, human_label=label,
            feedback_reason="clause_exists", query_text=query_text,
            experience_text=experience_text, doc_id=None, index_version=None,
        )
        s.add(exp)
        s.commit()
        s.refresh(exp)
        eid = exp.id
        s.close()
        feedback_store.add_experience(self._reload(eid))
        return eid

    def _reload(self, eid):
        s = self.S()
        try:
            return s.query(FeedbackExperience).filter(FeedbackExperience.id == eid).first()
        finally:
            s.close()

    # ── 6. 只检索 active ────────────────────────────────────────────
    def test_only_active_experiences_are_retrievable(self):
        active = self._add(status="active", query_text="履约验收标准依据是否存在")
        revoked = self._add(status="revoked", query_text="履约验收标准依据是否存在")
        actives = feedback_store.load_active_experiences()
        self.assertEqual([e["id"] for e in actives], [active])
        hits = feedback_store.search_feedback_experiences("履约验收标准依据", top_k=5)
        self.assertTrue(hits)
        self.assertEqual([h["id"] for h in hits], [active], "revoked 经验绝不能被检索")
        self.assertNotIn(revoked, [h["id"] for h in hits])

    def test_pending_like_statuses_are_not_retrievable(self):
        # 经验表只允许 active/revoked；用非 active 值模拟"未批准就落库"的异常数据
        self._add(status="pending_index")
        self.assertEqual(feedback_store.load_active_experiences(), [])
        self.assertEqual(feedback_store.search_feedback_experiences("履约验收标准"), [])

    # ── 7. 过滤 ─────────────────────────────────────────────────────
    def test_contract_type_and_risk_type_filters(self):
        a = self._add(contract_type="买卖合同", risk_type="R08", query_text="履约验收标准依据")
        b = self._add(contract_type="承揽合同", risk_type="R08", query_text="履约验收标准依据")
        c = self._add(contract_type="买卖合同", risk_type="R02", query_text="履约验收标准依据")

        only_ct = {e["id"] for e in feedback_store.load_active_experiences(contract_type="买卖合同")}
        self.assertEqual(only_ct, {a, c})
        only_rt = {e["id"] for e in feedback_store.load_active_experiences(risk_type="R02")}
        self.assertEqual(only_rt, {c})
        both = {e["id"] for e in feedback_store.load_active_experiences(contract_type="承揽合同", risk_type="R08")}
        self.assertEqual(both, {b})

        hits = feedback_store.search_feedback_experiences("履约验收标准依据", contract_type="买卖合同", top_k=5)
        self.assertTrue(hits)
        self.assertTrue(all(h["contract_type"] == "买卖合同" for h in hits))

    # ── 8/9. 撤销 ───────────────────────────────────────────────────
    def test_revoke_excludes_and_precisely_deletes_only_that_doc(self):
        keep = self._add(query_text="买卖合同 违约金比例约定是否存在上限")
        drop = self._add(query_text="履约验收标准依据是否存在")
        self.assertEqual(sorted(self.env.doc_ids()), sorted([f"exp_{keep}", f"exp_{drop}"]))

        result = feedback_store.remove_experience(drop)
        self.assertTrue(result["removed"], result)

        # 精确删除：只删目标文档，另一条经验仍在索引中
        self.assertEqual(self.env.doc_ids(), [f"exp_{keep}"])

        # 完整撤销语义 = 索引删除 + DB 状态置 revoked（见 services.feedback_experience.revoke_experience）
        s = self.S()
        s.query(FeedbackExperience).filter(FeedbackExperience.id == drop).update({"status": "revoked"})
        s.commit()
        s.close()

        self.assertEqual([e["id"] for e in feedback_store.load_active_experiences()], [keep])
        hits = feedback_store.search_feedback_experiences("履约验收标准依据", top_k=5)
        self.assertNotIn(drop, [h["id"] for h in hits])

    # ── 10/11. 降级 ────────────────────────────────────────────────
    def test_dense_failure_falls_back_to_bm25(self):
        eid = self._add(query_text="履约验收标准依据是否存在")
        with mock.patch.object(feedback_store, "_get_client", side_effect=RuntimeError("chroma down")):
            hits = feedback_store.search_feedback_experiences("履约验收标准依据", top_k=3)
        self.assertTrue(hits, "Chroma 不可用时 BM25 仍应召回")
        self.assertEqual(hits[0]["id"], eid)

    def test_embedder_failure_falls_back_to_bm25(self):
        eid = self._add(query_text="履约验收标准依据是否存在")
        with mock.patch.object(feedback_store, "_get_embedder", side_effect=RuntimeError("model missing")):
            hits = feedback_store.search_feedback_experiences("履约验收标准依据", top_k=3)
        self.assertEqual([h["id"] for h in hits], [eid])

    def test_search_never_raises(self):
        self._add()
        with mock.patch.object(feedback_store, "load_active_experiences", side_effect=RuntimeError("db down")):
            self.assertEqual(feedback_store.search_feedback_experiences("任意"), [])
        self.assertEqual(feedback_store.search_feedback_experiences(""), [])

    # ── 12. 重建隔离 ───────────────────────────────────────────────
    def test_rebuild_touches_only_own_collection(self):
        client = self.env.client
        laws = client.create_collection("laws", metadata={"hnsw:space": "cosine"})
        laws.upsert(ids=["law_1"], embeddings=[[1.0, 0.0, 0.0]], documents=["民法典585"],
                    metadatas=[{"clause_id": "1"}])
        templates = client.create_collection("contract_templates", metadata={"hnsw:space": "cosine"})
        templates.upsert(ids=["tpl_1"], embeddings=[[1.0, 0.0, 0.0]], documents=["范本"],
                         metadatas=[{"type": "买卖合同"}])

        self._add(query_text="履约验收标准依据")
        out = feedback_store.rebuild_index()
        self.assertTrue(out["rebuilt"], out)
        self.assertEqual(out["count"], 1)

        # 其它集合并未被触碰
        self.assertEqual(laws.get().get("ids"), ["law_1"])
        self.assertEqual(templates.get().get("ids"), ["tpl_1"])
        self.assertEqual(len(client.get_collection(COLLECTION).get().get("ids")), 1)

    # ── 13. 版本与块上限 ───────────────────────────────────────────
    def test_index_version_changes_when_corpus_changes(self):
        v0 = feedback_store.index_version([])
        self.assertEqual(v0, "v0-0")
        self._add()
        v1 = feedback_store.index_version()
        self.assertTrue(v1.startswith("v"), v1)
        self.assertNotEqual(v1, v0)
        self._add(query_text="另一条经验")
        self.assertNotEqual(feedback_store.index_version(), v1)

    def test_block_formatting_respects_limit(self):
        exps = [{"risk_type": "R08", "contract_type": "买卖合同", "experience_text": "核查点" * 200} for _ in range(3)]
        block = feedback_store.format_experience_block(exps, max_chars=300)
        self.assertLessEqual(len(block), 320)
        self.assertEqual(feedback_store.format_experience_block([]), "")

    def test_provider_records_used_ids_and_version(self):
        eid = self._add(query_text="履约验收标准依据是否存在")
        provider = feedback_store.build_feedback_context_provider(contract_type="买卖合同")
        block = provider("本合同履约验收标准依据是否存在？")
        self.assertIn("[历史人工结论]", block)
        summary = provider.audit_summary()
        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["experience_ids"], [eid])
        self.assertEqual(summary["collection"], COLLECTION)
        self.assertEqual(summary["applied_chunks"], 1)
        self.assertIsNotNone(summary["index_version"])

    def test_provider_returns_empty_without_experiences(self):
        provider = feedback_store.build_feedback_context_provider(contract_type="劳动合同")
        self.assertEqual(provider("任意文本"), "")
        self.assertEqual(provider.audit_summary()["experience_ids"], [])

    def test_disabled_summary_shape(self):
        s = feedback_store.disabled_audit_summary("disabled")
        self.assertFalse(s["enabled"])
        self.assertEqual(s["experience_ids"], [])
        self.assertIn("reason", s)

    # ── 只注入"应重点核查什么"（内容策略）────────────────────────────
    def test_experience_text_is_templated_and_contains_no_verdict(self):
        from services.feedback_experience import build_experience_text
        for label in ("false_positive", "confirmed"):
            txt = build_experience_text("R08", label, "clause_exists", "第四条第（2）款已有验收标准")
            self.assertIn("[应重点核查]", txt)
            self.assertIn("[约束]", txt)
            self.assertIn("不得据此输出任何风险判定字段", txt)
            # 不得出现"直接下结论"的措辞
            for banned in ("应判定", "应标记", "判为风险", "输出 risk"):
                self.assertNotIn(banned, txt)
            # 人工意见原文被引用，但被明确标注为参考
            self.assertIn("第四条第（2）款已有验收标准", txt)

    def test_experience_text_covers_all_13_risk_types(self):
        from services.feedback_experience import build_experience_text
        for i in range(1, 14):
            rt = f"R{i:02d}"
            txt = build_experience_text(rt, "false_positive", "other", None)
            self.assertIn("[应重点核查]", txt)
            self.assertGreater(len(txt), 40, rt)


class TestProductionAuditInjection(unittest.TestCase):
    """端到端：生产审核链路（`_run_audit`）在开关打开时注入经验、关闭时完全不注入。

    用临时库 + 假 LLM，验证三件事：
      1. 开关打开 → evidence 抽取 prompt 里出现【人工历史参考】，且 audit_records.learning_context
         记录了本次实际用到的 experience_ids 与 index_version；
      2. 开关关闭（默认）→ prompt 里没有参考块，learning_context 明确标记未启用；
      3. 检索失败（例如 Chroma 不可用）→ 审核照常完成（降级为不使用经验）。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.eng = create_engine(f"sqlite:///{Path(cls.tmp.name) / 'audit.db'}",
                                connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(cls.eng)
        cls.S = sessionmaker(bind=cls.eng)

    @classmethod
    def tearDownClass(cls):
        cls.eng.dispose()
        gc.collect()
        try:
            cls.tmp.cleanup()
        except (PermissionError, NotADirectoryError):
            pass

    def setUp(self):
        self.env = RagEnv(self.S)
        self.env.__enter__()
        s = self.S()
        s.query(FeedbackExperience).delete()
        from models.contract import Contract
        from models.audit_record import AuditRecord
        s.query(AuditRecord).delete()
        s.query(Contract).delete()
        s.commit()

        contract = Contract(
            user_id=1, file_name="生产合同.docx", contract_type="买卖合同",
            parsed_text="第一条 付款：验收合格后30日内支付。\n（6）履约验收标准：设备验收合格后",
            status="parsed", audit_mode="precise",
        )
        s.add(contract)
        s.commit()
        s.refresh(contract)
        self.cid = contract.id
        s.close()

        self.prompts = []
        fake_ev = mock.MagicMock()
        fake_ev.chat.side_effect = lambda prompt, temperature=0.1: (
            self.prompts.append(prompt) or
            '{"contract_type": "买卖", "is_delivery_type": true,'
            ' "R09_不可抗力": {"has_force_majeure": false, "force_majeure_evidence": ""}}'
        )
        fake_rec = mock.MagicMock()
        fake_rec.chat.return_value = "[]"

        import api.contracts as contracts
        from ai.auditor import recommendation_engine as rec
        self._patchers = [
            mock.patch.object(ee, "llm_client", fake_ev),
            mock.patch.object(rec, "llm_client", fake_rec),
            mock.patch.object(contracts, "SessionLocal", self.S),
            mock.patch.object(contracts, "compare_clauses",
                              mock.MagicMock(return_value={"clauses": [], "summary": {"total": 0}})),
        ]
        for p in self._patchers:
            p.start()
        self.contracts = contracts

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.env.__exit__(None, None, None)

    def _add_experience(self):
        s = self.S()
        exp = FeedbackExperience(
            source_feedback_id=7777, kind="risk_judgement", status="active",
            contract_type="买卖合同", risk_type="R09", human_label="false_positive",
            feedback_reason="clause_exists",
            query_text="履约验收标准：设备验收合格后",
            experience_text="[历史人工结论] R09 曾被人工判定为误报。\n[应重点核查] 是否已存在不可抗力处理机制。",
        )
        s.add(exp)
        s.commit()
        s.refresh(exp)
        eid = exp.id
        s.close()
        feedback_store.add_experience(self._reload(eid))
        return eid

    def _reload(self, eid):
        s = self.S()
        try:
            return s.query(FeedbackExperience).filter(FeedbackExperience.id == eid).first()
        finally:
            s.close()

    def _run(self):
        self.contracts._run_audit(self.cid)

    def _records(self):
        from models.audit_record import AuditRecord
        s = self.S()
        try:
            return s.query(AuditRecord).filter(AuditRecord.contract_id == self.cid).all()
        finally:
            s.close()

    def test_enabled_injects_and_records_learning_context(self):
        eid = self._add_experience()
        with mock.patch("config.FEEDBACK_RAG_ENABLED", True):
            self._run()

        injected = [p for p in self.prompts if "【人工历史参考】" in p]
        self.assertTrue(injected, "开关打开时应注入人工历史参考")
        self.assertIn("应重点核查", injected[0])

        recs = self._records()
        self.assertTrue(recs, "审核应写入风险记录（或无风险时空批次亦应有记录）")
        for r in recs:
            lc = r.learning_context or {}
            self.assertTrue(lc.get("enabled"))
            self.assertEqual(lc.get("collection"), COLLECTION)
            self.assertIn(eid, lc.get("experience_ids") or [])
            self.assertIsNotNone(lc.get("index_version"))

    def test_disabled_does_not_inject_and_marks_context(self):
        self._add_experience()
        with mock.patch("config.FEEDBACK_RAG_ENABLED", False):
            self._run()
        self.assertFalse([p for p in self.prompts if "【人工历史参考】" in p],
                         "开关关闭时绝不能注入")
        # 无风险记录时也要能证明"未启用"（用 report 无关的证据：prompt 层面已断言）
        recs = self._records()
        for r in recs:
            lc = r.learning_context or {}
            self.assertFalse(lc.get("enabled"))
            self.assertEqual(lc.get("experience_ids") or [], [])

    def test_retrieval_failure_does_not_break_audit(self):
        self._add_experience()
        with mock.patch("config.FEEDBACK_RAG_ENABLED", True), \
             mock.patch.object(feedback_store, "load_active_experiences",
                               side_effect=RuntimeError("chroma/db down")):
            self._run()   # 不得抛出

        self.assertFalse([p for p in self.prompts if "【人工历史参考】" in p])
        from models.contract import Contract
        s = self.S()
        try:
            c = s.query(Contract).filter(Contract.id == self.cid).first()
            # 主审核链路照常完成（状态推进到 completed）
            self.assertEqual(c.status, "completed")
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
