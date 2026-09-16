"""总体修改会话（总控会话）后端能力测试。

覆盖本轮重构的核心语义：
1. 只读总览：`GET /overview` 汇总全部专项会话的
   原文 / 当前最新修改结果 / 法律依据 / 剩余风险 / 定位状态 / 导出状态；
2. 结构化方案：`POST /overview/plan` 把「整份合同 + 各专项会话当前结果 + 用户整体要求」
   送进 LLM，产出**逐项**修改方案，并且**只写方案表、不写任何 ClauseRevision**；
3. 用户确认：`POST /overview/confirm` 把具体修改项转换成现有安全的
   `clause` / `add_clause` revision（原文锚点与 `POST /revise` 完全同源）；
4. 安全边界：
   - 未建立可靠定位的修改项**拒绝落库**（不产生漏改/改错位置的 DOCX）；
   - 用户可以自己指定原文片段完成定位（AI 辅助 + 人确认）；
   - `overview+replace` 永远不会被写进修订版 DOCX（方案本身也不参与导出）；
   - 已确认的项不可重复确认。

运行（backend 目录下）：
    python -m unittest tests.test_overview_session -v
"""
import json
import os
import sys
import tempfile
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
from fastapi import BackgroundTasks, HTTPException  # noqa: E402

from database import Base  # noqa: E402
from models.contract import Contract  # noqa: E402
from models.audit_record import AuditRecord  # noqa: E402
from models.clause_revision import ClauseRevision  # noqa: E402
from models.revision_proposal import RevisionProposal  # noqa: E402
import api.contracts as contracts  # noqa: E402
import api.overview as overview  # noqa: E402

DOC_PARAS = [
    "第一条 合同标的与范围",
    "第二条 验收标准与验收方式：本合同项下的验收采用人工审核方式，验收合格后30日内组织验收。",
    "第三条 违约责任与违约金上限为合同总价的30%。",
    "第四条 争议解决：提交甲方所在地人民法院裁决。",
]
PARSED = "\n".join(DOC_PARAS)

RISK_KEY = "1"
CMP_KEY = "__cmp__验收标准"
RISK_ANCHOR = DOC_PARAS[2]
CMP_ANCHOR = "验收标准与验收方式：本合同项下的验收采用人工审核方式"


def _make_docx(path, paragraphs=DOC_PARAS):
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))
    return str(path)


def _fake_plan(summary="统一收紧验收与违约责任。", items=None):
    """构造一次 AI 方案返回（避免真调 LLM）。"""
    return json.dumps({"summary": summary, "items": items if items is not None else []},
                      ensure_ascii=False)


