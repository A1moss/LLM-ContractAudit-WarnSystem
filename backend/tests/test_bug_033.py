"""BUG-033 回归测试：RAG 检索身份链可靠（content 反查 → metadata/id 关联 + 缓存失效）。

覆盖：
1. NDA-011 / PUR-018 同 content 时身份不串；
2. KB 内容更新后 cache 自动失效（_json_cache + BM25 共用版本号）；
3. 正常检索结果 metadata 与原 JSON 一致。

运行（backend 目录下）：
    python -m unittest tests.test_bug_033 -v
注意：顶层 import ai.rag.vector_store 会拉 chromadb + sentence_transformers（约 35s，一次性）。
"""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 顶层 import 触发 chromadb + sentence_transformers（重，一次性）
import ai.rag.vector_store as vs  # noqa: E402

DUPLICATE_CONTENT = "明确双方的联系方式、通知送达方式及视为送达的条件。"


def _data_with_duplicate():
    return [
        {"id": "NDA-011", "title": "通知与送达", "type": "保密协议", "content": DUPLICATE_CONTENT},
        {"id": "PUR-018", "title": "通知与送达", "type": "买卖合同", "content": DUPLICATE_CONTENT},
    ]


class TestBug033Identity(unittest.TestCase):
    def setUp(self):
        vs._json_cache.clear()

    def test_duplicate_content_identity_not_swapped(self):
        data = _data_with_duplicate()
        results = {
            "documents": [[DUPLICATE_CONTENT]],
            "ids": [["standard_clauses_NDA-011"]],
            "metadatas": [[{"clause_id": "NDA-011", "title": "通知与送达"}]],
            "distances": [[0.1]],
        }
        hits = vs._map_dense_hits(data, results, "standard_clauses")
        # 命中 NDA-011 → 下标 0；绝不因 content 相同误指到 PUR-018（下标 1）
        self.assertEqual(hits, [(0, 0.9)])

    def test_duplicate_content_other_doc(self):
        data = _data_with_duplicate()
        results = {
            "documents": [[DUPLICATE_CONTENT]],
            "ids": [["standard_clauses_PUR-018"]],
            "metadatas": [[{"clause_id": "PUR-018"}]],
            "distances": [[0.2]],
        }
        hits = vs._map_dense_hits(data, results, "standard_clauses")
        self.assertEqual(hits, [(1, 0.8)])

    def test_fallback_to_chroma_id_when_no_metadata(self):
        # 旧集合（无 metadatas）：回退解析 Chroma id "{coll}_{id}"
        data = [
            {"id": "PUR-018", "title": "通知与送达", "type": "买卖合同", "content": DUPLICATE_CONTENT},
            {"id": "NDA-011", "title": "通知与送达", "type": "保密协议", "content": DUPLICATE_CONTENT},
        ]
        results = {
            "documents": [[DUPLICATE_CONTENT]],
            "ids": [["standard_clauses_NDA-011"]],
            "distances": [[0.15]],
        }
        hits = vs._map_dense_hits(data, results, "standard_clauses")
        self.assertEqual(hits, [(1, 0.85)])

    def test_unmappable_hit_logged_not_silent(self):
        data = _data_with_duplicate()
        results = {
            "documents": [["某条已不存在的旧文档"]],
            "ids": [["standard_clauses_GONE-999"]],
            "metadatas": [[{"clause_id": "GONE-999"}]],
            "distances": [[0.3]],
        }
        with self.assertLogs("ai.rag.vector_store", level="WARNING") as cm:
            hits = vs._map_dense_hits(data, results, "standard_clauses")
        self.assertEqual(hits, [])
        self.assertTrue(any("无法映射" in line for line in cm.output))


class TestBug033Cache(unittest.TestCase):
    def setUp(self):
        vs._json_cache.clear()

    def test_cache_invalidates_on_file_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            kb_file = tmp / "laws.json"
            kb_file.write_text(
                json.dumps([{"id": 1, "law": "民法典", "content": "A"}], ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch.object(vs, "KNOWLEDGE_DIR", str(tmp)):
                d1 = vs._load_knowledge_json("laws")
                self.assertEqual(d1[0]["content"], "A")
                # 改内容（同时改变 size），应自动失效重载
                kb_file.write_text(
                    json.dumps([{"id": 1, "law": "民法典", "content": "BB"}], ensure_ascii=False), encoding="utf-8"
                )
                d2 = vs._load_knowledge_json("laws")
                self.assertEqual(d2[0]["content"], "BB")
        vs._json_cache.clear()


class TestBug033Metadata(unittest.TestCase):
    def test_build_metadatas_matches_json(self):
        data = [
            {"id": "NDA-011", "title": "通知与送达", "type": "保密协议", "source": "民法典第501条"},
            {"id": 1, "law": "民法典", "article": "第五百零一条", "title": "保密义务", "content": "..."},
        ]
        metas = vs._build_metadatas(data)
        self.assertEqual(metas[0], {
            "clause_id": "NDA-011", "title": "通知与送达",
            "type": "保密协议", "source": "民法典第501条",
        })
        self.assertEqual(metas[1]["clause_id"], "1")
        self.assertEqual(metas[1]["law"], "民法典")
        self.assertEqual(metas[1]["article"], "第五百零一条")
        self.assertEqual(metas[1]["source"], "民法典第五百零一条")


if __name__ == "__main__":
    unittest.main(verbosity=2)
