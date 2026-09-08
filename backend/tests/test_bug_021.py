"""BUG-021 / BUG-021-a regression：grounding 中文百分比识别 + 前端展示字段（后端侧）。

覆盖：
1. grounding 能拦截「百分之十」（修复前漏拦、修复后能拦）；
2. 正常阿拉伯数字（有证据来源）不被误拦；
3. 阿拉伯数字无证据来源时应被拦（对照）。
"""
import os
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from ai.auditor.recommendation_engine import recommendation_grounding_check  # noqa: E402


class TestBug021GroundingCnPercent(unittest.TestCase):
    def test_cn_percent_caught(self):
        # 修复前：百分之十 被拆成 百/十 单字跳过 → passed=True（漏拦）
        rec = {"risk_description": "违约金过高", "suggestion": "建议改为百分之十", "example": "示例"}
        res = recommendation_grounding_check(rec, {})
        self.assertFalse(res["passed"])
        self.assertTrue(any("百分之十" in i for i in res["issues"]), res["issues"])

    def test_arabic_number_with_evidence_passes(self):
        # 有证据来源的阿拉伯数字不应被误拦
        rec = {"risk_description": "违约金过高", "suggestion": "建议调整为10%", "example": "示例"}
        res = recommendation_grounding_check(rec, {"R01_违约金": {"clause_text": "违约金10%"}})
        self.assertTrue(res["passed"], res["issues"])

    def test_arabic_number_without_evidence_caught(self):
        rec = {"risk_description": "违约金过高", "suggestion": "建议调整为30%", "example": "示例"}
        res = recommendation_grounding_check(rec, {})
        self.assertFalse(res["passed"])
        self.assertTrue(any("30" in i for i in res["issues"]), res["issues"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
