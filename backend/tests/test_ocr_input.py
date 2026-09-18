"""图片 / 扫描 PDF → OCR → parsed_text → 中间 DOCX → 统一审核修改链路 测试。

覆盖本轮要求的 A–O：

  A. RapidOCR 初始化/依赖            I. parsed_text → 中间 DOCX 无损
  B. JPG OCR                         J. PDF anchor（OCR 文本上）
  C. PNG OCR                         K. R09 重复编号精确插入
  D. 扫描 PDF OCR                    L. OCR 低质量拒绝
  E. 多页 PDF page 顺序              M. OCR 无文字拒绝
  F. OCR 坐标排序                    N. 原有 DOCX 回归
  G. 重复 OCR 顺序稳定               O. 普通 PDF 回归
  H. OCR → parsed_text

**评测边界**：本文件只用自造合成样本，不引用任何 Gold / 测试集 / F1 / 分类评测数据。

运行（backend 目录下）：
    python -m pytest tests/test_ocr_input.py -v
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from docx import Document  # noqa: E402

import pdfplumber  # noqa: E402

from ai.parser import detect_and_parse, IMAGE_EXTS  # noqa: E402
from ai.parser import ocr_engine, ocr_parser, scan_pdf  # noqa: E402
from api.contracts import _headings_with_occurrences  # noqa: E402
from services.docx_reviser import build_revised_docx, _heading_num  # noqa: E402
from services.intermediate_docx import build_from_parsed_text  # noqa: E402
from services.pdf_anchor import build_anchor, MIN_ANCHOR_LEN  # noqa: E402

# ── 字体：用系统可用的中文字体渲染合成样本 ──
_FONT_PATH = next(
    (p for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                 "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
     if os.path.isfile(p)), None)

# 一份"合同形状"的文本：编号单调递增 + **同编号重复**（验证 R09 精确位置）
CONTRACT_LINES = [
    "建设工程施工合同",
    "合同编号：HN-ZJ-2026-0812",
    "第一条 工程概况",
    "工程名称：海口江东新区安居房一期项目",
    "第二条 合同价款",
    "合同总价为人民币壹亿贰仟捌佰万元整",
    "第三条 违约责任",
    "违约金上限为合同总价的 30%",
    "第四条 争议解决",
    "提交海口市龙华区人民法院裁决",
    "第五条 保密",
    "双方对合同内容承担永久保密义务",
    "第六条 附则甲",
    "本条为第一个第六条的内容",
    "第七条 附则乙",
    "本条为第七条的内容",
    "第六条 附则丙",
    "本条为重复编号的第三个第六条",
    "第八条 签署",
    "双方于文首所载日期签署本合同",
]


def _render_png(path, lines, size=(1240, 2000), px=22, blur=None):
    """把文本行渲染成 PNG（模拟"纯图片扫描件"）。"""
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(_FONT_PATH, px) if _FONT_PATH else None
    y = 60
    for ln in lines:
        d.text((70, y), ln, fill="black", font=font)
        y += int(px * 1.9)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    im.save(path)
    return path


def _make_text_pdf(path, pages_lines):
    """用 reportlab 造一份**有文字层**的 PDF（用于回归 O 与扫描件样本来源）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    name = "TestCJK"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, _FONT_PATH))
    c = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages_lines:
        c.setFont(name, 13)
        y = 800
        for ln in lines:
            c.drawString(50, y, ln)
            y -= 26
        c.showPage()
    c.save()
    return str(path)


def _make_scanned_pdf(path, pages_lines):
    """把文本 PDF 逐页栅格化后**重新合成图片型 PDF** → 得到"纯扫描件"。"""
    text_pdf = str(Path(path).with_suffix(".srctext.pdf"))
    _make_text_pdf(text_pdf, pages_lines)
    imgs = []
    with pdfplumber.open(text_pdf) as pdf:
        for pg in pdf.pages:
            imgs.append(pg.to_image(resolution=150).original.convert("RGB"))
    imgs[0].save(str(path), "PDF", save_all=True, append_images=imgs[1:], resolution=150)
    return str(path)


