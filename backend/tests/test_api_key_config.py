"""DeepSeek API Key 配置边界测试（安全回归护栏，不做任何真实 LLM 调用）。

覆盖：
  1. 未配置/空/纯空格 的 Key → 导入即抛出**明确**的 RuntimeError（不静默、不把空 Key 发出去）
  2. 仓库源码中不存在硬编码的真实 DeepSeek Key（sk- + 长串）
  3. `.env` 未被 Git 追踪（防误提交真实 Key）
  4. `.env.example` 只是模板：声明了 DEEPSEEK_API_KEY 且值为空（不含真实 Key）

注意：不做真实网络调用，不读取也不打印 `.env` 里的真实 Key。

运行（backend 目录下）：
    python -m unittest tests.test_api_key_config -v
"""
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 形如真实 DeepSeek Key：sk- 后跟 20+ 位不含连字符的字母数字
_REAL_KEY_RE = re.compile(r"sk-[0-9A-Za-z]{20,}")
_SKIP_DIRS = {".git", "node_modules", "venv", "chroma_data", "dist", "__pycache__", ".vite"}


def _iter_files(root: Path):
    """遍历文件并**剪枝**跳过目录（rglob 仍会走进 node_modules，很慢）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def _read_text(path: Path, limit: int = 5 * 1024 * 1024):
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


class TestMissingKeyBehaviour(unittest.TestCase):
    """Key 缺失时必须是「明确报错」，而不是静默把空 Key 发出去。

    注意（C-008 语义变更）：报错点从 **import 期** 改到 **调用期**。
    原因是实现「用户个人 Key」后，必须允许「.env 不配系统 Key、由每个用户各自配个人 Key」
    的部署方式 —— 若 import 期就硬失败，这种部署根本起不来。
    报错内容不变：明确告知去哪配置、并点出默认 Key 的变量名，且不含 Key 本身。
    """

    def _run_with_env_key(self, value, snippet):
        env = dict(os.environ)
        env["DEEPSEEK_API_KEY"] = value      # 空串即为「未配置」（load_dotenv 不会覆盖已存在的变量）
        return subprocess.run([sys.executable, "-c", snippet],
                              cwd=str(_BACKEND_DIR), env=env, capture_output=True, text=True, timeout=120)

    _CALL_SNIPPET = (
        "import ai.llm_client as m\n"
        "print('IMPORT_OK')\n"
        "m.llm_client.chat('ping')\n"      # 无任何 Key 时应在此抛出
    )

    def test_import_succeeds_without_key_so_deployment_can_start(self):
        """没有系统 Key 也必须能正常 import（否则「用户自配 Key」的部署方式无法启动）。"""
        r = self._run_with_env_key("", "import ai.llm_client as m; print('IMPORT_OK')")
        self.assertEqual(r.returncode, 0, f"缺 Key 不应导致 import 失败：{r.stderr[-300:]}")
        self.assertIn("IMPORT_OK", r.stdout)

    def test_empty_key_raises_clear_error_on_call(self):
        r = self._run_with_env_key("", self._CALL_SNIPPET)
        self.assertNotEqual(r.returncode, 0, "空 Key 调用 LLM 时必须报错，不能静默发送")
        self.assertIn("LLMConfigError", r.stderr)
        self.assertIn("DEEPSEEK_API_KEY", r.stderr, "报错必须点名系统默认 Key 的变量名，便于定位")
        self.assertIn("个人信息", r.stderr, "报错要指引用户去「个人信息」页配置个人 Key")

    def test_whitespace_key_raises_clear_error_on_call(self):
        r = self._run_with_env_key("   ", self._CALL_SNIPPET)
        self.assertNotEqual(r.returncode, 0, "纯空格 Key 必须视为未配置（按 strip 判空）")
        self.assertIn("LLMConfigError", r.stderr)


class TestNoKeyLeakage(unittest.TestCase):
    """真实 Key 不得出现在源码/前端/模板里。"""

    def test_no_hardcoded_real_key_in_sources(self):
        offenders = []
        for p in _iter_files(_ROOT):
            if p.name == ".env":          # 本地真实配置，已被 gitignore，不参与本项断言
                continue
            text = _read_text(p)
            if text and _REAL_KEY_RE.search(text):
                offenders.append(str(p.relative_to(_ROOT)))
        self.assertEqual(offenders, [], f"以下文件疑似硬编码了真实 DeepSeek Key: {offenders}")

    def test_frontend_has_no_real_key_or_key_env(self):
        """前端只能是 Vue→自己的后端→DeepSeek：不得出现真实 Key，也不得用 Vite 构建期环境变量暴露凭证。

        说明：源码里提到变量「名字」（如个人信息页的说明文案）不是凭证泄露，故不在此断言范围内。
        """
        fe = _ROOT / "frontend"
        offenders = []
        for p in _iter_files(fe):
            if p.suffix not in (".vue", ".js", ".ts", ".json", ".html"):
                continue
            text = _read_text(p)
            if text is None:
                continue
            rel = str(p.relative_to(_ROOT))
            if _REAL_KEY_RE.search(text):
                offenders.append(f"{rel} (疑似真实 Key)")
            if re.search(r"import\.meta\.env\s*\.\s*VITE_\w*(KEY|SECRET|TOKEN)", text):
                offenders.append(f"{rel} (构建期环境变量暴露凭证)")
        self.assertEqual(offenders, [], f"前端不得携带凭证：{offenders}")

    def test_env_not_tracked_by_git(self):
        out = subprocess.run(["git", "ls-files"], cwd=str(_ROOT),
                             capture_output=True, text=True).stdout.splitlines()
        tracked_env = [f for f in out if Path(f).name == ".env" or f.endswith("/.env")]
        self.assertEqual(tracked_env, [], f"真实配置 .env 不应被 Git 追踪：{tracked_env}")

    def test_env_example_is_template_without_real_key(self):
        p = _ROOT / ".env.example"
        self.assertTrue(p.exists(), ".env.example 必须存在，供使用者复制")
        text = p.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("DEEPSEEK_API_KEY", text, "模板必须声明 DEEPSEEK_API_KEY 变量名")
        self.assertIsNone(_REAL_KEY_RE.search(text), ".env.example 里不能出现真实 Key")
        # 值应为空（引导使用者自行填写），而不是会被当成有效 Key 的占位符
        m = re.search(r"^DEEPSEEK_API_KEY\s*=(.*)$", text, re.M)
        self.assertIsNotNone(m, "模板里应有 DEEPSEEK_API_KEY= 一行")
        self.assertEqual(m.group(1).strip(), "",
                         "模板里 DEEPSEEK_API_KEY 的值应留空，避免占位符被误当成有效 Key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
