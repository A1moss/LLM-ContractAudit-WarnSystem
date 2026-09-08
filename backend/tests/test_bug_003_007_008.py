"""BUG-003 / BUG-007 / BUG-008 回归单测（全部离线，不调真实 LLM）。

覆盖：
- BUG-007：_merge_elements 通用嵌套 dict 逐字段非空合并 + 顺序无关（4 用例 + 根因回归）。
- BUG-003：证据抽取「成功 + 空 evidence → 0 风险」vs「失败 → failure/degraded」不混淆。
- BUG-008：_extract_chunk 失败(None) 与 成功空结果({}) 语义区分；_extract_one 部分失败
  保留成功块结果、失败块可统计；extract_evidence_detailed 状态信封。

运行方式（backend 目录下任选其一）：
    python tests/test_bug_003_007_008.py
    python -m unittest tests.test_bug_003_007_008 -v
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# 1) 让 `import ai...` 可用（本文件位于 backend/tests/，父目录即 backend）
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 2) 防 import ai.llm_client 时因缺 DEEPSEEK_API_KEY 抛 RuntimeError（本测试不调真实 LLM）。
#    load_dotenv(override=False) 不会覆盖已存在的环境变量。
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from ai.extractor.extractor import _merge_elements  # noqa: E402
from ai.auditor import evidence_extractor  # noqa: E402
from ai.auditor.evidence_adjudicator import adjudicate_risks  # noqa: E402


# 成功但"无任何风险事实"的证据块（真实 LLM 会返回完整结构、各字段为中性/空值）。
EMPTY_EVIDENCE = {
    "contract_type": "买卖",
    "is_delivery_type": False,
    "R01_违约金": {"exists": False, "rate": 0.0, "unit": "", "basis": "", "liable_party": "", "clause_text": ""},
}

# 成功且"命中 R01"的证据块。
RESULT_EVIDENCE = {
    "contract_type": "买卖",
    "is_delivery_type": True,
    "R01_违约金": {
        "exists": True, "rate": 0.03, "unit": "daily",
        "basis": "合同总价", "liable_party": "乙方", "clause_text": "每日按合同总价3%支付违约金",
    },
}


class TestBug007MergeElements(unittest.TestCase):
    """BUG-007：验证 _merge_elements 真正修复，而非只看旧 cache。"""

    def _base(self, **kw):
        d = {
            "parties": None, "amount": None, "sign_date": None,
            "performance_period": None, "dispute_resolution": None, "governing_law": None,
        }
        d.update(kw)
        return d

    def test_case1_both_fields_preserved(self):
        # chunk1 提供 amount，chunk2 提供 performance_period（term），两者都应保留
        c1 = self._base(amount={"value": 100, "currency": "CNY", "text": "壹佰元"})
        c2 = self._base(performance_period={"start": "2024-01-01", "end": "2024-12-31"})
        m = _merge_elements([c1, c2])
        self.assertEqual(m["amount"]["value"], 100)
        self.assertEqual(m["performance_period"]["start"], "2024-01-01")
        self.assertEqual(m["performance_period"]["end"], "2024-12-31")

    def test_case2_null_then_value(self):
        c1 = self._base(amount=None)
        c2 = self._base(amount={"value": 100, "currency": "CNY", "text": "壹佰元"})
        m = _merge_elements([c1, c2])
        self.assertEqual(m["amount"]["value"], 100)

    def test_case3_value_then_null(self):
        c1 = self._base(amount={"value": 100, "currency": "CNY", "text": "壹佰元"})
        c2 = self._base(amount=None)
        m = _merge_elements([c1, c2])
        self.assertEqual(m["amount"]["value"], 100)

    def test_case4_order_independent(self):
        a = self._base(
            amount={"value": 100, "currency": None, "text": None},
            performance_period={"start": None, "end": "2024-12-31"},
        )
        b = self._base(
            amount={"value": None, "currency": "CNY", "text": None},
            performance_period={"start": "2024-01-01", "end": None},
        )
        m1 = _merge_elements([a, b])
        m2 = _merge_elements([b, a])
        self.assertEqual(m1, m2)
        self.assertEqual(m1["amount"], {"value": 100, "currency": "CNY", "text": None})
        self.assertEqual(m1["performance_period"], {"start": "2024-01-01", "end": "2024-12-31"})

    def test_nested_all_null_dict_does_not_block(self):
        # 原 BUG-007 根因：首块返回全 null 的嵌套 dict 挡住后续块的正确值
        c1 = self._base(amount={"value": None, "currency": None, "text": None})
        c2 = self._base(amount={"value": 500000, "currency": "CNY", "text": "伍拾万"})
        m = _merge_elements([c1, c2])
        self.assertEqual(m["amount"]["value"], 500000)
        self.assertEqual(m["amount"]["currency"], "CNY")

    def test_parties_nested_merge(self):
        c1 = self._base(parties={"甲方": "杭州科技有限公司", "乙方": None})
        c2 = self._base(parties={"甲方": None, "乙方": "上海软件有限公司"})
        m = _merge_elements([c1, c2])
        self.assertEqual(m["parties"], {"甲方": "杭州科技有限公司", "乙方": "上海软件有限公司"})

    def test_non_dict_result_skipped(self):
        c1 = self._base(sign_date="2024-03-15")
        m = _merge_elements([c1, None, "garbage", 123])
        self.assertEqual(m["sign_date"], "2024-03-15")


class TestBug008ChunkStatus(unittest.TestCase):
    """BUG-008：_extract_chunk 失败(None) 与 成功空结果({}) 语义区分。"""

    def test_success_empty_returns_empty_dict(self):
        with mock.patch.object(evidence_extractor.llm_client, "chat", return_value="{}"):
            ev = evidence_extractor._extract_chunk("任意合同文本")
        self.assertEqual(ev, {})  # 成功 + 空 → 空 dict，不是 None

    def test_exception_returns_none(self):
        with mock.patch.object(evidence_extractor.llm_client, "chat", side_effect=RuntimeError("boom")):
            ev = evidence_extractor._extract_chunk("任意合同文本")
        self.assertIsNone(ev)  # 失败 → None

    def test_unparseable_returns_none(self):
        with mock.patch.object(evidence_extractor.llm_client, "chat", return_value="这不是JSON"):
            ev = evidence_extractor._extract_chunk("任意合同文本")
        self.assertIsNone(ev)

    def test_non_dict_json_returns_none(self):
        with mock.patch.object(evidence_extractor.llm_client, "chat", return_value='[1,2,3]'):
            ev = evidence_extractor._extract_chunk("任意合同文本")
        self.assertIsNone(ev)


class TestBug003EvidenceStatus(unittest.TestCase):
    """BUG-003：成功空 evidence=0风险，失败=degraded，两者不混淆。"""

    def test_success_empty_evidence_is_zero_risk(self):
        with mock.patch.object(evidence_extractor, "split_chunks", return_value=["one"]), \
             mock.patch.object(evidence_extractor, "_extract_chunk", return_value=dict(EMPTY_EVIDENCE)):
            res = evidence_extractor.extract_evidence_detailed("任意文本")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["failed_chunks"], 0)
        self.assertEqual(res["total_chunks"], 1)
        # 正常空 evidence 必须继续表示"没有提取到风险证据" → 0 风险
        self.assertEqual(adjudicate_risks(res["evidence"]), [])

    def test_failure_is_degraded_not_zero_risk(self):
        with mock.patch.object(evidence_extractor, "split_chunks", return_value=["one"]), \
             mock.patch.object(evidence_extractor, "_extract_chunk", return_value=None):
            res = evidence_extractor.extract_evidence_detailed("任意文本")
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["failed_chunks"], 1)
        self.assertEqual(res["total_chunks"], 1)
        self.assertEqual(res["evidence"], {})
        # 失败状态能被上层识别：status != success（上层应降级，绝不把失败当 0 风险）

    def test_extract_evidence_backward_compat_returns_plain_dict(self):
        # 评测脚本（run_evidence / ab_normalize）仍拿到纯 evidence dict，不破坏评测链
        with mock.patch.object(evidence_extractor, "split_chunks", return_value=["one"]), \
             mock.patch.object(evidence_extractor, "_extract_chunk", return_value=dict(EMPTY_EVIDENCE)):
            ev = evidence_extractor.extract_evidence("任意文本")
        self.assertIsInstance(ev, dict)
        self.assertEqual(ev, EMPTY_EVIDENCE)


class TestBug008PartialFailure(unittest.TestCase):
    """BUG-008：部分块失败——成功结果保留、失败可识别、空结果不与失败混淆。"""

    def test_partial_failure_chain(self):
        # 固定 3 块：A=成功有结果，B=失败，C=成功但无结果
        chunks = ["chunkA", "chunkB", "chunkC"]

        def fake_split(text, max_chars):
            return list(chunks)

        def fake_extract(chunk):
            if chunk == "chunkA":
                return dict(RESULT_EVIDENCE)
            if chunk == "chunkB":
                return None  # 失败
            if chunk == "chunkC":
                return dict(EMPTY_EVIDENCE)  # 成功但空
            raise AssertionError(f"unexpected chunk: {chunk!r}")

        with self.assertLogs("ai.auditor.evidence_extractor", level="WARNING") as cm, \
             mock.patch.object(evidence_extractor, "split_chunks", side_effect=fake_split), \
             mock.patch.object(evidence_extractor, "_extract_chunk", side_effect=fake_extract):
            res = evidence_extractor.extract_evidence_detailed("任意文本")

        self.assertEqual(res["status"], "partial")
        self.assertEqual(res["failed_chunks"], 1)   # 只有 B 失败
        self.assertEqual(res["total_chunks"], 3)
        # 日志明确记录 partial failure（要求4）
        self.assertTrue(any("块失败" in line for line in cm.output), cm.output)
        # A 的结果保留
        self.assertEqual(res["evidence"]["R01_违约金"]["exists"], True)
        self.assertEqual(res["evidence"]["R01_违约金"]["rate"], 0.03)
        # 成功块证据仍可正常裁决出风险
        self.assertIn("R01", {r["risk_type"] for r in adjudicate_risks(res["evidence"])})
        # C 的 empty 与 B 的 failure 不混淆：B 体现在 failed_chunks=1，而不是 evidence 里多出什么

    def test_all_failed_is_failed(self):
        with mock.patch.object(evidence_extractor, "split_chunks", return_value=["a", "b", "c"]), \
             mock.patch.object(evidence_extractor, "_extract_chunk", return_value=None):
            res = evidence_extractor.extract_evidence_detailed("任意文本")
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["failed_chunks"], 3)
        self.assertEqual(res["evidence"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
