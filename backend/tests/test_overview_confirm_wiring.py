"""「纳入方案」真实落库链路（P0-4）测试。

背景：`POST /overview/confirm` 后端早已实现并有覆盖，但前端「纳入方案」过去只是本地
UI 状态，**从未调用过该接口**。本轮把它接通后，前端调用链精确等于：

    POST /contracts/{id}/overview/plan     生成方案（只写方案表）
    POST /contracts/{id}/overview/confirm  逐项确认 → 落库 ClauseRevision(adopted=True)
    GET  /contracts/{id}/overview/proposals 刷新后读回 confirmed_ids（"已纳入"的权威来源）

因此本文件做两件事：
1. **按前端调用顺序**跑一遍真实链路，断言：ClauseRevision 真的被创建、`adopted=True`
   真的落库、`confirmed_ids` 真的可读回（= 刷新页面后状态正确）、重复确认不会重复落库；
2. 用源码断言把前端接线钉死（必须 import 并调用 confirmOverviewProposal、必须从
   confirmed_ids 派生"已纳入"，不得再出现本地 includedItemIds 会话态）。

运行（backend 目录下）：
    python -m pytest tests/test_overview_confirm_wiring.py -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from models.clause_revision import ClauseRevision  # noqa: E402
from models.revision_proposal import RevisionProposal  # noqa: E402
import api.overview as overview  # noqa: E402

from tests.test_overview_session import (  # noqa: E402
    OverviewSessionTestBase, _fake_plan, _make_docx, CMP_ANCHOR, CMP_KEY,
)


class OverviewConfirmWiringTest(OverviewSessionTestBase):
    """① 前端调用顺序的端到端落库验证。"""

    def test_confirm_chain_persists_and_survives_refresh(self):
        """plan → confirm → proposals（模拟刷新）→ 重复 confirm。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._seed_special_sessions(S, cid)

                # ── 步骤 1：生成方案（前端 generateProposal → POST /overview/plan） ──
                plan = self._plan(S, cid, plan_json=_fake_plan(items=[{
                    "operation": "replace", "target_session_key": CMP_KEY, "clause_no": "2",
                    "original_quote": CMP_ANCHOR,
                    "revised_clause": "第二条 验收标准与验收方式：采用人工审核与第三方复核。",
                    "reason": "统一验收安排", "legal_basis": [],
                }]))["data"]
                pid = plan["proposal_id"]
                item = plan["items"][0]
                item_id = str(item["id"])
                self.assertTrue(item["resolved"], "已定位的项才允许被纳入")
                self.assertEqual(plan["confirmed_ids"], [], "生成方案时不应有任何已确认项")

                # 生成方案本身不产生任何修订（与前端「纳入前不显示已纳入」一致）
                s = S()
                before = s.query(ClauseRevision).filter(ClauseRevision.contract_id == cid).count()
                s.close()

                # ── 步骤 2：点击「纳入方案」（前端 toggleIncludeItem → POST /overview/confirm） ──
                res = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(id=item_id)])["data"]
                self.assertEqual(res["failed"], [], "不应有失败项")
                self.assertEqual(len(res["applied"]), 1)
                applied = res["applied"][0]
                self.assertEqual(str(applied["id"]), item_id)
                self.assertIsNotNone(applied["revision_id"], "必须真的创建了 ClauseRevision")
                self.assertTrue(applied["exportable"], "确认后的修订应可进入 DOCX 导出集合")

                # ── 步骤 3：真的落库了 ClauseRevision，且 adopted=True ──
                s = S()
                rev = s.query(ClauseRevision).filter(
                    ClauseRevision.id == applied["revision_id"]).first()
                self.assertIsNotNone(rev)
                self.assertTrue(rev.adopted, "总体方案确认落库即 adopted（与专项会话同一语义）")
                self.assertEqual(rev.clause_key, applied["clause_key"])
                self.assertIn(rev.operation, ("replace", "add_clause"))
                after = s.query(ClauseRevision).filter(ClauseRevision.contract_id == cid).count()
                self.assertEqual(after - before, 1, "一次纳入只应新增一条修订")
                s.close()

                # ── 步骤 4：模拟刷新页面 → 后端必须能读回"已纳入"状态 ──
                rows = self._list_proposals(S, cid)
                refreshed = next(p for p in rows if p["proposal_id"] == pid)
                self.assertIn(item_id, [str(x) for x in refreshed["confirmed_ids"]],
                              "刷新后 confirmed_ids 必须仍包含该项（前端据此恢复已纳入状态）")
                self.assertEqual(refreshed["counts"]["confirmed"], 1)
                self.assertEqual(refreshed["status"], "applied")

                # ── 步骤 5：重复点击不会制造重复 revision ──
                res2 = self._confirm(S, cid, pid, [overview.OverviewConfirmItem(id=item_id)])["data"]
                self.assertEqual(res2["applied"], [], "重复确认不得再落库")
                self.assertEqual(len(res2["failed"]), 1)
                self.assertIn("重复确认", res2["failed"][0]["reason"])

                s = S()
                final = s.query(ClauseRevision).filter(ClauseRevision.contract_id == cid).count()
                self.assertEqual(final, after, "重复确认后修订总数不得变化")
                confirmed = s.query(ClauseRevision).filter(
                    ClauseRevision.contract_id == cid,
                    ClauseRevision.clause_key == applied["clause_key"],
                    ClauseRevision.adopted.is_(True)).count()
                self.assertEqual(confirmed, 1, "同一会话只能有一条 adopted 修订")
                s.close()
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_confirm_batch_of_two_items_creates_two_revisions(self):
        """一次纳入多项（前端逐项调用；这里验证后端逐项落库互不影响）。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._seed_special_sessions(S, cid)

                plan = self._plan(S, cid, plan_json=_fake_plan(items=[
                    {"operation": "replace", "target_session_key": CMP_KEY, "clause_no": "2",
                     "original_quote": CMP_ANCHOR,
                     "revised_clause": "第二条 验收标准与验收方式：人工审核并出具验收书。",
                     "reason": "验收安排", "legal_basis": []},
                    {"operation": "add_clause", "target_session_key": CMP_KEY,
                     "revised_clause": "第X条 不可抗力：因不可抗力不能履行合同的，部分或全部免除责任。",
                     "reason": "补不可抗力", "legal_basis": [],
                     "position": {"anchor": "2"}},
                ]))["data"]
                pid = plan["proposal_id"]
                ids = [str(i["id"]) for i in plan["items"]]

                res = self._confirm(S, cid, pid,
                                    [overview.OverviewConfirmItem(id=i) for i in ids])["data"]
                self.assertEqual(len(res["applied"]), 2)
                self.assertEqual(res["failed"], [])

                rows = self._list_proposals(S, cid)
                refreshed = next(p for p in rows if p["proposal_id"] == pid)
                self.assertEqual(sorted(str(x) for x in refreshed["confirmed_ids"]), sorted(ids))
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_unlocated_item_cannot_be_included(self):
        """未定位项：前端会禁用按钮；即便绕过，后端也必须拒绝且不落库。"""
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx)
                cid = self._add_contract(S, docx)
                self._seed_special_sessions(S, cid)

                plan = self._plan(S, cid, plan_json=_fake_plan(items=[{
                    "operation": "replace", "clause_no": "9",
                    "original_quote": "合同里根本不存在的这一段原文绝不匹配任何位置",
                    "revised_clause": "第九条 某某条款。", "reason": "x", "legal_basis": [],
                }]))["data"]
                item = plan["items"][0]
                self.assertFalse(item["resolved"], "该构造项应无法定位")

                res = self._confirm(S, cid, plan["proposal_id"],
                                    [overview.OverviewConfirmItem(id=str(item["id"]))])["data"]
                self.assertEqual(res["applied"], [])
                self.assertTrue(res["failed"][0].get("needs_location"))
        finally:
            eng.dispose()
            tmp.cleanup()

    def _list_proposals(self, S, cid):
        s = S()
        try:
            return overview.list_overview_proposals(cid, db=s, current_user=self._user())["data"]
        finally:
            s.close()


class OverviewConfirmFrontendSourceTest(unittest.TestCase):
    """② 前端接线静态断言 —— 防止退回"纯前端会话态"。"""

    SRC = _BACKEND_DIR.parent / "frontend" / "src"

    def _read(self, rel):
        p = self.SRC / rel
        self.assertTrue(p.is_file(), f"找不到 {p}")
        return p.read_text(encoding="utf-8")

    def test_composable_imports_and_calls_confirm_api(self):
        src = self._read("composables/useContractWorkspace.js")
        self.assertRegex(
            src,
            r"import\s*\{[^}]*\bconfirmOverviewProposal\b[^}]*\}\s*from\s*'\.\./api/contract\.js'",
            "必须显式 import confirmOverviewProposal",
        )
        self.assertRegex(
            src,
            r"await\s+confirmOverviewProposal\(",
            "「纳入方案」必须真的 await 调用该接口",
        )
        self.assertIn("overview/confirm", self._read("api/contract.js"))

    def test_no_local_included_item_state_remains(self):
        src = self._read("composables/useContractWorkspace.js")
        # 允许注释里提到旧实现（解释为何删除），但不得再出现任何可执行引用
        for line in src.splitlines():
            code = line.strip()
            if code.startswith("//") or code.startswith("*") or code.startswith("/*"):
                continue
            self.assertNotIn(
                "includedItemIds", code,
                f"不得再维护本地 includedItemIds 会话态：{code}",
            )

    def test_included_state_derived_from_confirmed_ids(self):
        src = self._read("composables/useContractWorkspace.js")
        self.assertIn("confirmed_ids", src, "「已纳入」必须来自后端 confirmed_ids")
        self.assertIn("confirmedItemIds", src)
        self.assertIn("isItemConfirmed", src)
        # sessionIncluded 也必须走 confirmed 判据
        idx = src.index("function sessionIncluded")
        body = src[idx:idx + 900]
        self.assertIn("confirmedItemIds", body)

    def test_panel_uses_persisted_state_and_honest_copy(self):
        src = self._read("views/contract-detail/OverviewPlanPanel.vue")
        self.assertIn("ws.isItemConfirmed", src, "面板必须用后端持久化状态判断已纳入")
        self.assertNotIn("includedItemIds", src, "面板不得再读本地会话态")
        # 旧文案断言「合同还没有被改动」，与接通后的真实语义冲突，必须已删除
        self.assertNotIn("合同还没有被改动", src)
        self.assertIn("正式写入本合同的修改记录", src)

        # 「取消纳入」只能作为解释性注释出现，不得再是任何按钮文案/可执行调用
        self.assertNotRegex(
            src,
            r">\s*取消纳入\s*<",
            "已落库项不得再提供「取消纳入」按钮（后端无 un-confirm 端点）",
        )
        # 模板区（</template> 之前）不得出现该文案
        template_part = src.split("</template>")[0]
        self.assertNotIn("取消纳入", template_part)
        # skip() 不得再静默调用 toggleIncludeItem 假装撤销
        idx = src.index("function skip(")
        self.assertNotIn("toggleIncludeItem", src[idx:idx + 700],
                         "skip 不得再自动取消纳入（会给人已撤销落库的错觉）")

    def test_confirm_failure_never_reports_success(self):
        """失败路径必须 error 提示，且只在 applied 命中时才提示成功。"""
        src = self._read("composables/useContractWorkspace.js")
        idx = src.index("async function toggleIncludeItem")
        body = src[idx:idx + 3200]
        self.assertIn("applied", body)
        self.assertIn("failed", body)
        self.assertIn("ElMessage.error", body)
        # 成功提示必须晚于（= 在）applied 命中分支内
        self.assertLess(body.index("ElMessage.error"), body.index("type: 'success'"))

    def test_adopt_endpoint_untouched(self):
        """既有单条款 /revisions/{id}/adopt 链路必须保持存在且未被替换。"""
        src = self._read("api/contract.js")
        self.assertIn("/revisions/${revisionId}/adopt", src)
        ws = self._read("composables/useContractWorkspace.js")
        self.assertIn("adoptRevision", ws)


if __name__ == "__main__":
    unittest.main()
