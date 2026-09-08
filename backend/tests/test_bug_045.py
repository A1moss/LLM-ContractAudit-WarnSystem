"""BUG-045 regression：report_html 存储型 XSS 防护 + 条款比对片段幂等。

覆盖：
1. 特殊字符 / <script> / HTML attribute 注入 → 全部转义；
2. 重复调用（replace 而非 append）→ report_html 不无限增长；
3. 已存储 HTML 再展示不出现未转义的 <script>（持久化 XSS 防护）。
"""
import os
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

import api.contracts as c  # noqa: E402  重 import（约 40s）


class TestBug045Escape(unittest.TestCase):
    def test_risk_rows_escape_type_and_level(self):
        html = c._build_risk_rows([
            {"risk_type": "<script>R01</script>", "level": "<img src=x onerror=alert(1)>",
             "reason": "<b>违约</b>", "suggestion": "<i>建议</i>"},
        ])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<img src=x onerror", html)
        # reason/suggestion 也应被转义（原有逻辑保留）
        self.assertIn("&lt;b&gt;违约&lt;/b&gt;", html)

    def test_compare_rows_escape_all_fields(self):
        html = c._build_compare_rows([
            {"title": "<script>t</script>", "status": "missing",
             "deviation": "<img src=x onerror=alert(1)>", "completion": "<b>c</b>"},
        ])
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x onerror", html)
        self.assertIn("&lt;script&gt;t&lt;/script&gt;", html)
        self.assertIn("&lt;b&gt;c&lt;/b&gt;", html)


class TestBug045Idempotent(unittest.TestCase):
    def test_replace_compare_section_no_growth(self):
        base = "<html><body><h2>Report</h2><p>x</p></body></html>"
        s1 = c._replace_compare_section(base, "<h3>比对1</h3><table>a</table>")
        s2 = c._replace_compare_section(s1, "<h3>比对2</h3><table>b</table>")
        s3 = c._replace_compare_section(s2, "<h3>比对3</h3><table>c</table>")
        # 只保留最新一份比对片段，不无限追加
        self.assertEqual(s1.count("比对1"), 1)
        self.assertEqual(s2.count("比对2"), 1)
        self.assertNotIn("比对1", s2)  # 旧片段被替换
        self.assertEqual(s3.count("比对3"), 1)
        self.assertNotIn("比对2", s3)
        self.assertEqual(s3.count("<h3>比对"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
