"""ai.a2a.agents — 两个最小 Agent（AuditAgent / LawAgent）。

本阶段事实边界（不得夸大）
------------------------
这里**不是** Multi-Agent 系统。两个 Agent 都是最小的：

    Agent Identity（AgentCard）
    + Capability（= 已注册 Skill 名）
    + Task Handler（收到 A2ATask → 调 Skill Registry → 返回 A2AResult）

**没有** autonomous planner、memory、self-reflection、long-running worker、
复杂状态机，也没有 `class SuperAgent` 这类"内部其实只是直接调函数"的假封装。

两个 Agent 的真实分工
--------------------
* `AuditAgent`（客户端侧）：提出一个法规检索任务，通过**真实 HTTP** 把 `A2ATask`
  发给 `LawAgent`，并解析回传的 `A2AResult`。
* `LawAgent`（服务端侧）：接收 `A2ATask`，按其 `capability` 调 **Skill Registry**，
  把 Skill 的真实返回值包成 `A2AResult` 返回。

调用链（A2A 负责通信，Skill 负责能力，两者不重叠）：

    AuditAgent ──HTTP POST A2ATask──▶ LawAgent ──skill_registry.invoke──▶ retrieve_knowledge
                                                                              │
                                                              现有真实函数 search_knowledge
                                                                              │
    AuditAgent ◀──HTTP 200 A2AResult──────────────────────────────────────────┘

安全边界
-------
Agent 不自行放行权限：`SkillRegistry.invoke` 之前先按 Skill 自己声明的 `permissions`
做角色校验（与 `api/skills.py` 的口径完全一致），禁用 Skill 一律拒绝。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Protocol

from ai.a2a.protocol import (
    ERR_SKILL_DISABLED,
    ERR_SKILL_ERROR,
    ERR_UNAUTHORIZED,
    ERR_UNKNOWN_CAPABILITY,
    A2AResult,
    A2ATask,
    AgentCard,
    new_id,
)
from ai.skills import registry as skill_registry
from ai.skills.protocol import (
    SkillDisabledError,
    SkillInputError,
    SkillNotFoundError,
    SkillPermissionError,
)

logger = logging.getLogger(__name__)

# ── 能力名（必须与已注册 Skill 名一致，注册时由 AgentRegistry 强制校验）──
CAP_RETRIEVE_KNOWLEDGE = "retrieve_knowledge"
CAP_REQUEST_LAW_RETRIEVAL = "request_law_retrieval"
CAP_REVIEW_RETRIEVAL = "review_retrieval"

# ── Agent 名（与 registry.py 的常量保持一致，避免散落字符串）──
AUDIT_AGENT_NAME = "audit_agent"
LAW_AGENT_NAME = "law_agent"
REVIEWER_AGENT_NAME = "reviewer_agent"

ADMIN_ROLE = "admin"


def check_skill_permission(skill, role: str) -> None:
    """与 `api/skills.py::_require_skill_permission` **完全同一口径**的角色校验。

    放在 A2A 层是为了让 LawAgent 无论是被 HTTP 调用还是被直接调用，
    权限语义都一致（admin 恒通过；声明为空则仅需登录）。
    """
    allowed = tuple(skill.permissions or ())
    if not allowed:
        return
    if role == ADMIN_ROLE:
        return
    if role not in allowed:
        raise SkillPermissionError(
            f"当前角色（{role}）无权调用 Skill {skill.name!r}"
        )


class A2AUser(Protocol):
    """A2A 调用者上下文：**只用于权限判断**，不构成 Agent 记忆或状态机。"""

    role: str


# ══════════════════════════════════════════════════════════════════════
# 服务端 Agent
# ══════════════════════════════════════════════════════════════════════

class Agent:
    """最小 Agent 基类：身份（AgentCard）+ 收任务 → 返回 A2AResult。

    刻意极薄：没有 planner、没有 memory、没有自我反思、没有循环。
    子类只实现 `handle_task`。
    """

    card: AgentCard

    def identity(self) -> AgentCard:
        return self.card

    def can(self, capability: str) -> bool:
        return capability in self.card.capabilities

    def handle_task(self, task: A2ATask, user: A2AUser) -> A2AResult:
        """处理一个 A2ATask。子类必须实现。"""
        raise NotImplementedError


class LawAgent(Agent):
    """合同法规检索 Agent。

    能力只有 `retrieve_knowledge`（= 已注册的真实 Skill）。
    它**不实现检索逻辑**：只把任务转成 Skill 调用，由 Skill 适配器
    转发到现有真实函数 `ai.rag.vector_store.search_knowledge`。
    """

    card = AgentCard(
        name="law_agent",
        description="合同法规检索 Agent：接收 A2A 任务，调用 retrieve_knowledge Skill 返回法条/标准条款检索结果。",
        capabilities=(CAP_RETRIEVE_KNOWLEDGE,),
        implementation="skill",
        kind="skill-backed",
        transport="http",
        endpoint="/api/a2a/tasks",
    )

    def handle_task(self, task: A2ATask, user: A2AUser) -> A2AResult:
        # 1) capability 白名单（只认自己声明的能力，杜绝任意函数调用）
        if not self.can(task.capability):
            return A2AResult.failed(
                task,
                code=ERR_UNKNOWN_CAPABILITY,
                message=f"LawAgent 不支持能力 {task.capability!r}",
                from_agent=self.card.name,
            )

        # 2) Skill 必须已注册（capability 只允许是 Skill 名，绝不是可 import 的路径）
        try:
            skill = skill_registry.get(task.capability)
        except SkillNotFoundError:
            return A2AResult.failed(
                task,
                code=ERR_UNKNOWN_CAPABILITY,
                message=f"能力 {task.capability!r} 未在 Skill Registry 注册",
                from_agent=self.card.name,
            )

        # 3) 权限（与 Skill API 同口径）
        try:
            check_skill_permission(skill, user.role)
        except SkillPermissionError as exc:
            return A2AResult.rejected(
                task,
                code=ERR_UNAUTHORIZED,
                message=str(exc),
                from_agent=self.card.name,
            )

        # 4) 禁用 Skill 一律拒绝
        if not skill.enabled:
            return A2AResult.failed(
                task,
                code=ERR_SKILL_DISABLED,
                message=f"Skill {skill.name!r} 已注册但处于禁用状态",
                from_agent=self.card.name,
            )

        # 5) 真实调用 Skill（输入校验 → 适配器 → 现有真实函数）
        try:
            items = skill_registry.invoke(task.capability, task.input)
        except SkillInputError as exc:
            return A2AResult.failed(
                task, code=ERR_SKILL_ERROR, message=f"输入不合法：{exc}", from_agent=self.card.name
            )
        except Exception as exc:  # noqa: BLE001
            # 只回可读摘要，绝不回传 traceback
            logger.exception("A2A: Skill 执行失败 capability=%s", task.capability)
            return A2AResult.failed(
                task,
                code=ERR_SKILL_ERROR,
                message=f"Skill {task.capability!r} 执行失败：{type(exc).__name__}",
                from_agent=self.card.name,
            )

        # 6) 包成 A2AResult —— 保留 Skill 的原始返回，不二次加工
        return A2AResult.completed(
            task,
            {
                "skill": skill.name,
                "items": items if isinstance(items, list) else [],
                "count": len(items) if isinstance(items, list) else 0,
            },
            from_agent=self.card.name,
            skill=skill.name,
            skill_source=skill.source,
        )


# ══════════════════════════════════════════════════════════════════════
# 决策型 Agent（本阶段新增：真正的决策点）
# ══════════════════════════════════════════════════════════════════════

# 决策集合（结构化，供 Orchestrator 当作控制流输入）
DECISION_APPROVE = "approve"
DECISION_REVISE = "revise"
DECISION_REJECT = "reject"
DECISIONS = (DECISION_APPROVE, DECISION_REVISE, DECISION_REJECT)


def decide_on_retrieval(law_result: A2AResult | None) -> dict:
    """**真实决策逻辑**（确定性，可测试、可复现）。

    输入是 LawAgent 的**真实中间结果**，输出是结构化决策：

    * 依赖结果是**成功**（`status == completed`）且至少一条**有效**法规依据
      （`content` 非空，且 `law` / `article` / `title` 中至少一项非空）
      → `approve`
    * 否则 → `revise`（要求回退到 LawAgent 重试）

    刻意先做成确定性函数而不是再引入一个 LLM：
    "决策真实影响了控制流"这件事必须**可复现、可断言**。
    将来接更复杂的 LLM Reviewer 时，只需替换这个函数的实现，
    Agent Card / A2A 协议 / Orchestrator 都不用改。
    """
    if law_result is None:
        return {"decision": DECISION_REVISE, "reason": "未收到法规检索结果"}

    if law_result.status != "completed":
        code = (law_result.error or {}).get("code") or "UNKNOWN"
        return {
            "decision": DECISION_REVISE,
            "reason": f"法规检索未成功（{code}）",
        }

    items = ((law_result.result or {}).get("items")) or []
    if not isinstance(items, list):
        items = []

    valid = [
        it for it in items
        if isinstance(it, dict)
        and str(it.get("content") or "").strip()
        and any(str(it.get(k) or "").strip() for k in ("law", "article", "title"))
    ]
    if valid:
        return {
            "decision": DECISION_APPROVE,
            "reason": f"已获得 {len(valid)} 条有效法规依据",
        }
    return {
        "decision": DECISION_REVISE,
        "reason": f"法规依据不足（返回 {len(items)} 条，其中有效 0 条）",
    }


class ReviewerAgent(Agent):
    """中间结果复核 Agent —— **本阶段真正的决策点**。

    它不是一个普通工具函数：它有 Agent Card、身份、capability、结构化输入与输出。
    它的输出（`decision`）会被 Orchestrator 当作**控制流输入**：
    `approve` → 结束；`revise` → 回退到 LawAgent 再执行一次。

    它**不做**法规检索（那是 LawAgent 的职责），也**不调用任何 Skill**
    —— 它是"决策型"能力，因此 `implementation="method"`（真实性由 AgentRegistry
    用 `hasattr` 校验）。
    """

    card = AgentCard(
        name="reviewer_agent",
        description="中间结果复核 Agent：对 LawAgent 返回的法规依据做有效性检查，输出 approve/revise 结构化决策。",
        capabilities=(CAP_REVIEW_RETRIEVAL,),
        implementation="method",
        kind="decision-maker",
        transport="http",
        endpoint="/api/a2a/tasks",
    )

    def review_retrieval(self, law_result: A2AResult | None) -> dict:
        """能力本体：`review_retrieval`。

        方法名与 `AgentCard.capabilities` 中的 `review_retrieval` **逐字一致**，
        因此 AgentRegistry 能用 `hasattr` 校验这项能力**真实存在**
        （与 `AuditAgent.request_law_retrieval` 同一套真实性约束）。
        """
        return decide_on_retrieval(law_result)

    def handle_task(self, task: A2ATask, user: A2AUser) -> A2AResult:
        # 1) capability 白名单
        if not self.can(task.capability):
            return A2AResult.failed(
                task,
                code=ERR_UNKNOWN_CAPABILITY,
                message=f"ReviewerAgent 不支持能力 {task.capability!r}",
                from_agent=self.card.name,
            )

        # 2) 解析输入的中间结果（上一跳 LawAgent 的 A2AResult payload）
        raw_result = task.input.get("law_result")
        law_result = None
        if isinstance(raw_result, Mapping):
            law_result = parse_result_payload(raw_result)
        elif raw_result is not None:
            return A2AResult.failed(
                task,
                code=ERR_SKILL_ERROR,
                message="输入 law_result 必须是上一跳 A2AResult 对象或省略",
                from_agent=self.card.name,
            )

        # 3) 真实决策（走能力本体方法，而不是另写一份逻辑）
        outcome = self.review_retrieval(law_result)
        decision = outcome["decision"]
        next_agent = LAW_AGENT_NAME if decision == DECISION_REVISE else None

        return A2AResult.completed(
            task,
            {
                "agent": self.card.name,
                "decision": decision,
                "reason": outcome["reason"],
                "next_agent": next_agent,
                "attempt": task.input.get("attempt"),
            },
            from_agent=self.card.name,
            skill="",
            skill_source="",
        )


# ══════════════════════════════════════════════════════════════════════
# 客户端 Agent
# ══════════════════════════════════════════════════════════════════════

class A2ATransport(Protocol):
    """把 A2ATask 送到对端 Agent 的传输层。"""

    def post_task(self, task: A2ATask, *, bearer_token: str | None = None) -> dict:
        """返回对端响应的 JSON dict（A2AResult payload）。"""
        ...


class AsyncA2ATransport(A2ATransport, Protocol):
    """额外支持异步投递的传输层（自请求场景需要，见 transport.py）。"""

    async def post_task_async(self, task: A2ATask, *, bearer_token: str | None = None) -> dict:
        ...


class AuditAgent:
    """合同审核任务 Agent（客户端侧）。

    本阶段它**只做一件事**：提出法规检索任务，经真实 HTTP 交给 LawAgent，
    并解析回传的 A2AResult。它不自己做审核编排，也不选择下一个 Agent
    （那属于下一阶段 Multi-Agent）。

    `capabilities` 刻意不是 Skill 名：`request_law_retrieval` 是**Agent 侧的动作**，
    它由 `AuditAgent.request_law_retrieval()` 这个真实方法实现，
    所以 AgentCard 声明的仍是"真实存在的能力"。
    """

    card = AgentCard(
        name="audit_agent",
        description="合同审核任务 Agent（客户端侧）：提出法规检索任务，通过真实 HTTP 调用 LawAgent 的 retrieve_knowledge 能力。",
        capabilities=(CAP_REQUEST_LAW_RETRIEVAL,),
        implementation="method",
        kind="a2a-client",
        transport="http",
        endpoint="(client) → /api/a2a/tasks",
    )

    def __init__(self, transport: A2ATransport):
        self._transport = transport

    def identity(self) -> AgentCard:
        return self.card

    def _build_task(
        self,
        query: str,
        *,
        top_k: int,
        collection_name: str | None,
        to_agent: str,
        trace_id: str | None,
    ) -> A2ATask:
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if collection_name:
            payload["collection_name"] = collection_name
        return A2ATask(
            to_agent=to_agent,
            capability=CAP_RETRIEVE_KNOWLEDGE,
            input=payload,
            from_agent=self.card.name,
            trace_id=trace_id or new_id(),
        )

    def request_law_retrieval(
        self,
        query: str,
        *,
        top_k: int = 3,
        collection_name: str | None = None,
        bearer_token: str | None = None,
        to_agent: str = "law_agent",
        trace_id: str | None = None,
    ) -> A2AResult:
        """构造 A2ATask → 经 HTTP 发给 LawAgent → 解析 A2AResult（同步）。"""
        task = self._build_task(query, top_k=top_k, collection_name=collection_name,
                                to_agent=to_agent, trace_id=trace_id)
        raw = self._transport.post_task(task, bearer_token=bearer_token)
        return parse_result_payload(raw)

    async def request_law_retrieval_async(
        self,
        query: str,
        *,
        top_k: int = 3,
        collection_name: str | None = None,
        bearer_token: str | None = None,
        to_agent: str = "law_agent",
        trace_id: str | None = None,
    ) -> A2AResult:
        """同上，但走**异步** HTTP。

        用于"本服务自身的 async 端点内部反向请求本服务"这一跳：
        阻塞式 HTTP 会占住事件循环导致自请求死锁，必须 await。
        """
        task = self._build_task(query, top_k=top_k, collection_name=collection_name,
                                to_agent=to_agent, trace_id=trace_id)
        post_async = getattr(self._transport, "post_task_async", None)
        if post_async is None:
            # 传输层不支持异步时，退回同步实现（行为一致，只是可能阻塞事件循环）
            post_async = self._transport.post_task
        raw = await post_async(task, bearer_token=bearer_token)
        return parse_result_payload(raw)


    def request(
        self,
        capability: str,
        payload: dict,
        *,
        to_agent: str,
        bearer_token: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
    ) -> A2AResult:
        """**通用**发起一次 A2A 任务（本阶段新增，供 Multi-Agent Orchestrator 复用）。

        与 `request_law_retrieval` 的区别：capability / to_agent 由调用方指定，
        因此同一个发起方既能问 `law_agent`，也能问 `reviewer_agent`。
        仍然只走 A2A，不直接调用任何 Skill 或业务函数。
        """
        task = A2ATask(
            to_agent=to_agent,
            capability=capability,
            input=payload,
            from_agent=self.card.name,
            trace_id=trace_id or new_id(),
            task_id=task_id or new_id(),
        )
        raw = self._transport.post_task(task, bearer_token=bearer_token)
        return parse_result_payload(raw)


# ══════════════════════════════════════════════════════════════════════
# 反序列化
# ══════════════════════════════════════════════════════════════════════

def parse_result_payload(raw: Mapping[str, Any]) -> A2AResult:
    """把对端返回的 JSON dict 还原成 A2AResult（严格按协议字段读取）。"""
    if not isinstance(raw, Mapping):
        raise ValueError("A2A 响应不是 JSON 对象")
    status = str(raw.get("status") or "")
    return A2AResult(
        task_id=str(raw.get("task_id") or ""),
        from_agent=str(raw.get("from_agent") or ""),
        to_agent=str(raw.get("to_agent") or ""),
        status=status,
        trace_id=str(raw.get("trace_id") or ""),
        result=raw.get("result") if isinstance(raw.get("result"), Mapping) else None,
        error=raw.get("error") if isinstance(raw.get("error"), Mapping) else None,
        schema_version=str(raw.get("schema_version") or ""),
        completed_at=str(raw.get("completed_at") or ""),
        capability=str(raw.get("capability") or ""),
        skill=str(raw.get("skill") or ""),
        skill_source=str(raw.get("skill_source") or ""),
    )


# ── 服务端按 Agent 名的分派表（声明式；无 if/else）──
# 只有"能接收 A2A 任务"的 Agent 在这里；audit_agent 是客户端侧，故意不在表内。
SERVER_AGENTS: dict[str, Callable[[], Agent]] = {
    LawAgent.card.name: LawAgent,
    ReviewerAgent.card.name: ReviewerAgent,
}


def server_agent(name: str) -> Agent | None:
    """按名取"服务端可接收任务"的 Agent 实例（None = 不是服务端 Agent）。"""
    factory = SERVER_AGENTS.get(name)
    return factory() if factory else None


def register_agents() -> None:
    """把三个 Agent Card 登记进 Agent Registry。

    * `law_agent`：能力落在 **Skill** 上 → Registry 校验"必须是已注册 Skill"；
    * `audit_agent`：客户端侧方法 → Registry 用 `hasattr` 校验方法真实存在；
    * `reviewer_agent`：决策型方法 → 同样由 `hasattr` 校验（它**不调 Skill**）。
    """
    from ai.a2a.registry import agent_registry

    agent_registry.register(AuditAgent.card, owner=AuditAgent)
    agent_registry.register(LawAgent.card)
    agent_registry.register(ReviewerAgent.card, owner=ReviewerAgent)


__all__ = [
    "ADMIN_ROLE",
    "AUDIT_AGENT_NAME",
    "CAP_REQUEST_LAW_RETRIEVAL",
    "CAP_RETRIEVE_KNOWLEDGE",
    "CAP_REVIEW_RETRIEVAL",
    "DECISIONS",
    "DECISION_APPROVE",
    "DECISION_REJECT",
    "DECISION_REVISE",
    "LAW_AGENT_NAME",
    "REVIEWER_AGENT_NAME",
    "SERVER_AGENTS",
    "A2ATransport",
    "A2AUser",
    "Agent",
    "AuditAgent",
    "LawAgent",
    "ReviewerAgent",
    "check_skill_permission",
    "decide_on_retrieval",
    "parse_result_payload",
    "register_agents",
    "server_agent",
]