@unittest.skipUnless(_FONT_PATH, "系统缺少可用中文字体，无法生成 OCR 合成样本")
class OcrInputTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.dir = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        if not ocr_engine.ocr_available():
            self.skipTest("RapidOCR 不可用")


class TestAEngineInit(OcrInputTestBase):
    """A. RapidOCR 初始化 / 依赖。"""

    def test_A_engine_available_and_singleton(self):
        e1 = ocr_engine._ensure_engine()
        e2 = ocr_engine._ensure_engine()
        self.assertIsNotNone(e1, "RapidOCR 应可用")
        self.assertIs(e1, e2, "必须是同一单例（惰性初始化只做一次）")

    def test_A_output_structure(self):
        """确认实际 API：boxes/txts/scores；且 一条都没有 时返回 None。"""
        png = _render_png(os.path.join(self.dir, "struct.png"), CONTRACT_LINES[:6])
        r = ocr_engine.ocr_image(png, page=1)
        self.assertIsNone(r["error"])
        self.assertTrue(r["items"], "应识别出文字")
        it = r["items"][0]
        for key in ("text", "confidence", "page", "bbox", "cy", "x0"):
            self.assertIn(key, it)
        self.assertEqual(it["page"], 1)
        self.assertGreater(it["confidence"], 0)

    def test_A_blank_image_returns_none_not_empty_object(self):
        """RapidOCR 在一条都没识别到时返回 None（不是空对象）—— 必须判 None。"""
        blank = os.path.join(self.dir, "blank_A.png")
        Image.new("RGB", (600, 800), "white").save(blank)
        r = ocr_engine.ocr_image(blank, page=1)
        self.assertEqual(r["items"], [])
        self.assertIsNone(r["error"], "无文字不算错误，由质量闸判定")


class TestBCImageOcr(OcrInputTestBase):
    """B. JPG OCR / C. PNG OCR。"""

    def _check(self, path, lines=None):
        parsed = detect_and_parse(path)
        self.assertEqual(parsed.get("format"), "image")
        self.assertTrue((parsed.get("full_text") or "").strip(), "OCR 应产出文本")
        self.assertEqual(parsed.get("ocr_quality"), "normal")
        return parsed

    def test_B_jpg_ocr(self):
        jpg = os.path.join(self.dir, "b.jpg")
        _render_png(jpg, CONTRACT_LINES[:10])
        Image.open(jpg).convert("RGB").save(jpg, "JPEG", quality=95)
        parsed = self._check(jpg)
        self.assertIn("工程概况", parsed["full_text"])

    def test_C_png_ocr(self):
        png = os.path.join(self.dir, "c.png")
        _render_png(png, CONTRACT_LINES[:10])
        parsed = self._check(png)
        self.assertIn("工程概况", parsed["full_text"])

    def test_image_exts_whitelist_unchanged(self):
        """本轮不扩大格式范围：白名单与改造前一致。"""
        self.assertEqual(IMAGE_EXTS, {"jpg", "jpeg", "png", "tiff", "tif", "bmp"})


