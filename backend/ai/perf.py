"""ai.perf — 临时链路耗时诊断（BUG-2 定位用）。

设计边界（**只做观测，不改业务**）：
* 不改变任何函数的返回值、异常语义、调用顺序；
* 未启用时（`A24_PERF` 未设置）`stage()` 只做一次环境变量判断后直接 yield，开销可忽略；
* 启用时（`A24_PERF=1`）按阶段累计耗时，并在一次链路结束时打印：

      [PERF] upload_save       0.3s
      [PERF] document_parse    1.8s
      ...
      [PERF] TOTAL            44.9s

用法：
    with stage("document_parse"):
        ...

嵌套/并行安全：内部按 `(线程, 上下文标签)` 各自持有一份账本，跨线程只做加锁累加，
不做任何共享可变状态的读写顺序依赖。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger("a24.perf")

_ENV_FLAG = "A24_PERF"

# 当前链路的账本标签（ContextVar 在 ThreadPoolExecutor 里不继承，故用线程局部 + 显式 label）
_state = threading.local()
_lock = threading.Lock()

# 账本：{label: {stage: seconds}}
_ledgers: dict[str, dict[str, float]] = {}
# 链路起点：{label: monotonic}
_started: dict[str, float] = {}
# 账本数量上限（长驻进程开启诊断时防止无限增长）
_MAX_LEDGERS = 64


def enabled() -> bool:
    """是否启用耗时诊断。每次读取环境变量（便于运行期开关），失败按关闭处理。"""
    try:
        return os.getenv(_ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return False


def _label() -> str:
    return getattr(_state, "label", "default")


def reset(label: str) -> None:
    """开始一条新链路（清空该 label 的账本并记录起点）。"""
    with _lock:
        _ledgers[label] = {}
        _started[label] = time.perf_counter()
        # 容量兜底：长驻进程开启诊断时，按 label 逐条累积会缓慢增长；超过上限先丢最早的
        while len(_ledgers) > _MAX_LEDGERS:
            oldest = next(iter(_ledgers))
            if oldest == label:
                break
            _ledgers.pop(oldest, None)
            _started.pop(oldest, None)
    _state.label = label


def apply_label_to_current_thread(label: str) -> None:
    """把当前线程挂到指定账本（供 ThreadPoolExecutor 工作线程使用）。"""
    _state.label = label


def _labeled_call(label: str, fn, args: tuple, kwargs: dict):
    """工作线程入口：先把自己挂到提交时刻的账本，再执行原函数（原样透传返回值/异常）。"""
    _state.label = label
    return fn(*args, **kwargs)


def submit_labeled(executor, fn, /, *args, **kwargs):
    """提交到线程池，并让工作线程把耗时/LLM 计数记到**提交方线程的账本**上。

    `ThreadPoolExecutor` 的工作线程既拿不到 contextvars，也拿不到线程局部变量，
    故由提交方把当时的 label 直接绑定进任务（不经过共享可变状态）。
    未启用诊断时行为与 `executor.submit(fn, ...)` 完全等价。
    """
    if not enabled():
        return executor.submit(fn, *args, **kwargs)
    return executor.submit(_labeled_call, _label(), fn, args, kwargs)


def _labeled_ctx_call(label: str, ctx, fn, args: tuple, kwargs: dict):
    """工作线程入口：挂账本 + 用复制过来的 contextvars 上下文执行原函数。"""
    _state.label = label
    return ctx.run(fn, *args, **kwargs)


def submit_labeled_ctx(executor, fn, /, *args, **kwargs):
    """`ai.llm_context.submit_with_context` 的**带诊断账本**版本。

    语义与 `submit_with_context(executor, fn, ...)` 完全一致（复制当前 contextvars），
    只是额外把提交时刻的账本 label 绑定进工作线程，使 LLM 计数与阶段耗时归到同一账本。
    未启用诊断时直接走原路径。
    """
    if not enabled():
        from ai.llm_context import submit_with_context
        return submit_with_context(executor, fn, *args, **kwargs)
    from contextvars import copy_context
    return executor.submit(_labeled_ctx_call, _label(), copy_context(), fn, args, kwargs)


def add(stage_name: str, seconds: float) -> None:
    """累加某阶段耗时（线程安全；同阶段多次执行会累加）。"""
    label = _label()
    with _lock:
        bucket = _ledgers.setdefault(label, {})
        bucket[stage_name] = bucket.get(stage_name, 0.0) + seconds


@contextmanager
def stage(stage_name: str):
    """统计一段代码的耗时；异常照常抛出（不影响业务语义）。"""
    if not enabled():
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        add(stage_name, time.perf_counter() - t0)


def report(label: str | None = None) -> dict:
    """打印并返回该链路的阶段耗时明细（含 TOTAL 与 LLM 次数）。"""
    lab = label or _label()
    with _lock:
        bucket = dict(_ledgers.get(lab, {}))
        t0 = _started.get(lab)
    if not bucket and t0 is None:
        return {}
    total = round(time.perf_counter() - t0, 2) if t0 is not None else None

    lines = []
    for name in sorted(bucket):
        lines.append(f"[PERF] {name:<18s} {bucket[name]:.1f}s")
    llm_calls = _counts.get(lab, 0)
    if llm_calls:
        lines.append(f"[PERF] {'llm_calls':<18s} {llm_calls}")
    if total is not None:
        lines.append(f"[PERF] {'TOTAL':<18s} {total:.1f}s")
    text = "\n".join(lines)
    if text:
        logger.info("链路耗时诊断（%s）:\n%s", lab, text)
    return {"stages": bucket, "llm_calls": llm_calls, "total": total, "text": text}


# ── LLM 调用计数（"一次审核到底调了几次 LLM"）───────────────────────────
_counts: dict[str, int] = {}


def count_llm() -> None:
    """记录一次 LLM 调用。"""
    label = _label()
    with _lock:
        _counts[label] = _counts.get(label, 0) + 1
