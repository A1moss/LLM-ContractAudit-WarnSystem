"""审核报告 PDF 导出（P0-1）测试。

覆盖三个层面：
1. **API 契约**：正常合同 → 200 + application/pdf + 非空 PDF；无审核报告 → 业务 404；
   合同不存在 → 404；越权（非本人合同）→ 与既有 `GET /audit-report` 完全一致的 404。
2. **PDF 内容**：用项目已有依赖 pdfplumber 抽取文本，逐项断言报告必须出现的真实内容
   （合同基本信息 / 合同状态 / 审核结论 / 风险统计 / 风险名称与等级 / 风险原文 /
   修改建议 / 法律依据 / 条款完整性 / 缺失条款 / 免责声明）。
3. **真实性边界**：报告里**不得出现**数据库根本不存在的字段
   （审核人 / 报告编号 / 报告版本 / 审批状态 / 审核机构）。

以及纯函数单测：文件名清洗、金额格式化、全角字符归一、字体会被解析出来。

运行（backend 目录下）：
    python -m pytest tests/test_report_pdf.py -v
"""
import io
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
from models.contract import Contract  # noqa: E402
from models.audit_record import AuditRecord  # noqa: E402
from models.audit_report import AuditReport  # noqa: E402
import api.contracts as contracts  # noqa: E402
from services import report_pdf  # noqa: E402

try:
    import pdfplumber
    HAVE_PDFPLUMBER = True
except Exception:  # pragma: no cover
    HAVE_PDFPLUMBER = False


