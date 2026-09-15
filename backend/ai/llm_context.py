"""ai.llm_context — 把「本次调用该用哪个 DeepSeek Key」绑定到当前请求上下文。

优先级：**用户个人 Key > .env 的 `DEEPSEEK_API_KEY`（系统默认 Key）**

* 用户配了个人 Key            → 用个人 Key
* 用户没配个人 Key            → 用 .env 系统默认 Key
* 两者都没有                  → 由 `llm_client` 抛出明确配置错误（绝不发送空 Key）

为什么用 ContextVar 而不是给业务模块加参数
------------------------------------------
所有 LLM 调用统一走 `llm_client.chat(...)`（8 处调用点签名完全一致），因此**无需修改
classifier / extractor / evidence_extractor / evidence_adjudicator /
recommendation_engine / matcher / reviser 的任何调用签名**：
请求入口绑定 Key → `llm_client` 调用时解析 → 业务层零改动。

传播边界（已用探针在 FastAPI 上实测，勿凭直觉改）
------------------------------------------------
* 在**中间件**里 `set` → 同步端点 ✅ / 依赖 ✅ / 后台任务(BackgroundTasks) ✅ 都能读到
* 在**依赖**里 `set` → **无效** ❌（依赖运行在线程池线程，改不到端点的上下文）→ 所以绑定必须放中间件
* `ThreadPoolExecutor` 新建的线程**不继承** contextvars ❌
  → 提交任务必须用本模块的 `submit_with_context()`，否则工作线程读不到用户 Key，
    会静默回退到 .env 默认 Key（= 个人 Key 失效）

并发安全：ContextVar 是「每个执行上下文一份」的，天然按请求隔离，
不存在「A 请求改了全局变量、B 请求读到 A 的 Key」的串 Key 风险。
"""
from contextvars import ContextVar, copy_context
from typing import Any, Callable

# 当前上下文的用户个人 Key；None 表示「该用户没有个人 Key」→ 回退 .env 默认 Key
_user_api_key: ContextVar[str | None] = ContextVar("a24_user_deepseek_key", default=None)


def set_user_api_key(key: str | None):
    """绑定当前上下文的用户个人 Key，返回 token 供 `reset_user_api_key` 复原。"""
    return _user_api_key.set((key or "").strip() or None)


def reset_user_api_key(token) -> None:
    """复原上下文（请求结束/后台任务结束时调用），避免污染后续执行。"""
    _user_api_key.reset(token)


def get_user_api_key() -> str | None:
    """读取当前上下文的用户个人 Key；没有则 None。"""
    return _user_api_key.get()


def submit_with_context(executor, fn: Callable[..., Any], /, *args, **kwargs):
    """把**当前上下文复制**到工作线程后再提交任务。

    `ThreadPoolExecutor` 的新线程不会继承 contextvars，直接 `executor.submit(fn, ...)`
    会导致线程内读不到用户 Key（个人 Key 失效、退回默认 Key），故必须经此函数提交。
    """
    ctx = copy_context()
    return executor.submit(ctx.run, fn, *args, **kwargs)
