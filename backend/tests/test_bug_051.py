"""BUG-051 regression：R13 名实不符的否定语境排除。
修复前「不得采用劳务派遣」这类风险防范条款同时命中名义+实质信号 → R13 误报 high。
"""
import os
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from ai.auditor.rule_engine import run_rules  # noqa: E402


def _types(text):
    return {r["risk_type"] for r in run_rules(text)}


class TestBug051R13Negation(unittest.TestCase):
    def test_negated_dispatch_not_r13(self):
        # 风险防范条款（明确禁止派遣），修复前误报 R13 high
        types = _types("本服务外包合同约定，承包方应自行组织员工，不得采用劳务派遣方式提供人员。")
        self.assertNotIn("R13", types)

    def test_true_positive_still_r13(self):
        # 真阳性（名为买卖实为借贷）修复后仍应命中 R13，不因否定排除误伤
        types = _types("本合同名为买卖，实为借贷，约定固定回报与到期回购。")
        self.assertIn("R13", types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
