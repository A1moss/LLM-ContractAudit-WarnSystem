"""R09 缺失条款「对话式新增」测试：DOCX 插入 + 最小可靠编号 + 建议位置 + API。

覆盖（7 场景）：
1. 段落级：采纳建议位置插入（anchor 之后、下一标题之前）+ 后续标题顺延 +1
2. 单段合同：内联标题插入 + 顺延
3. 追加到末尾（append）
4. 多轮修改同一新增条款（同位置只留最终版）
5. 改其他条款（replace）+ 新增（add_clause）同时生效
6. 位置无法定位 → 显式失败（不静默跳过）
7. 原文件不被覆盖
8. 建议位置启发式 + 建议 API（RAG 建议不替用户决定位置）

运行（backend 目录下）：
    python -m unittest tests.test_add_clause -v
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
from services.docx_reviser import (  # noqa: E402
    build_revised_docx, _final_add_clause_map, _cn_to_int, _int_to_cn,
)
import api.contracts as contracts  # noqa: E402


def _make_docx(path, paragraphs):
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))
    return str(path)


def _rev(operation="replace", scope="clause", clause_text="", revised_clause="",
         original_clause_text=None, position=None):
    return types.SimpleNamespace(scope=scope, operation=operation, clause_text=clause_text,
                                 revised_clause=revised_clause, original_clause_text=original_clause_text,
                                 position=position)


class TestAddClauseInsert(unittest.TestCase):
    """纯单元测试：build_revised_docx 的新增条款插入 + 编号。"""

    def _texts(self, path):
        return [p.text for p in Document(path).paragraphs if p.text.strip()]

    def test_insert_paragraph_level_with_renumber(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", [
                "四、合同验收", "（1）验收方式：人工审核", "五、违约责任",
                "六、争议解决", "七、合同生效",
            ])
            dst = Path(tmp) / "out.docx"
            applied, skipped = build_revised_docx(src, [
                _rev(operation="add_clause", scope="overview", clause_text="",
                     revised_clause="不可抗力条款内容", position={"anchor": "五", "hint": "第五条之后"}),
            ], str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            self.assertEqual(self._texts(str(dst)), [
                "四、合同验收", "（1）验收方式：人工审核", "五、违约责任",
                "六、不可抗力条款内容", "七、争议解决", "八、合同生效",
            ])

    def test_insert_inline_single_paragraph(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = "四、合同验收（1）验收方式：人工审核。五、违约责任：按约定承担。六、争议解决：协商或诉讼。七、合同生效。"
            src = _make_docx(Path(tmp) / "src.docx", [full])
            dst = Path(tmp) / "out.docx"
            applied, skipped = build_revised_docx(src, [
                _rev(operation="add_clause", scope="overview", clause_text="",
                     revised_clause="不可抗力条款内容", position={"anchor": "五"}),
            ], str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            text = Document(str(dst)).paragraphs[0].text
            self.assertIn("六、不可抗力条款内容", text)
            self.assertIn("七、争议解决", text)   # 原"六、争议解决"顺延
            self.assertIn("八、合同生效", text)   # 原"七、合同生效"顺延
            self.assertNotIn("六、争议解决", text)

    def test_append_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["一、标的", "二、价款"])
            dst = Path(tmp) / "out.docx"
            applied, skipped = build_revised_docx(src, [
                _rev(operation="add_clause", scope="overview", clause_text="",
                     revised_clause="新增条款内容", position={"append": True}),
            ], str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            self.assertEqual(self._texts(str(dst)), ["一、标的", "二、价款", "三、新增条款内容"])

    def test_same_position_last_wins(self):
        # 用户多轮修改同一新增条款（同位置 add_clause 多次）→ 只保留最终版
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["五、违约责任", "六、争议解决"])
            dst = Path(tmp) / "out.docx"
            applied, skipped = build_revised_docx(src, [
                _rev(operation="add_clause", scope="overview", clause_text="",
                     revised_clause="不可抗力条款V1", position={"anchor": "五"}),
                _rev(operation="add_clause", scope="overview", clause_text="",
                     revised_clause="不可抗力条款V2", position={"anchor": "五"}),
            ], str(dst))
            self.assertEqual((applied, skipped), (1, 0))
            texts = self._texts(str(dst))
            self.assertIn("六、不可抗力条款V2", texts)
            self.assertNotIn("V1", texts)

    def test_replace_and_add_both_applied(self):
        # 改其他条款（replace）+ 新增缺失条款（add_clause）同时生效
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["A条款原文", "五、违约责任", "六、争议解决"])
            dst = Path(tmp) / "out.docx"
            applied, skipped = build_revised_docx(src, [
                _rev(operation="replace", scope="clause", clause_text="A条款原文",
                     revised_clause="A条款修订", original_clause_text="A条款原文"),
                _rev(operation="add_clause", scope="overview", clause_text="",
                     revised_clause="不可抗力条款内容", position={"anchor": "五"}),
            ], str(dst))
            self.assertEqual((applied, skipped), (2, 0))
            texts = self._texts(str(dst))
            self.assertIn("A条款修订", texts)
            self.assertIn("六、不可抗力条款内容", texts)
            self.assertIn("七、争议解决", texts)  # 原"六、争议解决"顺延

    def test_position_not_found_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_docx(Path(tmp) / "src.docx", ["五、违约责任"])
            dst = Path(tmp) / "out.docx"
            with self.assertRaises(ValueError):
                build_revised_docx(src, [
                    _rev(operation="add_clause", scope="overview", clause_text="",
                         revised_clause="内容", position={"anchor": "十"}),
                ], str(dst))

    def test_original_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.docx"
            _make_docx(src, ["五、违约责任", "六、争议解决"])
            before = src.read_bytes()
            build_revised_docx(str(src), [
                _rev(operation="add_clause", scope="overview", clause_text="",
                     revised_clause="不可抗力", position={"anchor": "五"}),
            ], str(Path(tmp) / "out.docx"))
            self.assertEqual(src.read_bytes(), before)


class TestAddClauseHelpers(unittest.TestCase):
    def test_cn_int_roundtrip(self):
        self.assertEqual(_cn_to_int("五"), 5)
        self.assertEqual(_cn_to_int("十"), 10)
        self.assertEqual(_cn_to_int("十一"), 11)
        self.assertEqual(_cn_to_int("二十一"), 21)
        self.assertEqual(_int_to_cn(5), "五")
        self.assertEqual(_int_to_cn(11), "十一")
        self.assertEqual(_int_to_cn(21), "二十一")

    def test_suggest_position_prefers_dispute(self):
        text = "一、标的\n五、违约责任\n九、争议解决\n十、合同生效"
        pos = contracts._suggest_position(text)
        self.assertIsNotNone(pos)
        self.assertEqual(pos["anchor"], "五")
        self.assertIn("争议", pos["hint"])

    def test_suggest_position_fallback_breach(self):
        text = "一、标的\n五、违约责任\n六、合同生效"
        pos = contracts._suggest_position(text)
        self.assertIsNotNone(pos)
        self.assertEqual(pos["anchor"], "五")

    def test_suggest_position_no_headings(self):
        self.assertIsNone(contracts._suggest_position("没有标题结构的一段话"))

    def test_final_add_clause_map_keeps_last_per_position(self):
        revs = [
            _rev(operation="add_clause", scope="overview", clause_text="", revised_clause="V1",
                 position={"anchor": "五"}),
            _rev(operation="replace", scope="clause", clause_text="A", revised_clause="A1",
                 original_clause_text="A"),
            _rev(operation="add_clause", scope="overview", clause_text="", revised_clause="V2",
                 position={"anchor": "五"}),
        ]
        out = _final_add_clause_map(revs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["clause_text"], "V2")


class TestAddClauseApi(unittest.TestCase):
    """API 层：新增建议接口 + revise(add_clause) 持久化 + 修订版下载。"""

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

    def _add_contract(self, S, docx_path, parsed_text="一、标的\n五、违约责任\n九、争议解决"):
        s = S()
        c = Contract(user_id=1, file_name="原合同.docx", stored_path=str(docx_path),
                     parsed_text=parsed_text, status="completed", contract_type="买卖合同")
        s.add(c)
        s.commit()
        cid = c.id
        s.close()
        return cid

    def test_add_clause_suggestion_endpoint(self):
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx, ["五、违约责任", "九、争议解决"])
                cid = self._add_contract(S, docx)

                fake_rag = types.ModuleType("ai.rag")
                fake_rag.search_similar_templates = mock.MagicMock(return_value=[
                    {"text": "范本不可抗力条款", "type": "买卖", "score": 0.9}])
                fake_rag.search_knowledge = mock.MagicMock(return_value=[
                    {"law": "民法典", "article": "第590条", "title": "不可抗力", "content": "……"}])

                s = S()
                with mock.patch.dict(sys.modules, {"ai.rag": fake_rag}):
                    res = contracts.get_add_clause_suggestion(
                        cid, contracts.AddClauseSuggestionRequest(risk_type="R09", instruction=""),
                        db=s, current_user=self._user())
                s.close()
                d = res["data"]
                self.assertEqual(d["risk_type"], "R09")
                self.assertEqual(d["templates"][0]["text"], "范本不可抗力条款")
                self.assertEqual(d["legal_basis"][0]["article"], "第590条")
                self.assertIsNotNone(d["suggested_position"])
                self.assertEqual(d["suggested_position"]["anchor"], "五")  # 争议解决之前
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_revise_add_clause_persists_position(self):
        from models.clause_revision import ClauseRevision
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx, ["五、违约责任", "六、争议解决"])
                cid = self._add_contract(S, docx)

                fake_rag = types.ModuleType("ai.rag")
                fake_rag.search_knowledge = mock.MagicMock(return_value=None)
                fake_rag.search_similar_templates = mock.MagicMock(return_value=None)
                _RESULT = {"clause_text": "不可抗力条款内容", "explanation": "已生成", "legal_basis": []}

                s = S()
                with mock.patch.dict(sys.modules, {"ai.rag": fake_rag}), \
                     mock.patch.object(contracts, "generate_clause", return_value=_RESULT):
                    res = contracts.revise_contract_clause(
                        cid,
                        contracts.ReviseRequest(clause_text="", instruction="新增不可抗力",
                                                scope="overview", clause_key="__overview__",
                                                operation="add_clause", position={"anchor": "五"}),
                        db=s, current_user=self._user())
                s.close()
                self.assertEqual(res["code"], 0)
                self.assertEqual(res["data"]["revised_clause"], "不可抗力条款内容")

                s = S()
                rev = s.query(ClauseRevision).filter(ClauseRevision.contract_id == cid).first()
                s.close()
                self.assertEqual(rev.operation, "add_clause")
                self.assertEqual(rev.position, {"anchor": "五"})
                self.assertEqual(rev.scope, "overview")
        finally:
            eng.dispose()
            tmp.cleanup()

    def test_download_includes_add_clause(self):
        from models.clause_revision import ClauseRevision
        tmp, eng, S = self._setup()
        try:
            with tempfile.TemporaryDirectory() as fdir:
                docx = Path(fdir) / "原合同.docx"
                _make_docx(docx, ["五、违约责任", "六、争议解决"])
                cid = self._add_contract(S, docx)

                s = S()
                s.add(ClauseRevision(contract_id=cid, scope="overview", operation="add_clause",
                                     clause_key="__overview__", clause_text="", instruction="新增",
                                     revised_clause="不可抗力条款内容", position={"anchor": "五"}))
                s.commit()
                s.close()

                s = S()
                with mock.patch.object(contracts, "UPLOAD_DIR", fdir):
                    resp = contracts.download_revised_docx(cid, background_tasks=mock.MagicMock(),
                                                           db=s, current_user=self._user())
                s.close()
                texts = [p.text for p in Document(resp.path).paragraphs if p.text.strip()]
                self.assertEqual(texts, ["五、违约责任", "六、不可抗力条款内容", "七、争议解决"])
        finally:
            eng.dispose()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
