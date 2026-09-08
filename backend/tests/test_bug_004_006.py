"""BUG-004 / BUG-006 regression：裁决器数值归一化 + R10/R11 同义归一化。

覆盖（对应《交付前系统性Bug扫尾审计报告》BUG-004/006 的最小复现）：
1. BUG-004：LLM 证据数值写成字符串时，旧 isinstance 静默跳过 → R01/R07/R10 漏检；
   修复后 _num 接受 int/float/str 并剥离 %/‰ 换算。
2. BUG-006：R10 scope 精确集合匹配、R11 mode 精确等值匹配，中文同义表述即漏检；
   修复后 _normalize_scope/_normalize_mode 归一化。
"""
import os
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from ai.auditor.evidence_adjudicator import (  # noqa: E402
    adjudicate_risks,
    _num,
    _normalize_scope,
    _normalize_mode,
)


def _risks(evidence: dict) -> set:
    return {r["risk_type"] for r in adjudicate_risks(evidence)}


class TestBug004Num(unittest.TestCase):
    def test_float_passthrough(self):
        self.assertEqual(_num(0.005), 0.005)

    def test_int_passthrough(self):
        self.assertEqual(_num(5), 5.0)

    def test_string_number(self):
        self.assertEqual(_num("0.005"), 0.005)
        self.assertEqual(_num("5"), 5.0)

    def test_percent_conversion(self):
        # "0.5%" -> 0.005（百分比换算）
        self.assertEqual(_num("0.5%"), 0.005)

    def test_permille_conversion(self):
        # "5‰" -> 0.005（千分比换算）
        self.assertEqual(_num("5‰"), 0.005)

    def test_null_bool_empty_return_none(self):
        # None / bool / 空串 一律 None，绝不当作数值
        self.assertIsNone(_num(None))
        self.assertIsNone(_num(False))
        self.assertIsNone(_num(True))
        self.assertIsNone(_num(""))

    def test_unparseable_return_none(self):
        self.assertIsNone(_num("abc"))

    def test_comma_stripped(self):
        self.assertEqual(_num("1,000"), 1000.0)


class TestBug006NormalizeScope(unittest.TestCase):
    def test_national_synonyms(self):
        for s in ("全国范围", "全球", "境内", "不限地域", "不限地区", "所有区域"):
            self.assertEqual(_normalize_scope(s), "全国", s)

    def test_main_business_synonym(self):
        self.assertEqual(_normalize_scope("主营"), "主营业务")

    def test_all_industry_synonym(self):
        self.assertEqual(_normalize_scope("所有行业"), "全行业")

    def test_contains_national(self):
        self.assertEqual(_normalize_scope("同行业全国"), "全国")

    def test_unchanged_other(self):
        self.assertEqual(_normalize_scope("广东省"), "广东省")

    def test_non_str_passthrough(self):
        self.assertIsNone(_normalize_scope(None))


class TestBug006NormalizeMode(unittest.TestCase):
    def test_english_mode_passthrough(self):
        self.assertEqual(_normalize_mode("silence_auto_renewal"), "silence_auto_renewal")

    def test_chinese_synonyms_to_silence(self):
        for m in ("沉默自动续约", "沉默续约", "自动续约", "到期自动续", "期满自动续"):
            self.assertEqual(_normalize_mode(m), "silence_auto_renewal", m)

    def test_active_renewal_not_silence(self):
        self.assertEqual(_normalize_mode("主动续签"), "主动续签")
        self.assertEqual(_normalize_mode("active_renewal"), "主动续签")

    def test_non_str_passthrough(self):
        self.assertIsNone(_normalize_mode(None))


class TestBug004006Adjudicate(unittest.TestCase):
    """端到端：修复前漏检、修复后命中的裁决用例。"""

    def test_r01_string_rate_caught(self):
        # 审计报告 BUG-004：rate="0.01" -> 修复前 []，修复后 ['R01']
        self.assertIn("R01", _risks({"R01_违约金": {"exists": True, "unit": "daily", "rate": "0.01"}}))

    def test_r07_string_percent_caught(self):
        # prepay "80%" -> 0.8 >= 0.8 → R07
        self.assertIn("R07", _risks({"R07_付款": {"prepay_ratio": "80%"}}))

    def test_r10_string_years_and_scope_caught(self):
        # duration "6" + scope "全国范围" → R10（字符串数值 + 地域同义 双归一化）
        self.assertIn("R10", _risks({"R10_竞业": {"has_noncompete": True, "duration_years": "6", "scope": "全国范围"}}))

    def test_r11_chinese_mode_caught(self):
        self.assertIn("R11", _risks({"R11_续约": {"mode": "沉默自动续约"}}))

    # 负例：归一化不应造成过捕
    def test_r01_below_threshold_not_caught(self):
        self.assertNotIn("R01", _risks({"R01_违约金": {"exists": True, "unit": "daily", "rate": "0.001"}}))

    def test_r07_below_threshold_not_caught(self):
        self.assertNotIn("R07", _risks({"R07_付款": {"prepay_ratio": "10%"}}))

    def test_r10_below_years_not_caught(self):
        self.assertNotIn("R10", _risks({"R10_竞业": {"has_noncompete": True, "duration_years": "3", "scope": "全国"}}))


if __name__ == "__main__":
    unittest.main()