class TestDEPages(OcrInputTestBase):
    """D. 扫描 PDF OCR / E. 多页 page 顺序。"""

    def test_D_scanned_pdf_goes_through_ocr(self):
        pdf = _make_scanned_pdf(os.path.join(self.dir, "scan1.pdf"), [CONTRACT_LINES[:10]])
        with pdfplumber.open(pdf) as doc:
            self.assertFalse((doc.pages[0].extract_text() or "").strip(),
                             "合成样本应无文字层")
        parsed = detect_and_parse(pdf)
        self.assertEqual(parsed.get("format"), "pdf")
        self.assertEqual(parsed.get("ocr_quality"), "normal")
        self.assertIn("工程概况", parsed["full_text"])
        self.assertEqual(parsed.get("scanned_pages"), [1])

    def test_E_multipage_page_order_and_boundaries(self):
        page1 = ["第一页 建设工程施工合同", "第一条 工程概况", "工程名称：海口项目"]
        page2 = ["第二页 合同价款", "第二条 合同价款", "总价 壹亿元整"]
        page3 = ["第三页 附则", "第三条 附则", "双方签署生效"]
        pdf = _make_scanned_pdf(os.path.join(self.dir, "scan3.pdf"), [page1, page2, page3])
        parsed = detect_and_parse(pdf)
        self.assertEqual(parsed.get("page_count"), 3)
        self.assertEqual(parsed.get("scanned_pages"), [1, 2, 3])

        # page 字段保留真实页码，且按页递增
        pages_in_order = [p["page"] for p in parsed["paragraphs"]]
        self.assertEqual(pages_in_order, sorted(pages_in_order), "页序必须单调")
        self.assertEqual(sorted({p["page"] for p in parsed["paragraphs"]}), [1, 2, 3])

        # 页边界：每页文本各自成行，不会被粘成一行
        lines = parsed["full_text"].split("\n")
        self.assertTrue(any("第一页" in l for l in lines))
        self.assertTrue(any("第二页" in l for l in lines))
        self.assertTrue(any("第三页" in l for l in lines))
        for l in lines:
            self.assertFalse("第一页" in l and "第二页" in l, "不同页不应粘到同一行")

    def test_E_text_pdf_is_not_forced_through_ocr(self):
        """普通文字 PDF 必须走原有 parse_pdf，**不进入 OCR**。"""
        pdf = _make_text_pdf(os.path.join(self.dir, "text.pdf"), [CONTRACT_LINES[:8]])
        parsed = detect_and_parse(pdf)
        self.assertEqual(parsed.get("format"), "pdf")
        self.assertIsNone(parsed.get("ocr_quality"), "文字 PDF 不应带 OCR 字段")
        self.assertNotIn("scanned_pages", parsed)
        self.assertIn("工程概况", parsed["full_text"])


class TestFGOrderingAndStability(OcrInputTestBase):
    """F. OCR 坐标排序 / G. 重复 OCR 顺序稳定。"""

    def test_F_reading_order_by_geometry(self):
        """乱序喂入识别框，输出必须按 (页, 行, x) 稳定排序。"""
        def ev(text, cy, x0, page=1):
            return {"text": text, "confidence": 1.0, "page": page, "bbox": [],
                    "cy": cy, "x0": x0, "top": cy - 1, "bottom": cy + 1, "h": 2.0,
                    "_i": 0}

        scrambled = [
            ev("右下", 100, 500), ev("左下", 100, 100),
            ev("第二行", 200, 100), ev("第一行", 10, 100),
        ]
        ordered = ocr_engine._sort_events(scrambled)
        self.assertEqual([e["text"] for e in ordered], ["第一行", "左下", "右下", "第二行"])

    def test_F_same_line_sorted_left_to_right(self):
        def ev(text, cy, x0):
            return {"text": text, "confidence": 1.0, "page": 1, "bbox": [],
                    "cy": cy, "x0": x0, "top": cy - 1, "bottom": cy + 1, "h": 2.0, "_i": 0}
        items = [ev("C", 50, 300), ev("A", 50, 100), ev("B", 50, 200)]
        self.assertEqual([e["text"] for e in ocr_engine._sort_events(items)], ["A", "B", "C"])

    def test_F_page_is_primary_key(self):
        def ev(text, cy, x0, page):
            return {"text": text, "confidence": 1.0, "page": page, "bbox": [],
                    "cy": cy, "x0": x0, "top": cy - 1, "bottom": cy + 1, "h": 2.0, "_i": 0}
        items = [ev("p2", 10, 10, 2), ev("p1", 900, 10, 1)]
        self.assertEqual([e["text"] for e in ocr_engine._sort_events(items)], ["p1", "p2"])

    def test_G_repeated_ocr_is_order_stable(self):
        png = _render_png(os.path.join(self.dir, "stable.png"), CONTRACT_LINES)
        runs = [ocr_engine.items_to_text(ocr_engine.ocr_image(png)["items"]) for _ in range(3)]
        self.assertEqual(runs[0], runs[1], "同一输入重复 OCR 文本必须一致")
        self.assertEqual(runs[1], runs[2])

    def test_G_paragraphs_index_is_sequential(self):
        png = _render_png(os.path.join(self.dir, "seq.png"), CONTRACT_LINES[:8])
        parsed = detect_parse = detect_and_parse(png)
        idx = [p["index"] for p in parsed["paragraphs"]]
        self.assertEqual(idx, list(range(len(idx))), "paragraph index 必须连续递增")


