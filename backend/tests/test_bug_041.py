"""BUG-041 regression：法条精确检索支持「民法典」（法|典 结尾）。
修复前正则只匹配「X法」，「民法典」以「典」结尾 → 无匹配 → 返回裸编号。
"""
import os
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from ai.auditor.recommendation_engine import _retrieve_legal  # noqa: E402


class TestBug041RetrieveLegal(unittest.TestCase):
    def test_civil_code_returns_content(self):
        r = _retrieve_legal("R01", "违约金过高", "")
        self.assertNotEqual(r, "民法典第585条")  # 修复后返回条文原文，而非裸编号
        self.assertTrue(len(r) > 10, r)

    def test_law_suffix_still_works(self):
        # 对照：以「法」结尾的法律（劳动合同法）修复前即能命中，回归不退化
        r = _retrieve_legal("R10", "竞业限制过宽", "")
        self.assertNotEqual(r, "劳动合同法第24条")


if __name__ == "__main__":
    unittest.main(verbosity=2)
