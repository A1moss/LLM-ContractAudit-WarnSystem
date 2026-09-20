"""ai.router.protocol — Task-level Expert Router 的数据协议。

定位（务必与 MoE 区分）
--------------------
本模块实现的是 **Task-level Expert Routing（任务级专家路由）**：

    Task → Expert Router → Expert（一组任务能力路径）→ Skill → 现有真实函数

**不是 Model-level Mixture-of-Experts**：不涉及任何神经网络专家、门控网络、
token 级路由，也**不切换 LLM 模型**。本阶段路由的对象是"任务能力"，不是"模型"。

命名纪律
-------
代码、API、注释一律使用 `Expert Router` / `Task-level Expert Routing`。
不使用 `Mixture of Experts` / `MoE Model` / `Neural MoE` 等表述。

Expert 与 Skill 的区别（本阶段的核心概念）
---------------------------------------
* **Skill**  = 系统"有什么能力"（12 个已注册能力，见 `ai.skills.registry`）
* **Expert** = 一组任务能力路径（**可以包含多个 Skill**）
* **Router** = 当前任务该找哪个 Expert

因此 Expert **不是** Skill 改个名：`contract_analysis_expert` 聚合了
抽取 / 规则 / 证据 / 裁决 四个 Skill，而不是某一个 Skill 的别名。

确定性
-----
本层**完全确定性**：不使用 LLM 做路由，不使用随机数、时间或外部状态。
相同 TaskDescriptor + 相同注册状态 ⇒ 相同路由结果（有测试断言）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# ── 路由状态 ──
STATUS_ROUTED = "routed"
STATUS_UNROUTABLE = "unroutable"

# ── 不可路由的原因（稳定契约，便于测试与前端区分）──
REASON_UNKNOWN_TASK_TYPE = "unknown_task_type"
REASON_NO_ENABLED_EXPERT = "no_enabled_expert"
REASON_NO_EXPERT_FOR_TASK = "no_expert_for_task_type"
REASON_DISABLED = "expert_disabled"

# ── 本阶段固定的三个 task_type（以 Skill 层真实 category 为依据）──
TASK_LEGAL_RETRIEVAL = "legal_retrieval"
TASK_CONTRACT_ANALYSIS = "contract_analysis"
TASK_CLAUSE_REVISION = "clause_revision"

KNOWN_TASK_TYPES = (
    TASK_LEGAL_RETRIEVAL,
    TASK_CONTRACT_ANALYSIS,
    TASK_CLAUSE_REVISION,
)


class RouterError(Exception):
    """路由层基类异常。"""


class TaskValidationError(RouterError):
    """TaskDescriptor 不合法（缺 task_type / 类型错）。"""


class ExpertRegistrationError(RouterError):
    """Expert 注册失败（重名 / 能力未落地为真实 Skill / 字段非法）。"""


@dataclass
class TaskDescriptor:
    """一个待路由的任务。

    最小必填只有 `task_type`；其余字段按各 Expert 的真实需要提供。

    `capability` 可选：用于在**同一个 Expert 内部**指定要用哪一项能力
    （例如 `clause_revision_expert` 里选 `draft_clause` 而非 `revise_clause`）。
    不提供时由 Expert 的 `capabilities` 顺序决定（第一项为主能力）。
    """

    task_type: str
    query: str = ""
    contract_type: str = ""
    capability: str = ""
    context: dict = field(default_factory=dict)

    REQUIRED_FIELDS = ("task_type",)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "TaskDescriptor":
        """从 JSON dict 构造并严格校验。"""
        if payload is None:
            raise TaskValidationError("Task 必须是 JSON 对象")
        if not isinstance(payload, Mapping):
            raise TaskValidationError("Task 必须是 JSON 对象")

        task_type = payload.get("task_type")
        if not isinstance(task_type, str) or not task_type.strip():
            raise TaskValidationError("字段 'task_type' 必须是非空字符串")

        for name in ("query", "contract_type", "capability"):
            value = payload.get(name)
            if value is not None and not isinstance(value, str):
                raise TaskValidationError(f"字段 {name!r} 必须是字符串或省略")

        context = payload.get("context")
        if context is None:
            context = {}
        if not isinstance(context, Mapping):
            raise TaskValidationError("字段 'context' 必须是 JSON 对象或省略")

        return cls(
            task_type=task_type.strip(),
            query=str(payload.get("query") or "").strip(),
            contract_type=str(payload.get("contract_type") or "").strip(),
            capability=str(payload.get("capability") or "").strip(),
            context=dict(context),
        )

    def to_payload(self) -> dict:
        return {
            "task_type": self.task_type,
            "query": self.query,
            "contract_type": self.contract_type,
            "capability": self.capability,
            "context": self.context,
        }


@dataclass
class ExpertDescriptor:
    """一个"专家"：一组任务能力路径。

    * `capabilities`：**Skill 名列表**（必须全部是 `ai.skills.registry` 中已注册的能力）。
      这一条由 ExpertRegistry 在注册时**强制校验** —— 不允许声明不存在的能力。
    * `task_types`：该 Expert 负责的任务类型（Router 据此匹配）。
    * `priority`：多个 Expert 命中同一任务时的确定性取舍（大者优先；相同则按注册顺序）。
    * `enabled`：禁用后 Router **不得**选中它。

    与 MoE 的区别：这里没有门控网络，也没有权重；`priority` 只是确定性的排序键。
    """

    name: str
    description: str
    capabilities: tuple[str, ...]
    task_types: tuple[str, ...]
    priority: int = 0
    enabled: bool = True
    # ── 预留位（本阶段仅作描述，不实现复杂管理）──
    version: str = "0.1.0"
    tags: tuple[str, ...] = ()
    # 若该 Expert 经 A2A Agent 落地，可填 Agent 名（本阶段仅 law 路线用到）
    agent: str = ""

    def supports_task(self, task_type: str) -> bool:
        return task_type in self.task_types

    def primary_capability(self, requested: str = "") -> str:
        """选定要执行的能力：显式请求优先（且必须属于本 Expert），否则取第一项。"""
        if requested and requested in self.capabilities:
            return requested
        return self.capabilities[0] if self.capabilities else ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "task_types": list(self.task_types),
            "priority": self.priority,
            "enabled": self.enabled,
            "version": self.version,
            "tags": list(self.tags),
            "agent": self.agent,
        }


@dataclass
class RouteDecision:
    """一次路由的结果（**只描述路由，不携带业务结果**）。"""

    status: str
    task_type: str
    expert: str = ""
    capability: str = ""
    capabilities: tuple[str, ...] = ()
    reason: str = ""
    detail: str = ""
    priority: int = 0
    candidates: tuple[str, ...] = ()
    """所有命中该任务的 Expert 名（按取舍顺序），用于证明"多命中时按 priority 取舍"。"""

    def to_dict(self) -> dict:
        payload = {
            "status": self.status,
            "task_type": self.task_type,
            "expert": self.expert,
            "capability": self.capability,
            "capabilities": list(self.capabilities),
            "priority": self.priority,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.detail:
            payload["detail"] = self.detail
        if self.candidates:
            payload["candidates"] = list(self.candidates)
        return payload


__all__ = [
    "KNOWN_TASK_TYPES",
    "REASON_DISABLED",
    "REASON_NO_ENABLED_EXPERT",
    "REASON_NO_EXPERT_FOR_TASK",
    "REASON_UNKNOWN_TASK_TYPE",
    "STATUS_ROUTED",
    "STATUS_UNROUTABLE",
    "TASK_CLAUSE_REVISION",
    "TASK_CONTRACT_ANALYSIS",
    "TASK_LEGAL_RETRIEVAL",
    "ExpertDescriptor",
    "ExpertRegistrationError",
    "RouteDecision",
    "RouterError",
    "TaskDescriptor",
    "TaskValidationError",
]
