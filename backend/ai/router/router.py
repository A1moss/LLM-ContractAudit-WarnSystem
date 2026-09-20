"""ai.router.router — Task-level Expert Router（确定性路由）。

它做什么
-------
把 **TaskDescriptor** 映射为一个 **RouteDecision**（选中哪个 Expert、用哪项能力）：

    Task ──▶ Expert Router ──▶ RouteDecision(expert, capability, ...)
                                    │
                                    ▼
                            （执行在 api/router.py 完成）
                            Expert → Skill Registry → 现有真实函数

它**不**做什么（边界）
--------------------
* 不执行业务：本模块**不**调用任何 Skill、不 import `ai.rag` / `ai.auditor` /
  `ai.reviser` / `ai.matcher`。执行统一在 `api/router.py` 通过 **Skill Registry** 完成，
  从而保证 `Router → Expert → Skill → 业务函数` 这条单向链。
* **不用 LLM**：本阶段 Router 必须确定性、可解释、可测试、不依赖 API Key。
* 不做模型级路由：**不切换任何 LLM 模型**（`llm_client.py` 完全未动）。
* 不做复杂评分：多命中时只用 `priority` + 注册顺序做确定性取舍。

路由规则（完全声明式）
--------------------
1. `task_type` 不在本阶段已知集合内 → `unroutable / unknown_task_type`（**NO_MATCH**，
   绝不"随便选一个 Expert"）。
2. 取该 `task_type` 下**已启用**的 Expert，按 `(-priority, 注册顺序)` 排序。
3. 无已启用候选 → `unroutable / no_enabled_expert`。
4. 取排序后的第一个；能力 = `task.capability`（若属于该 Expert）否则第一项能力。

确定性保证：上述排序键不含随机数、时间或外部状态。
"""
from __future__ import annotations

import logging

from ai.router.protocol import (
    KNOWN_TASK_TYPES,
    REASON_DISABLED,
    REASON_NO_ENABLED_EXPERT,
    REASON_NO_EXPERT_FOR_TASK,
    REASON_UNKNOWN_TASK_TYPE,
    STATUS_ROUTED,
    STATUS_UNROUTABLE,
    ExpertDescriptor,
    RouteDecision,
    TaskDescriptor,
)
from ai.router.registry import ExpertRegistry, expert_registry

logger = logging.getLogger(__name__)


class ExpertRouter:
    """任务级专家路由器（无状态、确定性）。"""

    def __init__(self, registry: ExpertRegistry | None = None):
        self._registry = registry or expert_registry

    # ── 对外唯一入口 ──
    def route(self, task: TaskDescriptor) -> RouteDecision:
        """为一个任务选出 Expert 与能力。**只返回路由决策，不执行业务。**"""

        # 规则 1：未知任务 → NO_MATCH（不猜、不兜底）
        if task.task_type not in KNOWN_TASK_TYPES:
            return RouteDecision(
                status=STATUS_UNROUTABLE,
                task_type=task.task_type,
                reason=REASON_UNKNOWN_TASK_TYPE,
                detail=(
                    f"没有匹配的 Expert：task_type={task.task_type!r} 不是已知任务类型"
                    f"（已知：{list(KNOWN_TASK_TYPES)}）"
                ),
            )

        # 规则 2：取已启用候选，按确定性顺序排序
        enabled = self._registry.candidates_for(task.task_type, enabled_only=True)
        if enabled:
            chosen = enabled[0]
            return self._routed(task, chosen, candidates=enabled)

        # 规则 3：该任务有 Expert 但全部被禁用 → 明确区分"被禁用"与"不存在"
        all_for_task = self._registry.candidates_for(task.task_type, enabled_only=False)
        if all_for_task:
            names = [e.name for e in all_for_task]
            logger.info("Router: task_type=%s 的 Expert 全部被禁用 %s", task.task_type, names)
            return RouteDecision(
                status=STATUS_UNROUTABLE,
                task_type=task.task_type,
                reason=REASON_DISABLED,
                detail=f"task_type={task.task_type!r} 的 Expert 均处于禁用状态：{names}",
                candidates=tuple(names),
            )

        # 规则 4：理论上不可达（KNOWN_TASK_TYPES 都有登记），防御性返回
        return RouteDecision(
            status=STATUS_UNROUTABLE,
            task_type=task.task_type,
            reason=REASON_NO_EXPERT_FOR_TASK,
            detail=f"没有为 task_type={task.task_type!r} 注册任何 Expert",
        )

    # ── 内部 ──
    def _routed(self, task: TaskDescriptor, chosen: ExpertDescriptor,
                *, candidates: list[ExpertDescriptor]) -> RouteDecision:
        capability = chosen.primary_capability(task.capability)
        logger.info(
            "Router: task_type=%s → expert=%s capability=%s (candidates=%s)",
            task.task_type, chosen.name, capability, [e.name for e in candidates],
        )
        decision = RouteDecision(
            status=STATUS_ROUTED,
            task_type=task.task_type,
            expert=chosen.name,
            capability=capability,
            capabilities=tuple(chosen.capabilities),
            priority=chosen.priority,
            candidates=tuple(e.name for e in candidates),
        )
        # 显式请求了能力，但该 Expert 不含它：如实标注，不静默忽略
        if task.capability and task.capability not in chosen.capabilities:
            decision.detail = (
                f"请求的能力 {task.capability!r} 不属于 {chosen.name}，"
                f"已回退到该专家的主能力 {capability!r}"
            )
        return decision

    # ── 辅助 ──
    def experts(self, *, enabled_only: bool = False,
                task_type: str | None = None) -> list[ExpertDescriptor]:
        return self._registry.list_experts(enabled_only=enabled_only, task_type=task_type)

    def describe_experts(self, *, enabled_only: bool = False,
                         task_type: str | None = None) -> list[dict]:
        return self._registry.describe(enabled_only=enabled_only, task_type=task_type)


__all__ = ["ExpertRouter"]