def _extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _page_count(pdf_bytes: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return len(pdf.pages)


CLAUSE_REPORT = {
    "summary": {"total": 4, "covered": 1, "partial": 2, "missing": 1, "coverage_rate": 0.25},
    "missing_critical": ["不可抗力条款"],
    "cross_clause_risks": [
        {"type": "前置依赖缺失", "risk": "验收标准缺失导致付款条件无法执行",
         "clause": "第二条", "depends_on": "第三条"},
        {"type": "条款互斥", "risk": "保密期限与自动续约条款互相冲突",
         "clause": "第五条", "conflicts_with": "第八条"},
    ],
    "clauses": [
        {"title": "标的与范围", "status": "covered", "priority": "required",
         "matched_text": "第一条 合同标的与范围：甲方委托乙方提供服务。"},
        {"title": "验收标准", "status": "partial", "priority": "required",
         "matched_text": "第二条 验收标准与验收方式", "deviation": "未约定验收期限",
         "completion": "建议补充验收期限与验收不合格的处理方式",
         "related_law": "民法典第782条"},
        {"title": "违约责任", "status": "partial", "priority": "required",
         "matched_text": "第三条 违约责任", "deviation": "违约金比例过高"},
        {"title": "不可抗力条款", "status": "missing", "priority": "required",
         "completion": "建议新增不可抗力条款", "risk": "缺失不可抗力条款"},
    ],
}


class ReportPdfTestBase(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        eng = create_engine(f"sqlite:///{Path(tmp.name) / 't.db'}", connect_args={"timeout": 30})
        Base.metadata.create_all(eng)
        self.tmp = tmp
        return sessionmaker(bind=eng)

    def _user(self, uid=1, role="uploader"):
        class _U:
            pass
        u = _U()
        u.id = uid
        u.role = role
        return u

    def _add_contract(self, S, user_id=1, status="completed", audit_mode="precise",
                      file_name="服务外包合同.docx", elements=None, with_report=True,
                      with_records=True, missing_clauses=CLAUSE_REPORT):
        s = S()
        c = Contract(
            user_id=user_id, file_name=file_name, contract_type="服务外包合同",
            parsed_text="第一条 合同标的与范围。\n第二条 验收标准与验收方式：人工审核。",
            extracted_elements=elements if elements is not None else {
                "parties": {"甲方": "海南安居置业发展有限公司", "乙方": "海南恒信工程造价咨询公司"},
                "amount": {"value": 1280000.0, "currency": "CNY", "text": "壹佰贰拾捌万元整"},
                "sign_date": "2026-08-15",
                "performance_period": {"start": "2026-09-01", "end": "2028-08-31"},
                "dispute_resolution": "海口市龙华区人民法院",
                "governing_law": "中华人民共和国法律",
            },
            status=status, audit_mode=audit_mode,
        )
        s.add(c)
        s.commit()
        cid = c.id
        if with_report:
            s.add(AuditReport(
                contract_id=cid, audit_batch="batch-1", report_html="<p>x</p>",
                risk_score=78, high_risk_count=1, mid_risk_count=1, low_risk_count=0,
                missing_clauses=missing_clauses,
            ))
        if with_records:
            s.add(AuditRecord(
                contract_id=cid, audit_batch="batch-1", risk_type="R01", risk_level="high",
                clause_text="第三条 违约责任与违约金上限为合同总价的30%。",
                clause_position={"original_text": "第三条 违约责任与违约金上限为合同总价的30%。",
                                 "clause_no": 3, "clause_title": "违约责任",
                                 "start": 120, "end": 150},
                reason="违约金比例约定过高，可能导致显失公平",
                suggestion="建议将违约金上限下调至合同总价的合理区间",
                detection_method="evidence", confidence=0.93, result_status="valid",
                evidence={"law": "民法典第585条"},
                recommendation={"example": "违约金上限为合同总价的10%。",
                                "risk_description": "违约金过高存在被调减风险",
                                "legal_basis": "民法典第585条",
                                "grounding": {"passed": True, "issues": []}},
            ))
            s.add(AuditRecord(
                contract_id=cid, audit_batch="batch-1", risk_type="R08", risk_level="medium",
                clause_text="第二条 验收标准与验收方式：人工审核。",
                clause_position={"original_text": "第二条 验收标准与验收方式：人工审核。",
                                 "clause_no": 2, "start": 60, "end": 80},
                reason="验收标准缺失，缺少客观可衡量的验收依据",
                suggestion="补充量化验收标准", detection_method="rule", confidence=0.71,
                result_status="valid", evidence={}, recommendation={"grounding": {"passed": True, "issues": []}},
            ))
        s.commit()
        s.close()
        return cid

    def _export(self, S, cid, user=None):
        s = S()
        try:
            return contracts.export_audit_report_pdf(
                cid, db=s, current_user=user or self._user())
        finally:
            s.close()


class TestExportApiContract(ReportPdfTestBase):
    """① API 契约：状态码 / media type / 非空文件 / 文件名。"""

    def test_normal_contract_returns_pdf(self):
        S = self._setup()
        cid = self._add_contract(S)
        resp = self._export(S, cid)

        self.assertEqual(resp.media_type, "application/pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.body, "PDF 内容不能为空")
        self.assertTrue(resp.body.startswith(b"%PDF-"), "必须是合法 PDF 头")
        self.assertIn(b"%%EOF", resp.body[-2048:], "PDF 必须有合法结尾")
        self.assertGreater(len(resp.body), 5000, "PDF 体积异常，疑似未渲染出内容")

        disp = resp.headers["content-disposition"]
        self.assertIn("attachment", disp)
        self.assertIn("filename*=UTF-8''", disp, "中文文件名必须做 RFC 5987 编码")
        from urllib.parse import unquote
        self.assertIn("_审核报告.pdf", unquote(disp))
        self.assertIn("服务外包合同_审核报告.pdf", unquote(disp))

    def test_no_audit_report_returns_business_404(self):
        """无报告且无任何审核记录 → 与 GET /audit-report 一致的 404 业务错误。"""
        S = self._setup()
        cid = self._add_contract(S, with_report=False, with_records=False)
        with self.assertRaises(contracts.HTTPException) as ctx:
            self._export(S, cid)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "no audit report found")

    def test_missing_contract_returns_404(self):
        S = self._setup()
        with self.assertRaises(contracts.HTTPException) as ctx:
            self._export(S, 999999)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "contract not found")

    def test_other_uploader_cannot_export(self):
        """越权：别人的合同对 uploader 一律 404（与既有权限判断同一入口）。"""
        S = self._setup()
        cid = self._add_contract(S, user_id=1)
        with self.assertRaises(contracts.HTTPException) as ctx:
            self._export(S, cid, user=self._user(uid=2, role="uploader"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_reviewer_role_can_export(self):
        """reviewer 属既有工作流角色，可查看/导出工作流内的合同。"""
        S = self._setup()
        cid = self._add_contract(S, user_id=1)
        resp = self._export(S, cid, user=self._user(uid=2, role="reviewer"))
        self.assertEqual(resp.status_code, 200)

    def test_deleted_contract_returns_404(self):
        S = self._setup()
        cid = self._add_contract(S, status="deleted")
        with self.assertRaises(contracts.HTTPException) as ctx:
            self._export(S, cid)
        self.assertEqual(ctx.exception.status_code, 404)


@unittest.skipUnless(HAVE_PDFPLUMBER, "pdfplumber 不可用")
class TestExportContent(ReportPdfTestBase):
    """② PDF 内容：必须包含页面审核报告里的真实内容。"""

    def setUp(self):
        self.S = self._setup()
        self.cid = self._add_contract(self.S)
        resp = self._export(self.S, self.cid)
        self.pdf = resp.body
        self.text = _extract_text(self.pdf)

    def test_contract_basic_info(self):
        for expected in (
            "合同智能审核报告",
            "服务外包合同",            # 合同类型
            "服务外包合同.docx",       # 合同文件名
            "合同状态",                # 语义修正后的名称
            "审核完成",                # contracts.status=completed 的中文
            "精细审核",                # 审核方式
            "合同金额",
            "1,280,000.00",            # 真实金额
            "海南安居置业发展有限公司",  # 甲方
            "海南恒信工程造价咨询公司",  # 乙方
            "履行期限",
            "争议解决",
            "适用法律",
        ):
            self.assertIn(expected, self.text, f"PDF 缺少「{expected}」")

    def test_conclusion_and_disclaimer(self):
        self.assertIn("审核结论", self.text)
        self.assertIn("本次审核共识别 2 项风险", self.text)
        self.assertIn("高风险 1 项", self.text)
        self.assertIn("中风险 1 项", self.text)
        self.assertIn("本报告由 AI 合同审核系统自动生成", self.text)
        self.assertIn("不构成法律意见", self.text)

    def test_risk_stats(self):
        self.assertIn("风险统计", self.text)
        self.assertIn("78 / 100", self.text)          # 风险评分取报告快照
        self.assertIn("风险项合计", self.text)
        self.assertIn("25%", self.text)               # 覆盖率 0.25

    def test_risk_detail_fields(self):
        # 风险编号 + 中文名称（镜像后端 RISK_NAMES，不是编造）
        self.assertIn("R01", self.text)
        self.assertIn("违约金过高", self.text)
        self.assertIn("R08", self.text)
        self.assertIn("验收标准缺失", self.text)
        # 风险等级
        self.assertIn("高风险", self.text)
        self.assertIn("中风险", self.text)
        # 涉及条款 + 原文位置（clause_position 有则展示）
        self.assertIn("涉及条款", self.text)
        self.assertIn("第 3 条", self.text)
        self.assertIn("字符区间 120", self.text)
        # 风险原文（逐字）
        self.assertIn("第三条 违约责任与违约金上限为合同总价的30%。", self.text)
        # 风险原因 / 修改建议 / 修改示例 / 风险说明 / 法律依据
        self.assertIn("风险原因", self.text)
        self.assertIn("违约金比例约定过高", self.text)
        self.assertIn("修改建议", self.text)
        self.assertIn("建议将违约金上限下调至合同总价的合理区间", self.text)
        self.assertIn("修改示例", self.text)
        self.assertIn("法律依据", self.text)
        self.assertIn("民法典第585条", self.text)
        # 检测方式 + 判断把握度（报告已有则展示）
        self.assertIn("证据裁决", self.text)
        self.assertIn("判断把握度 93%", self.text)

    def test_clause_completeness(self):
        self.assertIn("条款完整性检查", self.text)
        self.assertIn("标准条款总数", self.text)
        self.assertIn("已覆盖", self.text)
        self.assertIn("部分覆盖", self.text)
        self.assertIn("缺失", self.text)
        self.assertIn("缺失关键条款", self.text)
        self.assertIn("不可抗力条款", self.text)
        self.assertIn("验收标准", self.text)
        self.assertIn("违约责任", self.text)

    def test_cross_clause_risks_only_when_present(self):
        self.assertIn("关联风险", self.text)
        self.assertIn("前置依赖缺失", self.text)
        self.assertIn("验收标准缺失导致付款条件无法执行", self.text)

    def test_no_cross_section_when_absent(self):
        """没有交叉风险时**不产出空章节**。"""
        S = self._setup()
        cid = self._add_contract(S, missing_clauses={
            "summary": {"total": 1, "covered": 1, "partial": 0, "missing": 0, "coverage_rate": 1.0},
            "missing_critical": [], "cross_clause_risks": [],
            "clauses": [{"title": "标的与范围", "status": "covered", "priority": "required"}],
        })
        text = _extract_text(self._export(S, cid).body)
        self.assertNotIn("关联风险", text)

    def test_no_fabricated_fields(self):
        """严禁虚构数据库不存在的字段。"""
        for forbidden in ("审核人", "报告编号", "报告版本", "审批状态", "审核机构", "生成机构"):
            self.assertNotIn(forbidden, self.text, f"PDF 不得出现虚构字段「{forbidden}」")

    def test_multipage_and_valid(self):
        self.assertGreaterEqual(_page_count(self.pdf), 2)
        self.assertIn("第 1 页", self.text)
        self.assertIn("第 2 页", self.text)


@unittest.skipUnless(HAVE_PDFPLUMBER, "pdfplumber 不可用")
class TestExportEdgeCases(ReportPdfTestBase):
    """③ 边界：低风险/无风险、无条款比对、缺省可选字段。"""

    def test_no_risk_contract(self):
        """无风险合同：仍能导出，给出「未识别到风险」的事实结论，且不伪造风险条目。"""
        S = self._setup()
        cid = self._add_contract(
            S, with_records=False, missing_clauses=None, elements={})
        # 手工把报告快照的三档计数置 0，模拟「本次审核确实没有风险」的真实形态
        s = S()
        rep = s.query(AuditReport).filter(AuditReport.contract_id == cid).first()
        rep.risk_score, rep.high_risk_count, rep.mid_risk_count, rep.low_risk_count = 0, 0, 0, 0
        s.commit()
        s.close()

        text = _extract_text(self._export(S, cid).body)
        self.assertIn("合同智能审核报告", text)
        self.assertIn("本次审核未识别到 R01–R13 风险项", text)
        self.assertIn("本次审核未检出风险项", text)
        # 不得出现任何具体风险条目（结论句里的「R01–R13」是固定口径表述，不算条目）
        self.assertNotIn("违约金过高", text)
        self.assertNotIn("验收标准缺失", text)
        self.assertNotIn("涉及条款", text)
        # 无可选字段时整项不渲染，不写「暂无」占位
        self.assertNotIn("合同金额", text)
        self.assertNotIn("履行期限", text)

    def test_snapshot_count_without_records_is_explained(self):
        """报告快照有风险计数、但当前批次查不到明细时，必须如实说明而不是自相矛盾。"""
        S = self._setup()
        cid = self._add_contract(S, with_records=False, missing_clauses=None)
        text = _extract_text(self._export(S, cid).body)
        self.assertIn("本次审核共识别 2 项风险", text)
        self.assertIn("本次审核未检出风险项", text)
        self.assertIn("当前可读取的风险明细为 0 项", text)

    def test_low_risk_only_contract(self):
        S = self._setup()
        cid = self._add_contract(S, missing_clauses=None)
        s = S()
        rep = s.query(AuditReport).filter(AuditReport.contract_id == cid).first()
        rep.high_risk_count, rep.mid_risk_count, rep.low_risk_count = 0, 0, 1
        s.commit()
        for r in s.query(AuditRecord).filter(AuditRecord.contract_id == cid).all():
            r.risk_level = "low"
        s.commit()
        s.close()

        text = _extract_text(self._export(S, cid).body)
        self.assertIn("低风险 1 项", text)
        self.assertIn("本次审核共识别 1 项风险", text)

    def test_missing_clauses_no_comparison(self):
        """报告没有条款比对数据时，给出明确的「未包含」说明，而不是空白章节。"""
        S = self._setup()
        cid = self._add_contract(S, missing_clauses=None)
        text = _extract_text(self._export(S, cid).body)
        self.assertIn("条款完整性检查", text)
        self.assertIn("未包含条款比对结果", text)

    def test_amount_without_currency_symbols(self):
        """金额展示用币种代码，避免字体缺字形（¥/€）导致空白方框。"""
        S = self._setup()
        cid = self._add_contract(S, elements={
            "amount": {"value": 1234.5, "currency": "CNY", "text": ""}})
        text = _extract_text(self._export(S, cid).body)
        self.assertIn("CNY 1,234.50", text)
        self.assertNotIn("¥", text)


class TestReportPdfPureFunctions(unittest.TestCase):
    """④ 纯函数单测（无 IO）。"""

    def test_safe_filename(self):
        cases = [
            ("服务外包合同.docx", "服务外包合同"),
            ("a/b\\c:d*e?f\"g<h>i|j.pdf", "a_b_c_d_e_f_g_h_i_j"),
            ("合同.PDF", "合同"),
            ("", "合同"),
            ("   ...   ", "合同"),
            ("合同\n换行\t制表.docx", "合同_换行_制表"),
        ]
        for raw, expected in cases:
            if raw == "a/b\\c:d*e?f\"g<h>i|j.pdf":
                expected = "a_b_c_d_e_f_g_h_i_j"
            self.assertEqual(report_pdf.safe_filename(raw), expected, f"输入 {raw!r}")

    def test_safe_filename_truncates(self):
        self.assertLessEqual(len(report_pdf.safe_filename("长" * 300)), 80)

    def test_amount_parts(self):
        self.assertEqual(report_pdf.amount_parts({"value": 1280000.0, "currency": "CNY"})[0],
                         "CNY 1,280,000.00")
        self.assertEqual(report_pdf.amount_parts({"value": 100, "currency": "USD"})[0], "USD 100.00")
        self.assertEqual(report_pdf.amount_parts({"value": 100})[0], "100.00")
        self.assertEqual(report_pdf.amount_parts({"currency": "CNY"})[0], "CNY")
        self.assertEqual(report_pdf.amount_parts(None), ("", ""))
        self.assertEqual(report_pdf.amount_parts({"text": "壹佰万元整"})[1], "壹佰万元整")

    def test_normalize_text_fullwidth(self):
        self.assertEqual(report_pdf.normalize_text("１２３４５６７８９０"), "1234567890")
        self.assertEqual(report_pdf.normalize_text("５０％"), "50%")
        # 中文标点原样保留
        self.assertEqual(report_pdf.normalize_text("、。，；：？！《》"), "、。，；：？！《》")

    def test_esc_escapes_and_trims_lines(self):
        self.assertEqual(report_pdf._esc("a<b>&c"), "a&lt;b&gt;&amp;c")
        self.assertEqual(report_pdf._esc("第一条\n    第二条  "), "第一条\n第二条")

    def test_risk_name_and_level_labels(self):
        self.assertEqual(report_pdf.risk_name("R01"), "违约金过高")
        self.assertEqual(report_pdf.risk_name("R99"), "R99")   # 未知代码回退为代码本身
        self.assertEqual(report_pdf.risk_name(None), "")
        self.assertEqual(report_pdf.risk_level_label("high"), "高风险")
        self.assertEqual(report_pdf.detection_label("corex_review"), "多智能体验证")
        self.assertEqual(report_pdf.cmp_status_label("partial"), "部分偏离")

    def test_font_always_resolves(self):
        """任何环境下都必须能解析出可用中文字体（系统字体或内置 CID 兜底）。"""
        font = report_pdf.resolve_font()
        self.assertTrue(font["regular"])
        if not font["ttf"]:
            self.assertEqual(font["regular"], "STSong-Light")


class TestFontWithoutSystemFonts(ReportPdfTestBase):
    """⑤ 无系统字体时必须降级到 reportlab 自带 CID 字体（Linux/CI 可用性）。"""

    def test_cid_fallback_renders_chinese(self):
        saved = report_pdf._FONT_CACHE
        try:
            report_pdf._FONT_CACHE = None
            # 屏蔽所有字体文件与环境变量，强制走 CID 兜底分支
            with mock.patch.object(report_pdf, "_SYSTEM_CANDIDATES", ()), \
                 mock.patch.object(report_pdf, "_VENDOR_CANDIDATES", ()), \
                 mock.patch.dict(os.environ, {"AUDIT_REPORT_FONT": ""}):
                font = report_pdf.resolve_font()
                self.assertFalse(font["ttf"])
                self.assertEqual(font["regular"], "STSong-Light")

                S = self._setup()
                cid = self._add_contract(S)
                resp = self._export(S, cid)
                self.assertTrue(resp.body.startswith(b"%PDF-"))
                if HAVE_PDFPLUMBER:
                    text = _extract_text(resp.body)
                    self.assertIn("合同智能审核报告", text)
                    self.assertIn("违约金过高", text)
        finally:
            report_pdf._FONT_CACHE = saved


if __name__ == "__main__":
    unittest.main()
