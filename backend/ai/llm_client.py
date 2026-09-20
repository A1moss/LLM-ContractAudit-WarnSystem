"""ai.llm_client — 统一 DeepSeek 客户端（全项目唯一的 LLM 出口）。

Key 解析优先级：**用户个人 Key > .env 的 `DEEPSEEK_API_KEY`（系统默认 Key）**

* 用户在「个人信息」页配了个人 Key → 该用户自己的请求用它
* 用户没配个人 Key                 → 用 .env 系统默认 Key
* 两者都没有                       → 抛 `LLMConfigError`，提示如何配置（绝不发送空 Key）

并发隔离设计（重要）
--------------------
`DeepSeekClient` **不保存 api_key**，也不在调用前修改任何全局状态；每次 `chat()` 都
按当前上下文解析 Key（见 `ai/llm_context.py`）并取「按 Key 缓存」的 OpenAI client。
因此 A / B / 无个人 Key 三种请求天然隔离，不存在「A 改了 singleton 的 key、
B 随后用到 A 的 Key」的串 Key 风险。

为什么不做 import 期硬校验
--------------------------
为了让「`.env` 不配系统 Key、由每个用户各自配个人 Key」这种部署方式可用，
缺 Key 的报错从 **import 期** 移到 **调用期**（`chat()` / `require_api_key()`）。
报错依然明确，且不含 Key 本身。
"""
import hashlib
import logging
import os
import threading
from typing import List

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from dotenv import load_dotenv

from ai.llm_context import get_user_api_key
from ai import perf as _perf   # 临时链路耗时诊断（BUG-2）

logger = logging.getLogger(__name__)

# 自动定位项目根目录 .env：ai/llm_client.py → ai/ → backend/ → 项目根/
current_file = os.path.abspath(__file__)
ai_folder = os.path.dirname(current_file)
backend_folder = os.path.dirname(ai_folder)
root_folder = os.path.dirname(backend_folder)
env_path = os.path.join(root_folder, ".env")
load_dotenv(env_path)

# 系统默认 Key（部署方在 .env 配置）。这里**不抛异常**：允许 .env 未配置、由用户各自配置个人 Key。
DEFAULT_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 没有任何可用 Key 时的明确提示（提示如何配置，不回显 Key）
NO_KEY_MESSAGE = (
    "未配置可用的 DeepSeek API Key：请在「个人信息」页配置你自己的 DeepSeek API Key，"
    "或联系部署管理员在 .env 中配置系统默认 Key（DEEPSEEK_API_KEY）。"
)


class LLMConfigError(RuntimeError):
    """没有任何可用的 DeepSeek Key（个人与系统默认都缺）时抛出；API 层会转成明确的 400 提示。"""


def resolve_api_key() -> tuple[str | None, str]:
    """解析本次调用实际使用的 Key，返回 `(key, source)`；source ∈ {user, env, none}。

    仅供内部与测试使用；**不要把返回值里的 key 输出到日志或响应**。
    """
    user_key = (get_user_api_key() or "").strip()
    if user_key:
        return user_key, "user"
    env_key = (DEFAULT_API_KEY or "").strip()
    if env_key:
        return env_key, "env"
    return None, "none"


def require_api_key() -> str:
    """取本次调用要用的 Key；个人与默认都没有时抛 `LLMConfigError`（明确提示、不含 Key）。"""
    key, _source = resolve_api_key()
    if not key:
        raise LLMConfigError(NO_KEY_MESSAGE)
    return key


# ── 按 Key 缓存 client（不改共享 singleton 的 api_key，避免并发串 Key）──
_client_lock = threading.Lock()
_clients: dict[str, OpenAI] = {}
_MAX_CACHED_CLIENTS = 128   # 容量上限，防止用户很多时无限增长（超出按插入顺序淘汰最早）


def _client_for(key: str) -> OpenAI:
    """按 Key 的**哈希**缓存 OpenAI client（保留连接池复用）。

    用哈希做字典键而非明文，避免明文 Key 长期作为字典键驻留；Key 本身仍会作为
    client 的构造参数被 SDK 持有（这是调用所必需的最小持有）。
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    with _client_lock:
        client = _clients.get(digest)
        if client is None:
            client = OpenAI(
                api_key=key,
                base_url=DEEPSEEK_BASE_URL,
                timeout=30.0,
                max_retries=0,
            )
            if len(_clients) >= _MAX_CACHED_CLIENTS:
                _clients.pop(next(iter(_clients)))
            _clients[digest] = client
        return client


class DeepSeekClient:
    """统一 LLM 客户端（进程内单例）。

    **不保存 api_key**：每次 `chat()` 都按当前上下文解析 Key，保证请求之间相互隔离。
    """

    def __init__(self):
        self.chat_model = "deepseek-chat"

    def chat(self, prompt: str, temperature: float = 0.1,
             timeout: float | None = None) -> str:
        """调用 DeepSeek。

        `timeout`：**本次调用**的超时秒数，None 表示沿用 client 默认值（30s）。
        为什么要按调用可配：同一个 client 承担"短输出"（分类/要素，秒级）与
        "长输出"（条款比对要逐条生成 30 条 clauses 的 JSON，实测 15~25s，并发下会顶到 30s），
        给后者单独放宽超时，比把全局默认一律调大更安全（短调用仍然快速失败）。
        """
        key = require_api_key()
        messages: List[ChatCompletionMessageParam] = [
            {"role": "user", "content": prompt}
        ]
        extra = {"timeout": timeout} if timeout is not None else {}
        try:
            with _perf.stage("llm"):   # 临时耗时诊断（BUG-2）：仅统计，不改语义
                response = _client_for(key).chat.completions.create(
                    model=self.chat_model,
                    messages=messages,
                    temperature=temperature,
                    **extra,
                )
            _perf.count_llm()
            return response.choices[0].message.content
        except Exception as e:
            # 只记异常摘要；绝不输出 api_key / Authorization / 完整请求头
            logger.error("DeepSeek LLM 调用失败: %s", e)
            raise


llm_client = DeepSeekClient()