class OverviewSessionTestBase(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        eng = create_engine(f"sqlite:///{Path(tmp.name) / 't.db'}", connect_args={"timeout": 30})
        Base.metadata.create_all(eng)
        self.uploads = Path(tmp.name) / "uploads"
        self.uploads.mkdir(exist_ok=True)
        return tmp, eng, sessionmaker(bind=eng)

    def _user(self, uid=1, role="uploader"):
        u = mock.MagicMock()
        u.id = uid
        u.role = role
        return u

    def _add_contract(self, S, docx_path, parsed=PARSED):
        s = S()
        c = Contract(user_id=1, file_name="原合同.docx", stored_path=str(docx_path),
                     parsed_text=parsed, contract_type="买卖合同", status="completed")
        s.add(c)
        s.commit()
        cid = c.id
        s.close()
        return cid

    def _seed_special_sessions(self, S, cid):
        """两条专项会话：风险来源（有审核锚点）+ 比对来源（无锚点，靠 _locate_clause）。"""
        s = S()
        rec = AuditRecord(contract_id=cid, audit_batch="b1", risk_type="R01", risk_level="high",
                          clause_text="违约责任与违约金上限为合同总价的30%",
                          detection_method="evidence", result_status="valid",
                          clause_position={"original_text": RISK_ANCHOR, "clause_no": 3})
        s.add(rec)
        s.commit()
        rid = rec.id
        s.add(ClauseRevision(
            contract_id=cid, scope="clause", operation="replace", clause_key=str(rid),
            clause_no="3", clause_text="第三条 违约责任与违约金上限为合同总价的30%。",
            original_clause_text=RISK_ANCHOR, instruction="下调违约金上限",
            revised_clause="违约责任与违约金上限为合同总价的15%。",
            explanation="违约金过高", legal_basis=["民法典第585条"],
            remaining_risks=["仍需确认实际损失举证责任"],
        ))
        s.add(ClauseRevision(
            contract_id=cid, scope="clause", operation="replace", clause_key=CMP_KEY,
            clause_no="2", clause_text=CMP_ANCHOR,
            original_clause_text="第二条 验收标准与验收方式：本合同项下的验收采用人工审核方式",
            instruction="补全验收方式",
            revised_clause="第二条 验收标准与验收方式：采用人工审核并出具验收书。",
            explanation="验收方式缺失", legal_basis=[], remaining_risks=[],
        ))
        s.commit()
        s.close()
        return str(rid)

    def _add_clause_revision(self, S, cid, **kw):
        s = S()
        rev = ClauseRevision(contract_id=cid, **kw)
        s.add(rev)
        s.commit()
        rid = rev.id
        s.close()
        return rid

    def _aggregate(self, S, cid, user=None):
        s = S()
        try:
            return overview.get_contract_overview(cid, db=s, current_user=user or self._user())
        finally:
            s.close()

    def _plan(self, S, cid, instruction="整体收紧", plan_json=None, user=None):
        s = S()
        try:
            with mock.patch.object(overview.llm_client, "chat",
                                   return_value=plan_json if plan_json is not None
                                   else _fake_plan()):
                return overview.create_overview_plan(
                    cid, overview.OverviewPlanRequest(instruction=instruction),
                    db=s, current_user=user or self._user())
        finally:
            s.close()

    def _confirm(self, S, cid, proposal_id, items, user=None):
        s = S()
        try:
            return overview.confirm_overview_plan(
                cid, overview.OverviewConfirmRequest(proposal_id=proposal_id, items=items),
                db=s, current_user=user or self._user())
        finally:
            s.close()

    def _download(self, S, cid):
        s = S()
        try:
            with mock.patch.object(contracts, "UPLOAD_DIR", str(self.uploads)):
                return contracts.download_revised_docx(
                    cid, BackgroundTasks(), db=s, current_user=self._user())
        finally:
            s.close()


class TestOverviewAggregate(OverviewSessionTestBase):
    """① 查看当前合同所有专项修改会话 + 每个会话的完整信息。"""

    def test_aggregate_lists_special_sessions_with_full_detail(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                rid = self._seed_special_sessions(S, cid)

                data = self._aggregate(S, cid)["data"]
                self.assertEqual(data["overview"]["key"], "__overview__")
                self.assertEqual(data["overview"]["scope"], "overview")
                # 总体会话自身的 replace 讨论稿永不写 DOCX（口径未被本轮改动）
                self.assertFalse(data["overview"]["writes_docx"])

                by_key = {s["key"]: s for s in data["sessions"]}
                self.assertEqual(set(by_key), {rid, CMP_KEY})

                risk = by_key[rid]
                self.assertEqual(risk["kind"], "risk")
                self.assertEqual(risk["operation"], "replace")
                self.assertEqual(risk["original_text"], RISK_ANCHOR)      # 原文
                self.assertEqual(risk["original_source"], "anchor")
                self.assertIn("15%", risk["revised_clause"])               # 当前最新修改结果
                self.assertEqual(risk["legal_basis"], ["民法典第585条"])     # 法律依据
                self.assertEqual(risk["remaining_risks"], ["仍需确认实际损失举证责任"])  # 剩余风险
                self.assertTrue(risk["located"])                           # 定位状态
                self.assertEqual(risk["location_state"], "located")
                self.assertTrue(risk["export"]["exportable"])
                self.assertEqual(risk["clause_no"], "3")

                cmp_s = by_key[CMP_KEY]
                self.assertEqual(cmp_s["kind"], "cmp")
                self.assertTrue(cmp_s["located"])
                self.assertEqual(cmp_s["location_state"], "located")
                self.assertTrue(cmp_s["export"]["exportable"])
                self.assertEqual(cmp_s["original_source"], "anchor")
                self.assertEqual(cmp_s["clause_no"], "2")

                self.assertEqual(data["counts"]["by_kind"]["risk"], 1)
                self.assertEqual(data["counts"]["by_kind"]["cmp"], 1)
                self.assertEqual(data["export"]["blocker_count"], 0)
                self.assertTrue(data["export"]["ready"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_aggregate_is_read_only_and_needs_no_llm(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._seed_special_sessions(S, cid)
                with mock.patch.object(overview.llm_client, "chat",
                                       side_effect=AssertionError("总览不得调用 LLM")):
                    data = self._aggregate(S, cid)["data"]
                self.assertEqual(len(data["sessions"]), 2)
                s = S()
                before = s.query(ClauseRevision).count()
                s.close()
                self.assertEqual(before, 2, "总览不得产生任何新的修订记录")
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_overview_add_clause_is_listed_and_exportable(self):
        """overview+add_clause 是唯一会写进 DOCX 的 overview 记录，必须在总览里可见。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._add_clause_revision(
                    S, cid, scope="overview", operation="add_clause", clause_key="__overview__",
                    clause_text="", instruction="新增不可抗力", revised_clause="第五条 不可抗力条款。",
                    position={"anchor": "四", "hint": "第四条之后"})
                data = self._aggregate(S, cid)["data"]
                adds = [s for s in data["sessions"] if s["operation"] == "add_clause"]
                self.assertEqual(len(adds), 1)
                self.assertTrue(adds[0]["export"]["exportable"])
                self.assertEqual(adds[0]["kind"], "overview")
        finally:
            eng.dispose()
            tmp.cleanup()


class TestOverviewPlan(OverviewSessionTestBase):
    """② 在总体会话中提出整体修改要求 → AI 形成结构化综合方案。"""

    def _capture_plan(self, S, cid, plan_json):
        captured = []

        def fake_chat(prompt, temperature=0.1):
            captured.append(prompt)
            return plan_json

        s = S()
        try:
            with mock.patch.object(overview.llm_client, "chat", side_effect=fake_chat):
                res = overview.create_overview_plan(
                    cid, overview.OverviewPlanRequest(instruction="结合前面讨论统一修改"),
                    db=s, current_user=self._user())
        finally:
            s.close()
        return res, captured

    def test_plan_context_carries_full_contract_and_all_sessions(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                rid = self._seed_special_sessions(S, cid)

                plan_json = _fake_plan(items=[{
                    "operation": "replace",
                    "target_session_key": rid,
                    "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "第三条 违约责任与违约金上限为合同总价的10%，且不超过实际损失。",
                    "reason": "与验收条款整体协调",
                    "legal_basis": ["民法典第585条"],
                }])
                res, captured = self._capture_plan(S, cid, plan_json)
                self.assertEqual(res["code"], 0)
                self.assertEqual(len(captured), 1)
                prompt = captured[0]

                # 整份合同 + 各专项会话当前结果 + 用户整体要求，都必须真的进入 prompt
                self.assertIn("第一条 合同标的与范围", prompt)
                self.assertIn("第四条 争议解决", prompt)
                self.assertIn(f"session_key={rid}", prompt)
                self.assertIn(CMP_KEY, prompt)
                self.assertIn("民法典第585条", prompt)          # 会话的法律依据
                self.assertIn("仍需确认实际损失举证责任", prompt)  # 会话的剩余风险
                self.assertIn("结合前面讨论统一修改", prompt)
                # 必须明确要求"只输出具体修改项、不要整份重写"
                self.assertIn("绝对不要输出整份合同的重写稿", prompt)

                data = res["data"]
                self.assertEqual(data["counts"]["total"], 1)
                self.assertEqual(data["counts"]["needs_location"], 0)
                item = data["items"][0]
                self.assertTrue(item["resolved"], "挂到已定位专项会话的项必须 resolved")
                self.assertEqual(item["resolved_by"], "session")
                # 锚点必须是合同正文中的逐字片段（句末标点是否含入由既有定位窗口决定）
                self.assertIn(item["anchor_text"], PARSED)
                self.assertIn("违约责任与违约金上限为合同总价的30%", item["anchor_text"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_plan_only_writes_proposal_not_revisions(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._seed_special_sessions(S, cid)
                plan_json = _fake_plan(items=[{
                    "operation": "replace", "target_session_key": "", "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "第三条 违约金上限为合同总价的10%。",
                    "reason": "下调", "legal_basis": [],
                }])
                res, _ = self._capture_plan(S, cid, plan_json)
                self.assertEqual(res["data"]["status"], "draft")

                s = S()
                self.assertEqual(s.query(RevisionProposal).count(), 1)
                self.assertEqual(s.query(ClauseRevision).count(), 2,   # 仍是种子里的两条
                                 "生成方案阶段绝不能写任何 ClauseRevision")
                s.close()

                # 刷新后仍能取回方案
                s = S()
                rows = overview.list_overview_proposals(cid, db=s, current_user=self._user())["data"]
                one = overview.get_overview_proposal(
                    cid, res["data"]["proposal_id"], db=s, current_user=self._user())["data"]
                s.close()
                self.assertEqual(len(rows), 1)
                self.assertEqual(one["proposal_id"], res["data"]["proposal_id"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_plan_marks_unlocatable_items_as_needing_location(self):
        """定位不了就诚实标记，不假装能改。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                plan_json = _fake_plan(items=[{
                    "operation": "replace", "target_session_key": "", "clause_no": "",
                    "original_quote": "合同里根本没有的这句话",
                    "revised_clause": "改后内容", "reason": "x", "legal_basis": [],
                }])
                res, _ = self._capture_plan(S, cid, plan_json)
                item = res["data"]["items"][0]
                self.assertFalse(item["resolved"])
                self.assertTrue(item["blocking_reason"])
                self.assertEqual(res["data"]["counts"]["needs_location"], 1)
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_plan_rejects_unparseable_llm_output(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                with self.assertRaises(HTTPException) as ctx:
                    self._plan(S, cid, plan_json="抱歉，我无法完成。")
                self.assertEqual(ctx.exception.status_code, 502)
        finally:
            eng.dispose()
            tmp.cleanup()


class TestOverviewConfirm(OverviewSessionTestBase):
    """③ 用户确认具体修改项 → 转换成现有安全的 clause/add_clause revision。"""

    def _propose(self, S, cid, items):
        return self._plan(S, cid, plan_json=_fake_plan(items=items))["data"]

    def test_confirm_replace_into_anchored_session_keeps_chain_and_exports(self):
        """确认到"已有锚点"的专项会话：clause_text 必须延续链条，锚点沿用会话锚点。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._seed_special_sessions(S, cid)

                data = self._propose(S, cid, [{
                    "operation": "replace", "target_session_key": CMP_KEY, "clause_no": "2",
                    "original_quote": CMP_ANCHOR,
                    "revised_clause": "第二条 验收标准与验收方式：采用人工审核、第三方复核并出具验收书。",
                    "reason": "整体协调验收安排", "legal_basis": [],
                }])
                pid = data["proposal_id"]
                item = data["items"][0]
                self.assertTrue(item["resolved"])
                self.assertEqual(item["anchor_text"], "第二条 验收标准与验收方式：本合同项下的验收采用人工审核方式")

                res = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(id="p1")])
                self.assertEqual(res["data"]["failed"], [])
                out = res["data"]["applied"][0]
                self.assertEqual(out["clause_key"], CMP_KEY)
                self.assertEqual(out["original_clause_text"],
                                 "第二条 验收标准与验收方式：本合同项下的验收采用人工审核方式")
                self.assertEqual(out["clause_text"],
                                 "第二条 验收标准与验收方式：本合同项下的验收采用人工审核方式")
                self.assertTrue(out["exportable"])

                resp = self._download(S, cid)
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                os.remove(resp.path)
                # 替换生效，且条款编号不会被写两遍（「第二条 第二条」）
                self.assertEqual(
                    texts[1],
                    "验收标准与验收方式：采用人工审核、第三方复核并出具验收书。，验收合格后30日内组织验收。",
                )
                self.assertEqual(texts[0], DOC_PARAS[0])
                self.assertIn("合同总价的15%", texts[2])   # 种子里的风险会话修订同样生效
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_replace_into_multi_round_session_reuses_session_anchor(self):
        """专项会话已改过两轮：确认必须沿用会话锚点（与 DOCX 链式归并口径一致）。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                rid = self._seed_special_sessions(S, cid)
                # 第二轮续改：clause_text 已是上一轮的修订稿（链条 root → A2 归并回原文锚点）
                self._add_clause_revision(
                    S, cid, scope="clause", operation="replace", clause_key=rid, clause_no="3",
                    clause_text="违约责任与违约金上限为合同总价的30%",
                    original_clause_text=RISK_ANCHOR, instruction="再紧一点",
                    revised_clause="第三条 违约责任与违约金上限为合同总价的12%。")
                data = self._propose(S, cid, [{
                    "operation": "replace", "target_session_key": rid, "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "第三条 违约责任与违约金上限为合同总价的10%。",
                    "reason": "整体压降", "legal_basis": ["民法典第585条"],
                }])
                res = self._confirm(S, cid, data["proposal_id"],
                                    [overview.OverviewConfirmItem(id="p1")])
                out = res["data"]["applied"][0]
                self.assertIn(out["original_clause_text"], PARSED)
                self.assertIn("违约责任与违约金上限为合同总价的30%", out["original_clause_text"])
                # 链条根沿用会话首轮 clause_text → 与 DOCX 链式归并口径一致（最终只落最新结果）
                self.assertEqual(out["clause_text"], RISK_ANCHOR)
                self.assertEqual(out["revised_clause"], "违约责任与违约金上限为合同总价的10%。")

                resp = self._download(S, cid)
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                os.remove(resp.path)
                self.assertIn("10%", texts[2])
                self.assertNotIn("12%", texts[2])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_replace_writes_safe_clause_revision_and_exports(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                rid = self._seed_special_sessions(S, cid)

                # 先补一条"已建立锚点"的比对会话修订（幂等：与该会话自身锚点/结果一致，
                # 避免同一段落出现两个互相重叠的替换锚点——那是既有 DOCX 链路的硬约束）
                self._add_clause_revision(
                    S, cid, scope="clause", operation="replace", clause_key=CMP_KEY, clause_no="2",
                    clause_text=CMP_ANCHOR,
                    original_clause_text="第二条 验收标准与验收方式：本合同项下的验收采用人工审核方式",
                    instruction="补全验收方式",
                    revised_clause="第二条 验收标准与验收方式：采用人工审核并出具验收书。")

                data = self._propose(S, cid, [{
                    "operation": "replace", "target_session_key": rid, "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "第三条 违约责任与违约金上限为合同总价的10%。",
                    "reason": "整体压降违约金", "legal_basis": ["民法典第585条"],
                }])
                pid = data["proposal_id"]

                res = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(id="p1")])
                self.assertEqual(res["code"], 0)
                self.assertEqual(len(res["data"]["applied"]), 1)
                self.assertEqual(res["data"]["failed"], [])
                out = res["data"]["applied"][0]
                self.assertEqual(out["operation"], "replace")
                self.assertEqual(out["clause_key"], rid)                 # 挂回原专项会话
                self.assertEqual(out["original_clause_text"], RISK_ANCHOR)
                self.assertTrue(out["exportable"])

                s = S()
                revs = (s.query(ClauseRevision)
                        .filter(ClauseRevision.contract_id == cid)
                        .order_by(ClauseRevision.id.asc()).all())
                p = s.query(RevisionProposal).filter(RevisionProposal.id == pid).first()
                s.close()
                self.assertEqual(len(revs), 4)   # 2 条种子 + 1 条补锚点 + 1 条总体方案确认
                self.assertEqual(revs[-1].instruction.startswith("【总体会话综合方案确认】"), True)
                self.assertEqual(p.status, "applied")
                self.assertEqual(p.confirmed_ids, ["p1"])

                # DOCX 导出复用既有链路：本条替换生效、其余条款不变
                resp = self._download(S, cid)
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                os.remove(resp.path)
                self.assertIn("合同总价的10%", texts[2])
                self.assertNotIn("合同总价的30%", texts[2])
                self.assertEqual(texts[0], DOC_PARAS[0])
                self.assertEqual(texts[3], DOC_PARAS[3])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_add_clause_goes_through_existing_add_clause_path(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                data = self._propose(S, cid, [{
                    "operation": "add_clause", "target_session_key": "", "clause_no": "",
                    "original_quote": "",
                    "revised_clause": "不可抗力条款：因不可抗力不能履行的，部分或全部免除责任。",
                    "reason": "R09 缺失", "legal_basis": ["民法典第590条"],
                    "position": {"anchor": "四", "hint": "第四条之后"},
                }])
                self.assertTrue(data["items"][0]["resolved"])
                self.assertEqual(data["items"][0]["resolved_by"], "position")

                res = self._confirm(S, cid, data["proposal_id"],
                                    [overview.OverviewConfirmItem(id="p1")])
                out = res["data"]["applied"][0]
                self.assertEqual(out["operation"], "add_clause")
                self.assertEqual(out["clause_key"], "__overview__")       # 与既有 add_clause 口径一致
                self.assertIsNone(out["original_clause_text"])           # 新增不得带替换锚点
                self.assertEqual(out["position"], {"anchor": "四", "hint": "第四条之后"})

                resp = self._download(S, cid)
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                os.remove(resp.path)
                self.assertEqual(len(texts), 5)
                self.assertTrue(texts[4].startswith("五、"), texts[4])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_add_clause_without_position_is_refused(self):
        """位置必须由用户确认：AI 没给位置时拒绝落库并回传建议位置。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                data = self._propose(S, cid, [{
                    "operation": "add_clause", "target_session_key": "", "clause_no": "",
                    "original_quote": "", "revised_clause": "新增条款正文", "reason": "x",
                    "legal_basis": [], "position": None,
                }])
                item = data["items"][0]
                self.assertFalse(item["resolved"])
                self.assertIn("插入位置", item["blocking_reason"])
                self.assertIsNotNone(item["suggested_position"])   # 只给建议，不替用户决定

                res = self._confirm(S, cid, data["proposal_id"],
                                    [overview.OverviewConfirmItem(id="p1")])
                self.assertEqual(res["data"]["applied"], [])
                self.assertEqual(len(res["data"]["failed"]), 1)
                self.assertTrue(res["data"]["failed"][0]["needs_location"])
                s = S()
                self.assertEqual(s.query(ClauseRevision).count(), 0)
                s.close()

                # 用户补上位置后再确认 → 成功
                res2 = self._confirm(S, cid, data["proposal_id"], [overview.OverviewConfirmItem(
                    id="p1", position={"anchor": "四", "hint": "第四条之后"})])
                self.assertEqual(len(res2["data"]["applied"]), 1)
                self.assertEqual(res2["data"]["failed"], [])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_unlocated_replace_is_refused_then_user_can_specify_text(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                data = self._propose(S, cid, [{
                    "operation": "replace", "target_session_key": "", "clause_no": "",
                    "original_quote": "（AI 改写过的、原文里没有的一句话）",
                    "revised_clause": "第三条 违约责任与违约金上限为合同总价的10%。",
                    "reason": "下调", "legal_basis": [],
                }])
                pid = data["proposal_id"]

                res = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(id="p1")])
                self.assertEqual(res["data"]["applied"], [])
                self.assertTrue(res["data"]["failed"][0]["needs_location"])
                s = S()
                self.assertEqual(s.query(ClauseRevision).count(), 0)
                s.close()

                # 用户自己指出原文位置（AI 不能偷偷替用户决定改哪里）
                res2 = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(
                    id="p1", original_quote="违约责任与违约金上限为合同总价的30%")])
                self.assertEqual(res2["data"]["failed"], [])
                out = res2["data"]["applied"][0]
                self.assertIn(out["original_clause_text"], PARSED)
                self.assertIn("违约责任与违约金上限为合同总价的30%", out["original_clause_text"])
                self.assertTrue(out["exportable"])

                resp = self._download(S, cid)
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                os.remove(resp.path)
                self.assertIn("10%", texts[2])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_twice_is_refused(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                data = self._propose(S, cid, [{
                    "operation": "replace", "target_session_key": "", "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "第三条 违约责任与违约金上限为合同总价的10%。",
                    "reason": "x", "legal_basis": [],
                }])
                pid = data["proposal_id"]
                first = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(id="p1")])
                self.assertEqual(len(first["data"]["applied"]), 1)
                second = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(id="p1")])
                self.assertEqual(second["data"]["applied"], [])
                self.assertIn("已确认", second["data"]["failed"][0]["reason"])
                s = S()
                self.assertEqual(s.query(ClauseRevision).count(), 1)
                s.close()
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_rejects_item_from_another_proposal(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                data = self._propose(S, cid, [{
                    "operation": "replace", "target_session_key": "", "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "改后", "reason": "x", "legal_basis": [],
                }])
                res = self._confirm(S, cid, data["proposal_id"],
                                    [overview.OverviewConfirmItem(id="p99")])
                self.assertEqual(res["data"]["applied"], [])
                self.assertIn("不属于这份方案", res["data"]["failed"][0]["reason"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_empty_selection_is_rejected(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                data = self._propose(S, cid, [{
                    "operation": "replace", "target_session_key": "", "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "改后", "reason": "x", "legal_basis": [],
                }])
                with self.assertRaises(HTTPException) as ctx:
                    self._confirm(S, cid, data["proposal_id"], [])
                self.assertEqual(ctx.exception.status_code, 400)
        finally:
            eng.dispose()
            tmp.cleanup()


class TestOverviewSafetyBoundaries(OverviewSessionTestBase):
    """④ 安全边界：方案本身与 overview+replace 讨论稿都不得进入 DOCX。"""

    def test_proposal_never_writes_docx(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                plan_json = _fake_plan(items=[{
                    "operation": "replace", "target_session_key": "", "clause_no": "3",
                    "original_quote": "违约责任与违约金上限为合同总价的30%",
                    "revised_clause": "第三条 违约责任与违约金上限为合同总价的10%。",
                    "reason": "x", "legal_basis": [],
                }, {
                    "operation": "add_clause", "target_session_key": "", "clause_no": "",
                    "original_quote": "", "revised_clause": "新增条款",
                    "reason": "y", "legal_basis": [], "position": {"append": True},
                }])
                data = self._plan(S, cid, plan_json=plan_json)["data"]

                # 方案已生成但**用户尚未确认** → 不能有任何修订可导出
                s = S()
                self.assertEqual(s.query(ClauseRevision).count(), 0)
                s.close()
                with self.assertRaises(HTTPException) as ctx:
                    self._download(S, cid)
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertIn("还没有任何条款修改", str(ctx.exception.detail))

                # 只确认 replace 项时，新增项不落库（逐项确认，不整包写入）
                res = self._confirm(S, cid, data["proposal_id"],
                                    [overview.OverviewConfirmItem(id="p1")])
                self.assertEqual(len(res["data"]["applied"]), 1)
                s = S()
                self.assertEqual(s.query(ClauseRevision).count(), 1)
                s.close()
                resp = self._download(S, cid)
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                os.remove(resp.path)
                self.assertEqual(len(texts), 4)           # 只替换，未新增
                self.assertIn("10%", texts[2])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_legacy_overview_replace_record_still_never_enters_docx(self):
        """历史「整体讨论稿」（overview+replace）仍不写 DOCX，本轮口径未被改动。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._add_clause_revision(
                    S, cid, scope="overview", operation="replace", clause_key="__overview__",
                    clause_text=PARSED, instruction="整体重写",
                    revised_clause="整份合同被重写了一遍（不应写入文件）", original_clause_text=None)
                s = S()
                with self.assertRaises(HTTPException) as ctx:
                    with mock.patch.object(contracts, "UPLOAD_DIR", str(self.uploads)):
                        contracts.download_revised_docx(cid, BackgroundTasks(), db=s,
                                                        current_user=self._user())
                s.close()
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertIn("还没有任何条款修改", str(ctx.exception.detail))
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_plan_requires_parsed_text(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx, parsed="")
                with self.assertRaises(HTTPException) as ctx:
                    self._plan(S, cid)
                self.assertEqual(ctx.exception.status_code, 400)
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_permission_boundary(self):
        """他人的合同不可见（不可越权读取总体会话汇总）。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                with self.assertRaises(HTTPException) as ctx:
                    self._aggregate(S, cid, user=self._user(uid=999, role="uploader"))
                self.assertEqual(ctx.exception.status_code, 404)
        finally:
            eng.dispose()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