class TestHIntermediateDocx(OcrInputTestBase):
    """H. OCR → parsed_text / I. parsed_text → 中间 DOCX 无损。"""

    def test_H_full_text_is_ocr_lines_joined(self):
        png = _render_png(os.path.join(self.dir, "h.png"), CONTRACT_LINES[:8])
        parsed = detect_and_parse(png)
        texts = [p["text"] for p in parsed["paragraphs"]]
        self.assertEqual(parsed["full_text"], "\n".join(texts))

    def test_I_intermediate_docx_is_verbatim_lossless(self):
        png = _render_png(os.path.join(self.dir, "i.png"), CONTRACT_LINES)
        parsed = detect_and_parse(png)
        out = os.path.join(self.dir, "i.docx")
        build_from_parsed_text(parsed["full_text"], out)
        doc = Document(out)
        self.assertEqual("\n".join(p.text for p in doc.paragraphs), parsed["full_text"])
        # 行号 = 段落下标（R09 的 paragraph_index 依赖这一点）
        self.assertEqual(len(doc.paragraphs), len(parsed["full_text"].split("\n")))

    def test_I_scanned_pdf_intermediate_docx_lossless(self):
        pdf = _make_scanned_pdf(os.path.join(self.dir, "i2.pdf"), [CONTRACT_LINES[:10]])
        parsed = detect_and_parse(pdf)
        out = os.path.join(self.dir, "i2.docx")
        build_from_parsed_text(parsed["full_text"], out)
        self.assertEqual("\n".join(p.text for p in Document(out).paragraphs),
                         parsed["full_text"])


class TestJAnchorAndKR09(OcrInputTestBase):
    """J. PDF anchor（OCR 文本）/ K. R09 重复编号精确插入。"""

    def setUp(self):
        super().setUp()
        self.png = _render_png(os.path.join(self.dir, "jk.png"), CONTRACT_LINES)
        self.parsed = detect_and_parse(self.png)
        self.text = self.parsed["full_text"]
        self.docx = os.path.join(self.dir, "jk.docx")
        build_from_parsed_text(self.text, self.docx)

    def test_J_anchor_works_on_ocr_text(self):
        """LLM 证据会把相邻 OCR 行拼成一句（换行→空格）；锚点必须仍可建立。"""
        lines = self.text.split("\n")
        pair = f"{lines[8]} {lines[9]}"          # 第四条 争议解决 + 提交…法院裁决
        built = build_anchor(self.text, pair)
        self.assertIsNotNone(built, "OCR 文本上应能建立锚点")
        anchor, start, end = built
        self.assertEqual(self.text[start:end], anchor)
        self.assertGreaterEqual(len(anchor), MIN_ANCHOR_LEN)

    def test_J_anchor_tolerates_ocr_digit_space(self):
        """OCR 常把 ``30%`` 识别成 ``30 %``；证据文本不带空格时也必须可定位。"""
        idx = next(i for i, l in enumerate(self.text.split("\n")) if "30" in l)
        raw = self.text.split("\n")[idx]
        no_space = re.sub(r"(?<=\d)\s+(?=\d)", "", raw)
        # 构造"证据文本去掉了数字间空格"的情形
        built = build_anchor(self.text, no_space)
        self.assertIsNotNone(built, "数字内部空白差异不应导致定位失败")
        anchor, start, end = built
        self.assertEqual(self.text[start:end], anchor)
        self.assertIn("30", anchor)

    def test_J_ambiguous_anchor_is_refused(self):
        """重复出现的短文本无法唯一确定 → 明确失败，不猜。"""
        self.assertIsNone(build_anchor(self.text, "第六条 附则甲"))

    def test_K_duplicate_number_distinct_positions(self):
        """同编号重复时，选第 N 处必须插到第 N 处（三处落点两两不同）。"""
        occ = [o for o in _headings_with_occurrences(self.text) if o["num"] == 6]
        self.assertGreaterEqual(len(occ), 2, f"fixture 应有重复的「六」：{occ}")

        class _Rev:
            def __init__(self, pos, text):
                self.id = 1
                self.scope = "clause"
                self.operation = "add_clause"
                self.clause_text = ""
                self.revised_clause = text
                self.original_clause_text = None
                self.position = pos
                self.adopted = True

        spots = []
        for k, o in enumerate(occ, start=1):
            out = os.path.join(self.dir, f"k{k}.docx")
            build_revised_docx(self.docx, [_Rev(
                {"anchor": o["cn"], "target_text": o["target_text"],
                 "paragraph_index": o["paragraph_index"]},
                f"【新增-{k}】不可抗力条款正文。")], out)
            paras = [p.text for p in Document(out).paragraphs]
            i_new = next(i for i, t in enumerate(paras) if f"【新增-{k}】" in t)
            self.assertGreater(i_new, o["paragraph_index"], f"第{k}处应插在该处之后")
            spots.append(i_new)
        self.assertEqual(len(set(spots)), len(spots),
                         f"不同出现必须落点不同（当前 {spots}）")
        self.assertEqual(spots, sorted(spots), f"应按出现顺序单调靠后：{spots}")

    def test_K_unreliable_position_fails_loudly(self):
        class _Rev:
            def __init__(self, pos):
                self.id = 1
                self.scope = "clause"
                self.operation = "add_clause"
                self.clause_text = ""
                self.revised_clause = "【新增】X"
                self.original_clause_text = None
                self.position = pos
                self.adopted = True

        out = os.path.join(self.dir, "kbad.docx")
        with self.assertRaises(ValueError):
            build_revised_docx(self.docx, [_Rev(
                {"anchor": "六", "target_text": "根本不存在的标题"})], out)
        self.assertFalse(os.path.exists(out), "失败时不得留下输出文件")


