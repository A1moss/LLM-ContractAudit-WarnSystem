"""只读条款定位（POST /contracts/{id}/locate-clause）测试。

覆盖：
1. text / clause_anchor 都为空 → 400
2. 原文唯一命中 → found=True + match_mode="exact"，original_text 是 parsed_text 的逐字子串
3. 多处命中、恰有一处在「（N）」子项 → found=True（复用 _pick_best_hit 消歧）
4. 多处命中、无子项 → found=False + 候选非空
5. 完全无命中 → found=False + candidates == []
6. 自然语言描述命中唯一关键词 → found=True + match_mode="keyword"
7. clause_anchor="三" → 定位第三条整条窗口；clause_anchor="九十九"（不存在）→ found=False
8. 只读证明：调用后 ClauseRevision 计数为 0、AuditRecord 计数与调用前一致

运行（backend 目录下）：
    python -m unittest tests.test_locate_clause -v
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
from fastapi import HTTPException  # noqa: E402

from database import Base  # noqa: E402
from models.contract import Contract  # noqa: E402
from models.audit_record import AuditRecord  # noqa: E402
from models.clause_revision import ClauseRevision  # noqa: E402
import api.contracts as contracts  # noqa: E402


# 一份带「第X条」结构的合同正文（条款编号锚点用）
_ANCHOR_TEXT = (
    "服务外包合同\n"
    "第一条 服务内容：乙方按约定提供服务。\n"
    "第二条 合同价款：总价人民币十万元。\n"
    "第三条 验收标准：由采购人组织人工审核，逐项核对履约标准。\n"
    "第四条 违约责任：逾期按日支付违约金。\n"
)


class TestLocateClauseApi(unittest.TestCase):
    """API 层：三种定位方式 + 只读保证。"""

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

    def _add_contract(self, S, parsed_text, status="completed"):
        s = S()
        c = Contract(user_id=1, file_name="原合同.docx", stored_path="x.docx",
                     parsed_text=parsed_text, status=status, contract_type="买卖合同")
        s.add(c)
        s.commit()
        cid = c.id
        s.close()
        return cid

    def _locate(self, S, cid, text="", clause_anchor=""):
        s = S()
        res = contracts.locate_contract_clause(
            cid, contracts.LocateClauseRequest(text=text, clause_anchor=clause_anchor),
            db=s, current_user=self._user())
        s.close()
        return res

    # --- 1. 空输入 → 400 -----------------------------------------------------
    def test_empty_text_and_anchor_400(self):
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, "第一条 服务内容：乙方按约定提供服务。")
            s = S()
            with self.assertRaises(HTTPException) as ctx:
                contracts.locate_contract_clause(
                    cid, contracts.LocateClauseRequest(text="   ", clause_anchor="  "),
                    db=s, current_user=self._user())
            s.close()
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("请提供", ctx.exception.detail)
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_no_parsed_text_400(self):
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, "")
            s = S()
            with self.assertRaises(HTTPException) as ctx:
                contracts.locate_contract_clause(
                    cid, contracts.LocateClauseRequest(text="任意片段"),
                    db=s, current_user=self._user())
            s.close()
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail, "no parsed text")
        finally:
            eng.dispose()
            tmp.cleanup()

    # --- 2. 原文唯一命中 -----------------------------------------------------
    def test_exact_unique_hit(self):
        parsed = "第一条 服务内容：乙方按约定提供服务。第二条 验收标准：设备验收合格30日内组织验收。"
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            d = self._locate(S, cid, text="设备验收合格30日内组织验收")["data"]
            self.assertTrue(d["found"])
            self.assertEqual(d["match_mode"], "exact")
            self.assertEqual(d["reason"], "原文唯一命中")
            self.assertIn(d["original_text"], parsed)          # 逐字子串
            self.assertEqual(parsed[d["start"]:d["end"]].strip(), d["original_text"])
            self.assertEqual(d["clause_no"], 2)
            self.assertEqual(d["clause_title"], "验收标准")
            self.assertEqual(len(d["candidates"]), 1)
        finally:
            eng.dispose()
            tmp.cleanup()

    # --- 3. 多处命中 +（N）子项消歧 ------------------------------------------
    def test_multi_hit_disambiguated_by_subitem(self):
        parsed = ("付款方式：设备正常运行生产30日并验收合格后，支付30%。"
                  "四、合同验收（6）履约验收标准：设备正常运行生产30日并验收合格后。")
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            d = self._locate(S, cid, text="设备正常运行生产30日并验收合格")["data"]
            self.assertTrue(d["found"])
            self.assertEqual(d["match_mode"], "exact")
            self.assertIn("（6）履约验收标准", d["original_text"])
            self.assertNotIn("支付30%", d["original_text"])
            self.assertIn(d["original_text"], parsed)
        finally:
            eng.dispose()
            tmp.cleanup()

    # --- 4. 多处命中、无法消歧 → 候选 ----------------------------------------
    def test_multi_hit_no_subitem_returns_candidates(self):
        parsed = "A设备正常运行生产30日并验收合格。B设备正常运行生产30日并验收合格。"
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            d = self._locate(S, cid, text="设备正常运行生产30日并验收合格")["data"]
            self.assertFalse(d["found"])
            self.assertEqual(d["match_mode"], "exact")
            self.assertEqual(d["original_text"], "")
            self.assertIsNone(d["start"])
            self.assertIsNone(d["end"])
            self.assertEqual(len(d["candidates"]), 2)
            self.assertIn("出现 2 处", d["reason"])
            for cand in d["candidates"]:
                self.assertIsInstance(cand["start"], int)
                self.assertIsInstance(cand["end"], int)
                self.assertIn(cand["original_text"], parsed)
            # 候选按 start 升序、去重
            starts = [c["start"] for c in d["candidates"]]
            self.assertEqual(starts, sorted(starts))
            self.assertEqual(len(starts), len(set(starts)))
        finally:
            eng.dispose()
            tmp.cleanup()

    # --- 5. 完全无命中 → candidates 空 ---------------------------------------
    def test_no_hit_at_all(self):
        parsed = "第一条 服务内容：乙方按约定提供服务。"
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            d = self._locate(S, cid, text="本次请求的条款在合同中完全不存在XYZ")["data"]
            self.assertFalse(d["found"])
            self.assertEqual(d["match_mode"], "")
            self.assertEqual(d["candidates"], [])
            self.assertEqual(d["original_text"], "")
            self.assertIsNone(d["start"])
            self.assertIsNone(d["end"])
            self.assertIn("未在合同正文中找到", d["reason"])
        finally:
            eng.dispose()
            tmp.cleanup()

    # --- 6. 自然语言描述 → 唯一关键词 ----------------------------------------
    def test_keyword_description_unique(self):
        parsed = "第三条 履约验收标准：由采购人组织人工审核，逐项核对履约标准。第四条 违约责任：逾期按日支付违约金。"
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            # 描述里除「履约验收标准」之外的 token 都不在正文中 → 唯一候选
            d = self._locate(S, cid, text="合同里的「履约验收标准」这一项")["data"]
            self.assertTrue(d["found"])
            self.assertEqual(d["match_mode"], "keyword")
            self.assertEqual(d["reason"], "已根据描述定位到唯一候选")
            self.assertIn("履约验收标准", d["original_text"])
            self.assertIn(d["original_text"], parsed)
            self.assertEqual(len(d["candidates"]), 1)
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_keyword_description_multiple_candidates(self):
        # 描述里两个 token 都在正文出现且各命中一次 → 2 处候选，交给用户确认（不替用户决定）
        # （用「」分隔 token：连续中文会被当成一个 token，无法作关键词）
        parsed = "第二条 服务内容：乙方提供驻场服务。第三条 验收标准：由采购人组织人工审核。"
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            d = self._locate(S, cid, text="「驻场服务」和「验收标准」")["data"]
            self.assertFalse(d["found"])
            self.assertEqual(d["match_mode"], "keyword")
            self.assertEqual(len(d["candidates"]), 2)
            self.assertIn("请选择确认", d["reason"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_keyword_contiguous_cjk_description(self):
        """设计方案给出的真实示例：整段中文连写、无分隔符。

        「合同第四部分验收条款中的第六项履约验收标准」在中文分词上是一个连续串，
        整串当然不在正文里；必须回溯到「最长且确实出现在正文中」的子串才能定位。
        """
        parsed = "第三条 履约验收标准：由采购人组织人工审核，逐项核对履约标准。第四条 违约责任：逾期按日支付违约金。"
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            d = self._locate(S, cid, text="合同第四部分验收条款中的第六项履约验收标准")["data"]
            self.assertTrue(d["found"], d)
            self.assertEqual(d["match_mode"], "keyword")
            self.assertEqual(d["reason"], "已根据描述定位到唯一候选")
            # 返回的必须是正文里的逐字原文（可直接作为 clause_text 提交 /revise）
            self.assertIn(d["original_text"], parsed)
            self.assertIn("履约验收标准", d["original_text"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_keyword_description_without_overlap_is_honest_empty(self):
        """描述与正文毫无交集 → found=False、candidates 为空、match_mode 为空（不猜位置）。"""
        parsed = "第三条 履约验收标准：由采购人组织人工审核。"
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, parsed)
            d = self._locate(S, cid, text="关于知识产权归属与保密期限的安排")["data"]
            self.assertFalse(d["found"])
            self.assertEqual(d["candidates"], [])
            self.assertEqual(d["match_mode"], "")
            self.assertEqual(d["original_text"], "")
            self.assertIsNone(d["start"])
            self.assertIn("未在合同正文中找到", d["reason"])
        finally:
            eng.dispose()
            tmp.cleanup()

    # --- 7. 条款编号锚点 -----------------------------------------------------
    def test_clause_anchor_resolves_third_clause(self):
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, _ANCHOR_TEXT)
            d = self._locate(S, cid, clause_anchor="三")["data"]
            self.assertTrue(d["found"])
            self.assertEqual(d["match_mode"], "clause_anchor")
            self.assertEqual(d["reason"], "已按条款编号定位到第三条")
            self.assertEqual(d["clause_no"], 3)
            self.assertEqual(d["clause_title"], "验收标准")
            # 窗口 = 第三条标题起点 → 下一个「第X条」标题之前
            self.assertTrue(d["original_text"].startswith("第三条"))
            self.assertIn("逐项核对履约标准", d["original_text"])
            self.assertNotIn("第四条", d["original_text"])
            self.assertIn(d["original_text"], _ANCHOR_TEXT)
            self.assertEqual(_ANCHOR_TEXT[d["start"]:d["end"]].strip(), d["original_text"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_clause_anchor_arabic_digits(self):
        # 阿拉伯数字锚点与中文等价
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, _ANCHOR_TEXT)
            d = self._locate(S, cid, clause_anchor="4")["data"]
            self.assertTrue(d["found"])
            self.assertEqual(d["clause_no"], 4)
            self.assertEqual(d["clause_title"], "违约责任")
            self.assertIn("逾期按日支付违约金", d["original_text"])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_clause_anchor_absent(self):
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, _ANCHOR_TEXT)
            d = self._locate(S, cid, clause_anchor="九十九")["data"]
            self.assertFalse(d["found"])
            self.assertEqual(d["match_mode"], "clause_anchor")
            self.assertEqual(d["reason"], "未在合同正文中找到第九十九条")
            self.assertEqual(d["original_text"], "")
            self.assertEqual(d["candidates"], [])
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_clause_anchor_unparsable(self):
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, _ANCHOR_TEXT)
            d = self._locate(S, cid, clause_anchor="第若干条")["data"]
            self.assertFalse(d["found"])
            self.assertEqual(d["match_mode"], "")
            self.assertIn("无法识别", d["reason"])
        finally:
            eng.dispose()
            tmp.cleanup()

    # --- 8. 只读证明 ---------------------------------------------------------
    def test_endpoint_is_read_only(self):
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, _ANCHOR_TEXT)

            # 调用前先落一条历史审核记录，作为「审计记录计数不被改动」的基线
            s = S()
            s.add(AuditRecord(contract_id=cid, audit_batch="b1", risk_type="R08",
                              risk_level="medium", clause_text="历史记录",
                              detection_method="rule", result_status="valid"))
            s.commit()
            s.close()

            s = S()
            before_records = s.query(AuditRecord).count()
            before_revisions = s.query(ClauseRevision).count()
            s.close()
            self.assertEqual(before_revisions, 0)

            # 三种模式各调一次（命中/未命中/锚点），确认都不产生写入
            self._locate(S, cid, text="设备验收合格30日内组织验收")
            self._locate(S, cid, text="本次请求的条款在合同中完全不存在XYZ")
            self._locate(S, cid, clause_anchor="三")

            s = S()
            self.assertEqual(s.query(ClauseRevision).count(), 0)
            self.assertEqual(s.query(AuditRecord).count(), before_records)
            s.close()
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_not_found_and_forbidden_contract_404(self):
        tmp, eng, S = self._setup()
        try:
            cid = self._add_contract(S, _ANCHOR_TEXT)
            s = S()
            with self.assertRaises(HTTPException) as ctx:
                contracts.locate_contract_clause(
                    cid + 999, contracts.LocateClauseRequest(text="任意"),
                    db=s, current_user=self._user())
            s.close()
            self.assertEqual(ctx.exception.status_code, 404)

            # 他人合同（uploader 只能看自己的）
            other = mock.MagicMock()
            other.id = 99
            other.role = "uploader"
            s = S()
            with self.assertRaises(HTTPException) as ctx2:
                contracts.locate_contract_clause(
                    cid, contracts.LocateClauseRequest(text="任意"),
                    db=s, current_user=other)
            s.close()
            self.assertEqual(ctx2.exception.status_code, 404)
        finally:
            eng.dispose()
            tmp.cleanup()


class TestLocateAtRefactor(unittest.TestCase):
    """_locate_clause 的纯提取重构：_locate_at 与旧行为等价。"""

    def test_locate_at_matches_locate_clause_window(self):
        full = "四、合同验收\n（6）履约验收标准：由采购人组织人工审核，逐项核对履约标准。\n五、争议解决"
        idx = full.find("（6）履约验收标准")
        at = contracts._locate_at(full, idx)
        loc = contracts._locate_clause(full, "履约验收标准")
        self.assertIsNotNone(loc)
        self.assertEqual(loc, at)
        self.assertEqual(set(at.keys()), {"clause_no", "clause_title", "original_text", "start", "end"})

    def test_locate_clause_uses_locate_at(self):
        full = "第一条 服务内容：乙方提供服务。第二条 违约责任：逾期支付违约金。"
        with mock.patch.object(contracts, "_locate_at", wraps=contracts._locate_at) as spy:
            contracts._locate_clause(full, "逾期支付违约金")
        self.assertEqual(spy.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
