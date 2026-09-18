"""R-1 回归测试：R09「新增条款」在**同编号重复**时必须插到用户真正选择的那一处。

## 问题（根因）

`_insert_paragraph_level` 原实现用 `_heading_num(p.text) == anchor_num` 找**第一个**匹配段。
真实合同里同一顶层编号会在目录/正文（甚至通用条款/专用条款）重复出现
（实测 22 个真实 DOCX 中 12 个存在重复顶层编号），而前端保存的 `position` 只有编号，
于是「用户选择的位置」与「实际插入的位置」不一致。

## 修复

`position` 增加可选的精确定位字段 `target_text`（该标题所在整行原文）与
`paragraph_index`（行号 = 中间 DOCX 段落下标），由 `_resolve_insert_paragraph` 优先使用：

  - 精确定位唯一命中 → 插到该处；
  - 精确定位命中 0 处或多处 → **明确失败**（抛错 / 导出 400），绝不静默插错；
  - 完全没有精确定位信息 → 退回既有「按编号找第一个」语义（历史数据零回归）。

运行（backend 目录下）：
    python -m pytest tests/test_add_clause_duplicate_heading.py -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from services.docx_reviser import (  # noqa: E402
    build_revised_docx, _pos_key, _resolve_insert_paragraph, _insert_one, _final_add_clause_map,
)
from api.contracts import _headings_with_occurrences  # noqa: E402

# ── 三处「八、其他」，**编号单调递增**（真实合同的编号本来就单调，不能倒挂）──
# 每处「八」后面都跟着更高的编号，插入算法据此确定「插到哪一条之后」；
# 三处标题文本刻意完全相同，用来验证唯一的区分依据是 paragraph_index。
DUP3 = "\n".join([
    "第一条 合同标的",              # 0
    "正文甲。",                      # 1
    "八、其他",                      # 2  ← 第 1 处
    "1. 甲",                         # 3
    "九、保密",                      # 4  （八 之后的更高编号）
    "正文乙。",                      # 5
    "八、其他",                      # 6  ← 第 2 处
    "2. 乙",                         # 7
    "十、争议解决",                  # 8
    "正文丙。",                      # 9
    "八、其他",                      # 10 ← 第 3 处
    "3. 丙",                         # 11
    "十一、附则",                    # 12
    "正文丁。",                      # 13
])

# 两处「八、其他」（标题文本也相同），编号同样单调
DUP2 = "\n".join([
    "第一条 合同标的",              # 0
    "八、其他",                      # 1 ← 第 1 处
    "1. 甲",                         # 2
    "九、保密",                      # 3
    "正文乙。",                      # 4
    "八、其他",                      # 5 ← 第 2 处
    "2. 乙",                         # 6
    "十、附则",                      # 7
])

# 无重复
UNIQ = "\n".join([
    "第一条 合同标的",
    "正文甲。",
    "第二条 履行期限",
    "正文乙。",
    "第三条 违约责任",
])


class _Rev:
    def __init__(self, rid, clause_text, position, operation="add_clause"):
        self.id = rid
        self.scope = "clause"
        self.operation = operation
        self.clause_text = ""
        self.revised_clause = clause_text
        self.original_clause_text = None
        self.position = position
        self.adopted = True


class DuplicateHeadingInsertTest(unittest.TestCase):
    """核心：同编号重复时，用户选第几处就插到第几处。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def _src(self, parsed: str) -> str:
        """按 parsed_text 逐行生成源 DOCX（与中间 DOCX 生成口径一致）。"""
        path = os.path.join(self.tmp.name, "src.docx")
        doc = Document()
        for line in parsed.split("\n"):
            doc.add_paragraph(line)
        doc.save(path)
        return path

    def _insert(self, parsed, position, text="【新增条款】不可抗力：因不可抗力不能履行合同的，部分或全部免除责任。"):
        src = self._src(parsed)
        out = os.path.join(self.tmp.name, "out.docx")
        build_revised_docx(src, [_Rev(1, text, position)], out)
        return [p.text for p in Document(out).paragraphs]

    def _occurrences(self, parsed, num):
        return [o for o in _headings_with_occurrences(parsed) if o["num"] == num]

    # ── ① 同编号 2 次，选第 2 个 ──
    def test_duplicate_twice_choose_second(self):
        occ = self._occurrences(DUP2, 8)
        self.assertEqual(len(occ), 2)
        self.assertEqual(occ[1]["paragraph_index"], 5)

        paras = self._insert(DUP2, {
            "anchor": "八", "target_text": occ[1]["target_text"],
            "paragraph_index": occ[1]["paragraph_index"], "hint": "第八条之后",
        })
        i_new = next(i for i, t in enumerate(paras) if "【新增条款】" in t)
        # 必须落在**第 2 处**「八、其他」之后（即原第 4 段之后），而不是第 1 处（第 1 段）
        i_first = next(i for i, t in enumerate(paras) if t.strip() == "八、其他")
        self.assertGreater(i_new, i_first, "不能插在第 1 处之后")
        self.assertGreater(i_new, 5, f"应插在第 2 处（原第 5 段）之后，实际 i_new={i_new}")
        self.assertEqual(_heading_num_of(paras[i_new]), 9, "新条款编号应为九")

    # ── ② 同编号 3 次，选第 3 个 ──
    def test_duplicate_thrice_choose_third(self):
        occ = self._occurrences(DUP3, 8)
        self.assertEqual(len(occ), 3)
        self.assertEqual([o["paragraph_index"] for o in occ], [2, 6, 10])

        paras = self._insert(DUP3, {
            "anchor": "八", "target_text": occ[2]["target_text"],
            "paragraph_index": occ[2]["paragraph_index"], "hint": "第八条之后",
        })
        i_new = next(i for i, t in enumerate(paras) if "【新增条款】" in t)
        self.assertGreater(i_new, 10, f"应插在第 3 处（原第 10 段）之后，实际 i_new={i_new}")
        self.assertEqual(_heading_num_of(paras[i_new]), 9)

    def test_duplicate_thrice_choose_first_and_second_differ(self):
        """三处分别插入 → 落点必须两两不同，且随所选序号单调靠后。"""
        occ = self._occurrences(DUP3, 8)
        spots = []
        for o in occ:
            paras = self._insert(DUP3, {
                "anchor": "八", "target_text": o["target_text"],
                "paragraph_index": o["paragraph_index"],
            })
            spots.append(next(i for i, t in enumerate(paras) if "【新增条款】" in t))
        self.assertEqual(len(set(spots)), 3, f"三处应落点各不相同，实际 {spots}")
        self.assertEqual(spots, sorted(spots), f"应按选择顺序单调靠后，实际 {spots}")

    # ── ③ 无重复编号：行为不变 ──
    def test_unique_heading_inserts_after_it(self):
        occ = self._occurrences(UNIQ, 2)
        self.assertEqual(len(occ), 1)
        paras = self._insert(UNIQ, {
            "anchor": "二", "target_text": occ[0]["target_text"],
            "paragraph_index": occ[0]["paragraph_index"],
        })
        i_new = next(i for i, t in enumerate(paras) if "【新增条款】" in t)
        i_two = next(i for i, t in enumerate(paras) if t.strip().startswith("第二条"))
        self.assertGreater(i_new, i_two, "必须插在用户选中的第二条之后")
        self.assertEqual(_heading_num_of(paras[i_new]), 3, "新条款编号应为三")
        # 后续标题顺延：原「第三条 违约责任」→「第四条 违约责任」
        self.assertTrue(any(t.strip().startswith("第四条 违约责任") for t in paras),
                        f"原第三条应顺延为第四条：{paras}")

    def test_unique_heading_without_precise_fields_still_works(self):
        """老数据（只有 anchor）：必须与修复前行为一致。"""
        paras = self._insert(UNIQ, {"anchor": "二", "hint": "第二条之后"})
        i_new = next(i for i, t in enumerate(paras) if "【新增条款】" in t)
        self.assertEqual(_heading_num_of(paras[i_new]), 3)

    # ── ④ 无法可靠定位 → 明确失败，禁止静默插错 ──
    def test_target_text_not_found_fails_loudly(self):
        src = self._src(DUP2)
        out = os.path.join(self.tmp.name, "o.docx")
        with self.assertRaises(ValueError) as ctx:
            build_revised_docx(src, [_Rev(1, "【新增】x", {
                "anchor": "八", "target_text": "完全不存在的条款标题",
            })], out)
        self.assertIn("未找到", str(ctx.exception))
        self.assertFalse(os.path.exists(out), "失败时不得留下输出文件")

    def test_ambiguous_target_text_fails_loudly(self):
        """三处标题文本完全相同且未给行号 → 无法唯一确定 → 明确失败。"""
        src = self._src(DUP3)
        out = os.path.join(self.tmp.name, "o.docx")
        doc = Document(src)
        # 用完全相同、且在文档中出现 3 次的标题文本，且不给 paragraph_index
        with self.assertRaises(ValueError) as ctx:
            build_revised_docx(src, [_Rev(1, "【新增】x", {
                "anchor": "八", "target_text": "八、其他",
            })], out)
        self.assertIn("无法唯一定位", str(ctx.exception))
        self.assertFalse(os.path.exists(out))

    def test_wrong_anchor_number_for_target_fails(self):
        """target_text 存在但编号对不上（例如指向别处的正文）→ 拒绝，不插错。"""
        ok, msg = _insert_one(Document(self._src(DUP2)), "【新增】x", {
            "anchor": "九", "target_text": "八、其他", "paragraph_index": 1,
        })
        self.assertFalse(ok)
        self.assertTrue("未找到" in msg or "无法唯一" in msg, msg)

    def test_out_of_range_paragraph_index_falls_back_to_scan(self):
        """行号越界时应退化为全文匹配；用**标题文本唯一但编号重复**的 fixture 验证。"""
        parsed = "\n".join([
            "第一条 合同标的",
            "八、甲方义务",            # 1 ← 编号 8 第 1 处（标题文本唯一）
            "1. 甲",
            "第二条 履行期限",
            "八、乙方义务",            # 4 ← 编号 8 第 2 处（标题文本唯一）
            "2. 乙",
        ])
        occ = self._occurrences(parsed, 8)
        self.assertEqual(len(occ), 2)
        self.assertEqual(len({o["target_text"] for o in occ}), 2, "两处标题文本不同")
        paras = self._insert(parsed, {
            "anchor": "八", "target_text": occ[1]["target_text"],
            "paragraph_index": 99999,     # 越界 → 退化到全文唯一匹配
        })
        i_new = next(i for i, t in enumerate(paras) if "【新增条款】" in t)
        self.assertGreater(i_new, 5, "仍应按 target_text 定位到第 2 处（原第 5 段）之后")

    def test_identical_titles_require_paragraph_index(self):
        """三处标题文本相同时，行号是唯一可靠的区分依据；行号缺失必须明确失败。"""
        occ = self._occurrences(DUP3, 8)
        self.assertEqual(len({o["target_text"] for o in occ}), 1,
                         "fixture 里三处标题文本相同，正好用来验证行号的作用")
        # 给行号 → 三处落点各不相同且单调靠后
        spots = {}
        for o in occ:
            paras = self._insert(DUP3, {
                "anchor": "八", "target_text": o["target_text"],
                "paragraph_index": o["paragraph_index"],
            })
            spots[o["paragraph_index"]] = next(
                i for i, t in enumerate(paras) if "【新增条款】" in t)
        self.assertEqual(len(set(spots.values())), 3, f"三处必须落点不同：{spots}")
        ordered = [spots[k] for k in sorted(spots)]
        self.assertEqual(ordered, sorted(ordered), f"行号越大落点越靠后：{spots}")
        # 不给行号 → 文本相同无法唯一确定 → 明确失败（绝不插错）
        with self.assertRaises(ValueError) as ctx:
            self._insert(DUP3, {"anchor": "八", "target_text": occ[0]["target_text"]})
        self.assertIn("无法唯一定位", str(ctx.exception))