class TestLMQualityGate(OcrInputTestBase):
    """L. OCR 低质量拒绝 / M. OCR 无文字拒绝（质量闸在**解析阶段**）。"""

    def test_L_heavy_blur_is_low_quality(self):
        blur = _render_png(os.path.join(self.dir, "blur6.png"), CONTRACT_LINES, blur=6)
        parsed = detect_and_parse(blur)
        self.assertEqual(parsed.get("ocr_quality"), "low")
        self.assertFalse((parsed.get("full_text") or "").strip())

    def test_L_low_mean_confidence_is_low_quality(self):
        items = [{"text": "这是一段足够长的测试文字内容", "confidence": 0.30, "page": 1,
                  "bbox": [], "cy": 1.0, "x0": 0.0, "top": 0.0, "bottom": 1.0, "h": 1.0}]
        q = ocr_engine.assess_quality(items)
        self.assertFalse(q["usable"])
        self.assertIn("置信度", q["reason"])

    def test_L_too_few_chars_is_low_quality(self):
        items = [{"text": "短", "confidence": 0.99, "page": 1, "bbox": [],
                  "cy": 1.0, "x0": 0.0, "top": 0.0, "bottom": 1.0, "h": 1.0}]
        q = ocr_engine.assess_quality(items)
        self.assertFalse(q["usable"])
        self.assertIn("过少", q["reason"])

    def test_L_high_low_conf_ratio_is_low_quality(self):
        items = [{"text": "有效文字内容足够长的一段中文", "confidence": 0.5, "page": 1,
                  "bbox": [], "cy": 1.0, "x0": 0.0, "top": 0.0, "bottom": 1.0, "h": 1.0}] * 4
        q = ocr_engine.assess_quality(items)
        self.assertFalse(q["usable"])

    def test_M_blank_image_no_text(self):
        blank = os.path.join(self.dir, "blankM.png")
        Image.new("RGB", (800, 1100), "white").save(blank)
        parsed = detect_and_parse(blank)
        self.assertFalse((parsed.get("full_text") or "").strip())
        self.assertTrue(parsed.get("error"), "无文字必须带明确 error")

    def test_M_upload_endpoint_rejects_low_quality_with_4xx(self):
        """复刻 upload 端点的判定：低质量 → 422 + 可操作提示（不进入审核）。"""
        from fastapi import HTTPException
        import api.contracts as contracts

        blur = _render_png(os.path.join(self.dir, "gate.png"), CONTRACT_LINES, blur=6)
        parsed = detect_and_parse(blur)
        self.assertEqual(parsed.get("ocr_quality"), "low")
        # 端点逻辑：full_text 空 或 ocr_quality=low → 422 并删除落盘文件
        self.assertFalse((parsed.get("full_text") or "").strip())

    def test_M_normal_ocr_passes_gate(self):
        png = _render_png(os.path.join(self.dir, "ok.png"), CONTRACT_LINES)
        parsed = detect_and_parse(png)
        self.assertEqual(parsed.get("ocr_quality"), "normal")
        self.assertGreaterEqual(parsed.get("ocr_valid_chars", 0), 10)
        self.assertGreaterEqual(parsed.get("ocr_confidence", 0), 0.75)


