"""修订版导出功能测试：会话持久化 + 修订版 DOCX 生成与下载。

覆盖：
1. 单条款修改 → 下载 DOCX（原文替换为新条款）
2. 多条款修改 → 其他条款不变
3. 刷新/重新进入 → 历史会话仍在（GET /revisions）
4. 继续上一轮会话（A→A1→A2 链式归并，只保留最终 A2）
5. 没有修改时下载 → 400 合理提示
6. 原始 DOCX 不被覆盖
7. 现有回归不退化（随全量 suite 跑）

运行（backend 目录下）：
    python -m unittest tests.test_revision_export -v
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from docx import Document

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from database import Base  # noqa: E402
from models.contract import Contract  # noqa: E402
from services.docx_reviser import build_revised_docx  # noqa: E402
import api.contracts as contracts  # noqa: E402


def _make_docx(path, paragraphs):
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))
    return str(path)


def _rev(scope="clause", clause_text="", revised_clause="", original_clause_text=None):
    return types.SimpleNamespace(scope=scope, clause_text=clause_text, revised_clause=revised_clause,
                                 original_clause_text=original_clause_text)


class TestDocxReviser(unittest.TestCase):
    """纯单元测试：build_revised_docx 的段落替换 + 链式归并 + 原文件不覆盖。"""

    def _texts(self, path):
        return [p.text for p in Document(path).paragraphs if p.text.strip()]

    def test_single_clause_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["甲方违约金为30%。", "乙方保密义务。", "丙方验收条款。"])
            dst = Path(tmp) / "out.docx"
            applied, skipped = build_revised_docx(src, [_rev(clause_text="甲方违约金为30%", revised_clause="甲方违约金为20%", original_clause_text="甲方违约金为30%")], str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            # 子串替换只替换命中片段，保留原段落尾部标点
            self.assertEqual(self._texts(str(dst)), ["甲方违约金为20%。", "乙方保密义务。", "丙方验收条款。"])

    def test_multi_clause_only_targets_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["A条款原文", "B条款原文", "C条款原文"])
            dst = Path(tmp) / "out.docx"
            revs = [_rev(clause_text="A条款原文", revised_clause="A条款修订", original_clause_text="A条款原文"),
                    _rev(clause_text="C条款原文", revised_clause="C条款修订", original_clause_text="C条款原文")]
            applied, skipped = build_revised_docx(src, revs, str(dst))
            self.assertEqual((applied, skipped), (2, 0))
            self.assertEqual(self._texts(str(dst)), ["A条款修订", "B条款原文", "C条款修订"])

    def test_same_clause_chain_keeps_latest(self):
        # A → A1 → A2 连续修改，导出只保留最终 A2，不残留 A1
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["A条款原文", "B条款原文"])
            dst = Path(tmp) / "out.docx"
            revs = [_rev(clause_text="A条款原文", revised_clause="A条款V1", original_clause_text="A条款原文"),
                    _rev(clause_text="A条款V1", revised_clause="A条款V2", original_clause_text="A条款原文")]
            applied, skipped = build_revised_docx(src, revs, str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            self.assertEqual(self._texts(str(dst)), ["A条款V2", "B条款原文"])

    def test_original_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.docx"
            _make_docx(src, ["A条款原文", "B条款原文"])
            before = src.read_bytes()
            build_revised_docx(str(src), [_rev(clause_text="A条款原文", revised_clause="A条款修订", original_clause_text="A条款原文")], str(Path(tmp) / "out.docx"))
            self.assertEqual(src.read_bytes(), before)

    def test_overview_scope_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["A条款原文"])
            applied, skipped = build_revised_docx(src, [_rev(scope="overview", clause_text="A条款原文", revised_clause="整份改")], str(Path(tmp) / "out.docx"))
            self.assertEqual((applied, skipped), (0, 0))

    def test_llm_evidence_differs_from_docx_but_location_succeeds(self):
        # 本次真实 Bug 根因：LLM evidence 是改写版，不等于 DOCX 原文；锚点用逐字原文即可定位
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", [
                "四、合同验收",
                "（6）履约验收标准：由采购人组织人工审核，逐项核对履约标准。",
                "其他条款保持不变。",
            ])
            dst = Path(tmp) / "out.docx"
            evidence = "验收标准与验收方式：本合同项下的验收采用人工审核方式，由采购人按照履约标准组织全面人工审核。"
            verbatim = "（6）履约验收标准：由采购人组织人工审核，逐项核对履约标准。"
            applied, skipped = build_revised_docx(src, [
                _rev(clause_text=evidence,
                     revised_clause="（6）履约验收标准：由采购人组织人工审核，逐项核对技术、服务、安全标准，并出具验收书。",
                     original_clause_text=verbatim)
            ], str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            self.assertEqual(self._texts(str(dst)), [
                "四、合同验收",
                "（6）履约验收标准：由采购人组织人工审核，逐项核对技术、服务、安全标准，并出具验收书。",
                "其他条款保持不变。",
            ])

    def test_sub_clause_replacement_in_single_paragraph(self):
        # 整份合同落在单个段落时，只替换命中的子条款，保留其余正文（111.docx 的真实结构）
        with tempfile.TemporaryDirectory() as tmp:
            full = "合同正文开头。四、合同验收（1）验收组织方式：甲方组织（6）履约验收标准：设备验收合格30日内组织验收。五、违约责任：按合同约定承担。"
            src = _make_docx(Path(tmp) / "src.docx", [full])
            dst = Path(tmp) / "out.docx"
            anchor = "（6）履约验收标准：设备验收合格30日内组织验收"
            applied, skipped = build_revised_docx(src, [
                _rev(clause_text="验收标准与验收方式：本合同项下的验收采用人工审核方式。",
                     revised_clause="（6）履约验收标准：设备验收合格30日内组织人工审核并出具验收书",
                     original_clause_text=anchor)
            ], str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            text = Document(str(dst)).paragraphs[0].text
            self.assertIn("（6）履约验收标准：设备验收合格30日内组织人工审核并出具验收书", text)
            self.assertIn("合同正文开头", text)       # 其余正文保留
            self.assertIn("五、违约责任", text)        # 后续内容保留
            self.assertNotIn("设备验收合格30日内组织验收。", text)  # 原锚点已替换

    def test_locate_clause_finds_verbatim_with_rewritten_evidence(self):
        # _locate_clause 前缀缩短到 4 字后，改写版 evidence 也能定位到真实原文行
        full = "四、合同验收\n（6）履约验收标准：由采购人组织人工审核，逐项核对履约标准。\n五、争议解决"
        evidence = "验收标准与验收方式：本合同项下的验收采用人工审核方式。"
        loc = contracts._locate_clause(full, evidence)
        self.assertIsNotNone(loc)
        self.assertIn("履约验收标准", loc["original_text"])

    def test_locate_clause_disambiguates_multi_hit_to_subitem(self):
        # 同一短语出现两次：第一次在付款条款、第二次在（6）子项 → 必须选（6）
        full = "付款方式：设备正常运行生产30日并验收合格后，支付30%。四、合同验收（6）履约验收标准：设备正常运行生产30日并验收合格后。"
        clause = "设备正常运行生产30日并验收合格"
        loc = contracts._locate_clause(full, clause)
        self.assertIsNotNone(loc)
        self.assertIn("（6）履约验收标准", loc["original_text"])
        self.assertNotIn("支付30%", loc["original_text"])

    def test_locate_clause_multi_hit_no_subitem_returns_none(self):
        # 多处命中且无（N）子项 → 显式定位失败（不随意取第一个）
        full = "A设备正常运行生产30日并验收合格。B设备正常运行生产30日并验收合格。"
        clause = "设备正常运行生产30日并验收合格"
        loc = contracts._locate_clause(full, clause)
        self.assertIsNone(loc)

    def test_locate_clause_unique_hit_unchanged(self):
        # 唯一命中：现有行为不变
        full = "四、合同验收（6）履约验收标准：设备正常运行生产30日并验收合格后。"
        clause = "设备正常运行生产30日并验收合格"
        loc = contracts._locate_clause(full, clause)
        self.assertIsNotNone(loc)
        self.assertIn("（6）履约验收标准", loc["original_text"])

    def test_r08_evidence_prompt_always_asks_verbatim(self):
        # R08 证据 prompt 必须要求逐字摘录完整子项（含编号+标题，唯一），不能填 false/空
        from ai.auditor.evidence_extractor import SYSTEM_PROMPT_EVIDENCE
        self.assertIn("无论是否客观都逐字摘录", SYSTEM_PROMPT_EVIDENCE)
        self.assertIn("含子项编号与标题", SYSTEM_PROMPT_EVIDENCE)
        self.assertNotIn("否则填false", SYSTEM_PROMPT_EVIDENCE)

    def test_r08_adjudicator_uses_verbatim_basis_evidence(self):
        # R08 裁决器：objective_basis=false 但有逐字 basis_evidence 时，用它做 clause_text，而非兜底标记
        from ai.auditor.evidence_adjudicator import adjudicate_risks
        evidence = {
            "is_delivery_type": True,
            "R08_验收": {"objective_basis": False, "basis_evidence": "（6）履约验收标准：设备验收合格30日内组织验收"},
        }
        risks = adjudicate_risks(evidence)
        r08 = [r for r in risks if r["risk_type"] == "R08"]
        self.assertEqual(len(r08), 1)
        self.assertEqual(r08[0]["clause_text"], "（6）履约验收标准：设备验收合格30日内组织验收")


class TestRevisionApi(unittest.TestCase):
    """API 层：修订持久化、会话重载、修订版下载、无修改提示。"""

    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        eng = create_engine(f"sqlite:///{Path(tmp.name) / 't.db'}", connect_args={"timeout": 30})
        Base.metadata.create_all(eng)
        return tmp, eng, sessionmaker(bind=eng)

    def _user(self):
        u = mock.MagicMock()
        u.id = 1
        u.role = "uploader"
        return u

    def _add_contract(self, S, docx_path, name="原合同.docx"):
        s = S()
        c = Contract(user_id=1, file_name=name, stored_path=str(docx_path),
                     parsed_text="A条款原文\nB条款原文", status="completed")
        s.add(c)
        s.commit()
        cid = c.id
        s.close()
        return cid

    def test_revise_persists_and_reload(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx, ["A条款原文", "B条款原文"])
                cid = self._add_contract(S, docx)

                fake_rag = types.ModuleType("ai.rag")
                fake_rag.search_knowledge = mock.MagicMock(return_value=None)
                _RESULT = {"revised_clause": "A条款修订", "constraints": ["不超过20%"],
                           "legal_basis": ["民法典585条"], "remaining_risks": [],
                           "explanation": "已修订"}

                s = S()
                with mock.patch.dict(sys.modules, {"ai.rag": fake_rag}), \
                     mock.patch.object(contracts, "revise_clause", return_value=_RESULT):
                    res = contracts.revise_contract_clause(
                        cid,
                        contracts.ReviseRequest(clause_text="A条款原文", instruction="改违约金",
                                                scope="clause", clause_key="1", clause_no=""),
                        db=s, current_user=self._user())
                s.close()
                self.assertEqual(res["code"], 0)
                self.assertIn("revision_id", res["data"])

                # 刷新：GET revisions 返回历史会话
                s = S()
                revs = contracts.get_contract_revisions(cid, db=s, current_user=self._user())
                s.close()
                self.assertEqual(len(revs["data"]), 1)
                self.assertEqual(revs["data"][0]["revised_clause"], "A条款修订")
                self.assertEqual(revs["data"][0]["clause_key"], "1")
                self.assertEqual(revs["data"][0]["legal_basis"], ["民法典585条"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_revise_reads_audit_record_anchor(self):
        """revise 端点直接从 AuditRecord.clause_position.original_text 读锚点，不再根据 evidence 猜。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx, ["四、合同验收（6）履约验收标准：设备验收合格30日内组织验收。五、违约责任。"])
                cid = self._add_contract(S, docx)

                from models.audit_record import AuditRecord
                from models.clause_revision import ClauseRevision
                s = S()
                rec = AuditRecord(contract_id=cid, audit_batch="b1", risk_type="R08",
                                  risk_level="medium", clause_text="验收标准与验收方式：本合同项下的验收采用人工审核方式。",
                                  detection_method="evidence", result_status="valid",
                                  clause_position={"original_text": "（6）履约验收标准：设备验收合格30日内组织验收"})
                s.add(rec); s.commit(); rid = rec.id; s.close()

                fake_rag = types.ModuleType("ai.rag")
                fake_rag.search_knowledge = mock.MagicMock(return_value=None)
                _RESULT = {"revised_clause": "（6）履约验收标准：设备验收合格30日内组织人工审核并出具验收书",
                           "constraints": [], "legal_basis": [], "remaining_risks": [], "explanation": "ok"}

                s = S()
                with mock.patch.dict(sys.modules, {"ai.rag": fake_rag}), \
                     mock.patch.object(contracts, "revise_clause", return_value=_RESULT):
                    res = contracts.revise_contract_clause(
                        cid,
                        contracts.ReviseRequest(clause_text="验收标准与验收方式：本合同项下的验收采用人工审核方式。",
                                                instruction="改", scope="clause", clause_key=str(rid), clause_no=""),
                        db=s, current_user=self._user())
                s.close()
                self.assertEqual(res["code"], 0)

                s = S()
                rev = s.query(ClauseRevision).filter(ClauseRevision.contract_id == cid).first()
                s.close()
                self.assertEqual(rev.original_clause_text, "（6）履约验收标准：设备验收合格30日内组织验收")
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_download_revised_docx(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx, ["A条款原文", "B条款原文"])
                cid = self._add_contract(S, docx)

                # 直接写一条修订（跳过 LLM）
                from models.clause_revision import ClauseRevision
                s = S()
                s.add(ClauseRevision(contract_id=cid, scope="clause", clause_key="1",
                                     clause_text="A条款原文", instruction="改",
                                     revised_clause="A条款修订", original_clause_text="A条款原文"))
                s.commit()
                s.close()

                s = S()
                with mock.patch.object(contracts, "UPLOAD_DIR", fdir):
                    resp = contracts.download_revised_docx(cid, background_tasks=mock.MagicMock(), db=s, current_user=self._user())
                s.close()
                self.assertTrue(resp.path.endswith(".docx"))
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                self.assertEqual(texts, ["A条款修订", "B条款原文"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_download_without_revisions_400(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx, ["A条款原文"])
                cid = self._add_contract(S, docx)
                s = S()
                with mock.patch.object(contracts, "UPLOAD_DIR", fdir):
                    with self.assertRaises(HTTPException) as ctx:
                        contracts.download_revised_docx(cid, background_tasks=mock.MagicMock(), db=s, current_user=self._user())
                s.close()
                self.assertEqual(ctx.exception.status_code, 400)
        finally:
            eng.dispose()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