class DuplicateHeadingUnitTest(unittest.TestCase):
    """`_resolve_insert_paragraph` 的单元级行为。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def _doc(self, parsed):
        path = os.path.join(self.tmp.name, "d.docx")
        doc = Document()
        for line in parsed.split("\n"):
            doc.add_paragraph(line)
        doc.save(path)
        return Document(path)

    def test_precise_index_selects_that_paragraph(self):
        doc = self._doc(DUP3)
        for expect in (2, 6, 10):
            idx, why = _resolve_insert_paragraph(doc, {
                "anchor": "八", "target_text": "八、其他", "paragraph_index": expect})
            self.assertEqual((idx, why), (expect, ""))

    def test_index_pointing_at_non_heading_is_rejected(self):
        """行号指向正文段（不是标题）→ 校验不通过 → 不采用该行号。"""
        doc = self._doc(DUP3)
        # 第 3 段是 "1. 甲"，不是「八、」标题；由于全文扫描也命不中（标题文本重复 3 次）
        idx, why = _resolve_insert_paragraph(doc, {
            "anchor": "八", "target_text": "八、其他", "paragraph_index": 3})
        self.assertEqual(idx, -1)
        self.assertIn("无法唯一定位", why)

    def test_legacy_number_only_uses_first_match(self):
        """无精确定位信息 → 既有语义：第一个匹配。"""
        doc = self._doc(DUP3)
        idx, why = _resolve_insert_paragraph(doc, {"anchor": "八"})
        self.assertEqual((idx, why), (2, ""))

    def test_no_paragraphs_returns_failure(self):
        doc = Document()
        idx, why = _resolve_insert_paragraph(doc, {"anchor": "八"})
        self.assertEqual(idx, -1)

    def test_pos_key_prefers_target_text(self):
        """多轮修改「同位置只留最终版」的归并键：有精确定位时用精确定位 + 行号。"""
        a = _pos_key({"anchor": "八", "target_text": "八、其他", "paragraph_index": 2})
        b = _pos_key({"anchor": "八", "target_text": "八、其他", "paragraph_index": 10})
        same = _pos_key({"anchor": "八", "target_text": "八、其他", "paragraph_index": 2})
        c = _pos_key({"anchor": "八"})
        d = _pos_key({"append": True})
        self.assertTrue(a.startswith("target:"))
        self.assertEqual(a, same, "同一处（同文本同行号）视为同一位置")
        self.assertNotEqual(a, b, "同编号的不同出现必须区分开，否则会互相覆盖")
        self.assertEqual(c, "anchor:八")
        self.assertEqual(d, "append")

    def test_final_add_clause_map_dedupes_by_precise_position(self):
        """同一精确定位的多轮修改只保留最终版；不同出现各自保留一条。"""
        revs = [
            _Rev(1, "V1", {"anchor": "八", "target_text": "八、其他", "paragraph_index": 2}),
            _Rev(2, "V2", {"anchor": "八", "target_text": "八、其他", "paragraph_index": 2}),
            _Rev(3, "V3", {"anchor": "八", "target_text": "八、其他", "paragraph_index": 10}),
        ]
        got = _final_add_clause_map(revs)
        self.assertEqual(len(got), 2, "两处不同位置 → 两条")
        self.assertEqual([g["clause_text"] for g in got], ["V2", "V3"], "同位置取最后一条")

    def test_legacy_positions_still_dedupe_by_anchor(self):
        """老数据（只有编号）归并行为逐字不变。"""
        revs = [_Rev(1, "V1", {"anchor": "五"}), _Rev(2, "V2", {"anchor": "五"})]
        got = _final_add_clause_map(revs)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["clause_text"], "V2")


class HeadingOccurrencesTest(unittest.TestCase):
    """后端 `_headings_with_occurrences` 必须给出每一处出现及其精确定位信息。"""

    def test_returns_every_occurrence_with_target_text(self):
        occ = _headings_with_occurrences(DUP3)
        eight = [o for o in occ if o["num"] == 8]
        self.assertEqual(len(eight), 3, "必须返回全部 3 处，而不是去重后的 1 处")
        self.assertEqual([o["paragraph_index"] for o in eight], [2, 6, 10])
        self.assertTrue(all(o["target_text"] == "八、其他" for o in eight))
        # start 必须单调递增，可用于排序与展示
        self.assertEqual([o["start"] for o in eight], sorted(o["start"] for o in eight))

    def test_target_text_is_the_whole_line(self):
        text = "前言。\n第七条 词语定义与解释\n正文。"
        occ = [o for o in _headings_with_occurrences(text) if o["num"] == 7]
        self.assertEqual(len(occ), 1)
        self.assertEqual(occ[0]["target_text"], "第七条 词语定义与解释")
        self.assertEqual(occ[0]["paragraph_index"], 1)

    def test_deduped_headings_contract_unchanged(self):
        """既有 `_parse_headings` 仍按编号去重（老前端契约不变）。"""
        from api.contracts import _parse_headings
        deduped = _parse_headings(DUP3)
        self.assertEqual([h["num"] for h in deduped], [1, 8, 9, 10, 11])
        self.assertEqual(len([h for h in deduped if h["num"] == 8]), 1)

    def test_inline_reference_is_not_a_heading_occurrence(self):
        """行内引用（正文里提到「通用条款第 7 条」）不能被当成可插入的标题位置。

        判据与 `docx_reviser._heading_num` 一致：标题必须位于**行首**。
        否则位置选择器会出现一个"选了必然失败"的假选项。
        （真实 PDF 合同 95 里就存在这样的行内引用，实测被正确排除。）
        """
        text = "\n".join([
            "第一条 合同标的",
            "七、争议解决",                                  # 行首真标题
            "本合同未尽事宜按通用条款第 7 条的约定办理。",    # 行内引用，不是标题
            "第八条 附则",
        ])
        occ = _headings_with_occurrences(text)
        seven = [o for o in occ if o["num"] == 7]
        self.assertEqual(len(seven), 1, f"只应识别行首标题，实际 {seven}")
        self.assertEqual(seven[0]["target_text"], "七、争议解决")
        self.assertEqual(seven[0]["paragraph_index"], 1)

    def test_every_exposed_occurrence_is_actually_insertable(self):
        """位置选择器里暴露的每一处，后端都必须能定位（不许出现"选了就失败"的假选项）。"""
        import tempfile as _tf
        occ = [o for o in _headings_with_occurrences(DUP3) if o["num"] == 8]
        self.assertEqual(len(occ), 3)
        with _tf.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = os.path.join(d, "d.docx")
            doc = Document()
            for line in DUP3.split("\n"):
                doc.add_paragraph(line)
            doc.save(path)
            for o in occ:
                idx, why = _resolve_insert_paragraph(Document(path), {
                    "anchor": o["cn"], "target_text": o["target_text"],
                    "paragraph_index": o["paragraph_index"]})
                self.assertEqual((idx, why), (o["paragraph_index"], ""),
                                 f"para_index={o['paragraph_index']} 应可定位")


def _heading_num_of(text: str):
    from services.docx_reviser import _heading_num
    return _heading_num(text)


REAL_DB = _BACKEND_DIR / "contract.db"


@unittest.skipUnless(REAL_DB.is_file(), "真实库 contract.db 不存在")
class RealContractDuplicateHeadingTest(unittest.TestCase):
    """真实合同 E2E：用户选第几处，就插到第几处（用真实 PDF 合同 95 的 parsed_text）。

    合同 95 里「七、争议解决」等编号重复出现多次 —— 正是 R-1 的真实触发场景。
    真实库**只读**，中间 DOCX 生成在临时目录，不写任何数据。
    """

    @classmethod
    def setUpClass(cls):
        import sqlite3
        con = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("select parsed_text from contracts where id=95").fetchone()
            if row is None:
                raise unittest.SkipTest("真实库里没有合同 95")
            cls.parsed = row["parsed_text"] or ""
        finally:
            con.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "intermediate.docx")
        from services.intermediate_docx import build_from_parsed_text
        build_from_parsed_text(self.parsed, self.src)

    def _insert_and_find(self, position, text):
        out = os.path.join(self.tmp.name, f"o_{abs(hash(text))}.docx")
        build_revised_docx(self.src, [_Rev(1, text, position)], out)
        return [p.text for p in Document(out).paragraphs]

    def test_real_contract_has_duplicate_numbered_headings(self):
        occ = [o for o in _headings_with_occurrences(self.parsed) if o["num"] == 7]
        self.assertGreater(len(occ), 1, f"真实合同 95 的「七」应重复出现：{occ}")

    def test_each_occurrence_is_addressable_and_lands_there(self):
        """对「七」的每一处分别插入 → 落点各不相同、单调靠后，且都在该处之后。"""
        occ = [o for o in _headings_with_occurrences(self.parsed) if o["num"] == 7]
        spots = []
        for k, o in enumerate(occ, start=1):
            paras = self._insert_and_find(
                {"anchor": o["cn"], "target_text": o["target_text"],
                 "paragraph_index": o["paragraph_index"]},
                f"【R1-REAL-{k}】不可抗力条款正文。")
            i_new = next(i for i, t in enumerate(paras) if f"【R1-REAL-{k}】" in t)
            self.assertGreater(i_new, o["paragraph_index"],
                               f"第{k}处必须插在该处标题之后")
            self.assertEqual(_heading_num_of(paras[i_new]), 8, "新条款编号应为八")
            spots.append(i_new)
        self.assertEqual(len(set(spots)), len(spots), f"各处落点必须不同：{spots}")
        self.assertEqual(spots, sorted(spots), f"应按出现顺序单调靠后：{spots}")

    def test_number_only_cannot_distinguish_occurrences(self):
        """对照：只给编号（修复前的 position 形态）时，各处落点完全相同 —— 这正是 R-1。"""
        occ = [o for o in _headings_with_occurrences(self.parsed) if o["num"] == 7]
        spots = set()
        for _ in occ:
            paras = self._insert_and_find({"anchor": "七"}, "【R1-LEGACY】x")
            spots.add(next(i for i, t in enumerate(paras) if "【R1-LEGACY】" in t))
        self.assertEqual(len(spots), 1, f"只给编号时应无法区分（复现 R-1）：{spots}")

    def test_inline_reference_excluded_in_real_contract(self):
        """真实合同里存在的行内引用（「按本合同通用条款第 7 条…」）必须被排除在标题之外。"""
        occ = [o for o in _headings_with_occurrences(self.parsed) if o["num"] == 7]
        for o in occ:
            self.assertFalse(o["target_text"].startswith("部分的款项"),
                             f"行内引用不应出现在可插入位置里：{o}")


if __name__ == "__main__":
    unittest.main()