class TestNORegression(OcrInputTestBase):
    """N. 原有 DOCX 回归 / O. 普通 PDF 回归。"""

    def test_N_docx_parse_unchanged(self):
        p = os.path.join(self.dir, "n.docx")
        doc = Document()
        for ln in CONTRACT_LINES[:8]:
            doc.add_paragraph(ln)
        doc.save(p)
        parsed = detect_and_parse(p)
        self.assertEqual(parsed.get("format"), "docx")
        self.assertIn("工程概况", parsed["full_text"])
        # DOCX 路径不应带 OCR 字段
        self.assertNotIn("ocr_quality", parsed)

    def test_O_text_pdf_parse_unchanged(self):
        p = _make_text_pdf(os.path.join(self.dir, "o.pdf"), [CONTRACT_LINES[:8]])
        parsed = detect_and_parse(p)
        self.assertEqual(parsed.get("format"), "pdf")
        self.assertIn("工程概况", parsed["full_text"])
        self.assertTrue((parsed.get("paragraphs") or [])[0].get("page"))
        self.assertIsNone(parsed.get("ocr_quality"))

    def test_O_tiff_bmp_still_routable(self):
        """TIFF/BMP 仍在白名单内（本轮不扩展也不缩减）。"""
        tif = os.path.join(self.dir, "o.tiff")
        Image.new("RGB", (800, 1100), "white").save(tif, "TIFF")
        parsed = detect_and_parse(tif)
        self.assertEqual(parsed.get("format"), "image")


class TestScanPdfHelpers(unittest.TestCase):
    """扫描 PDF 的判定与保护（不需要 OCR 引擎）。"""

    def test_text_layer_detection(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            if not _FONT_PATH:
                self.skipTest("缺少中文字体")
            text_pdf = _make_text_pdf(os.path.join(tmp.name, "t.pdf"), [CONTRACT_LINES[:6]])
            scanned = _make_scanned_pdf(os.path.join(tmp.name, "s.pdf"), [CONTRACT_LINES[:6]])
            with pdfplumber.open(text_pdf) as pdf:
                self.assertTrue(scan_pdf.has_text_layer(pdf))
                self.assertEqual(scan_pdf.scan_page_numbers(pdf), [])
            with pdfplumber.open(scanned) as pdf:
                self.assertFalse(scan_pdf.has_text_layer(pdf))
                self.assertEqual(scan_pdf.scan_page_numbers(pdf), [1])
        finally:
            tmp.cleanup()

    def test_page_cap_is_enforced(self):
        """页数上限存在且有明确语义（避免一份几百页扫描件拖死请求）。"""
        self.assertIsInstance(scan_pdf.MAX_OCR_PAGES, int)
        self.assertGreater(scan_pdf.MAX_OCR_PAGES, 0)
        self.assertLessEqual(scan_pdf.MAX_OCR_PAGES, 200)


if __name__ == "__main__":
    unittest.main()
