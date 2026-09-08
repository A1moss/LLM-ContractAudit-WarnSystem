"""BUG-046 regression：条款标题识别（日期/金额/普通数字误判 + 条款标题变体）。

覆盖：
- 负例（不应识别为标题）：日期、金额、4 位年份；
- 正例（应识别为标题）：第X条（中文/阿拉伯/带空格/带小数）、一、二、（一）、1. 列表项。
"""
import os
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from ai.chunker import _HEADING_RE  # noqa: E402


class TestBug046Heading(unittest.TestCase):
    def _is_heading(self, line):
        return _HEADING_RE.match(line) is not None

    def test_positive_clause_titles(self):
        for line in [
            "第一条 总则",
            "第五条 合同变更与解除",
            "第 1 条 总则",
            "第1.1条 分则",
            "一、总则",
            "（一）总则",
            "1. 合同条款",
            "12. 违约责任",
            # 边界 A：空格 + 多级编号变体均不误伤
            "第 12.1 条",
            "第 12 条",
            "第12条",
            "第 1.1 条",
            "第1.1条",
        ]:
            self.assertTrue(self._is_heading(line), f"应识别为标题: {line!r}")

    def test_negative_dates_amounts_numbers(self):
        for line in [
            "2024.03.15 交付",
            "2024. 交付日期",
            "100.5万元",
            "100. 金额",
            "2024.03.15",
            # 边界 B：金额/数值/百分比不被误判为标题
            "100.1 元",
            "100.1万元",
            "100.1%",
            "2024.1",
        ]:
            self.assertFalse(self._is_heading(line), f"不应识别为标题: {line!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
