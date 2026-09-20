"""ai.router — Task-level Expert Router（任务级专家路由）。

四层旁路创新架构的最上层
----------------------
    Skill Registry  → 能力标准化（12 个真实 Skill）
          ↑
    A2A             → Agent 间标准化任务通信
          ↑
    Multi-Agent     → 基于中间结果的决策与协作回路
          ↑
    Expert Router   → 按任务类型选择专家能力路径        ← 本层

本阶段完成了什么
--------------
    Task ──▶ Expert Router ──▶ Expert ──▶ Skill Registry ──▶ 现有真实函数

* 3 个 Expert（各含 2~4 项真实 Skill，**不是** Skill 改名）；
* 确定性路由：相同任务 + 相同注册状态 ⇒ 相同结果；
* 未知任务 → `unroutable`（**NO_MATCH**），绝不"随便选一个"；
* 禁用 Expert 不可被选中；多命中时按 `priority` 确定性取舍；
* 真实执行经 **Skill Registry**，不直接调业务函数。

本阶段**没有**做什么（务必与 MoE 区分）
------------------------------------
* **不是 Model-level Mixture-of-Experts**：没有神经网络专家、没有门控网络、
  没有 token 级路由，**也没有切换任何 LLM 模型**（`ai/llm_client.py` 完全未动）。
* 本阶段路由的是**任务能力**，不是模型。
* 未接入审核主链路：`_run_audit` / `api/contracts.py` 完全不依赖本层。
"""
from ai.router.protocol import (  # noqa: F401
    KNOWN_TASK_TYPES,
    REASON_DISABLED,
    REASON_NO_ENABLED_EXPERT,
    REASON_NO_EXPERT_FOR_TASK,
    REASON_UNKNOWN_TASK_TYPE,
    STATUS_ROUTED,
    STATUS_UNROUTABLE,
    TASK_CLAUSE_REVISION,
    TASK_CONTRACT_ANALYSIS,
    TASK_LEGAL_RETRIEVAL,
    ExpertDescriptor,
    ExpertRegistrationError,
    RouteDecision,
    RouterError,
    TaskDescriptor,
    TaskValidationError,
)
from ai.router.registry import (  # noqa: F401
    ExpertRegistry,
    expert_registry,
    register_experts,
)
from ai.router.router import ExpertRouter  # noqa: F401

# 触发声明式登记（3 个 Expert；能力真实性由 ExpertRegistry 校验）
register_experts()

# 进程内默认路由器（无状态，可复用）
router = ExpertRouter()

__all__ = [
    "ExpertDescriptor",
    "ExpertRegistrationError",
    "ExpertRegistry",
    "ExpertRouter",
    "KNOWN_TASK_TYPES",
    "REASON_DISABLED",
    "REASON_NO_ENABLED_EXPERT",
    "REASON_NO_EXPERT_FOR_TASK",
    "REASON_UNKNOWN_TASK_TYPE",
    "RouteDecision",
    "RouterError",
    "STATUS_ROUTED",
    "STATUS_UNROUTABLE",
    "TASK_CLAUSE_REVISION",
    "TASK_CONTRACT_ANALYSIS",
    "TASK_LEGAL_RETRIEVAL",
    "TaskDescriptor",
    "TaskValidationError",
    "expert_registry",
    "register_experts",
    "router",
]
