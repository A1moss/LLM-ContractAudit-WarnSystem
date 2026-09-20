"""ai.a2a — A2A（Agent-to-Agent）最小真实通信闭环。

本阶段（省赛第二阶段）完成了什么
------------------------------
    AuditAgent ──HTTP POST A2ATask──▶ LawAgent ──Skill Registry──▶ retrieve_knowledge
                                                                            │
                                                            现有真实函数 search_knowledge
                                                                            │
    AuditAgent ◀──HTTP 200 A2AResult────────────────────────────────────────┘

* **真实 HTTP**：Agent A 用 httpx 发 POST，Agent B 是 FastAPI 端点（`/api/a2a/tasks`）。
  不是 `audit_agent()` 里直接调 `law_agent()`。
* **A2A 只管通信，Skill 只管能力**：`A2ATask.capability` 必须是
  `ai.skills.registry` 中已注册的 Skill 名，由 LawAgent 转发给 Skill Registry，
  最终落到现有真实函数。A2A **没有**再造一套业务函数。

本阶段**没有**完成什么（务必如实表述）
------------------------------------
* 不是 Multi-Agent：没有编排器、没有 planner、没有"按中间结果选择下一个 Agent"、
  没有 memory、没有任务图、没有失败隔离与重试。
* 没有 A2A 认证体系、没有 Agent 动态发现、没有跨机器部署、
  没有消息队列（Redis / Celery / Kafka 一概未引入）。
* 与主审核链路完全无关：`_run_audit` / `api/contracts.py` 均不引用本包。

目录
----
* `protocol.py`  — A2ATask / A2AResult / AgentCard 与校验
* `registry.py`  — Agent Registry（登记 + 按名/按能力查询；能力必须对应真实 Skill）
* `agents.py`    — AuditAgent（客户端）/ LawAgent（服务端）
* `transport.py` — 真实 HTTP 传输（httpx，目标地址限本机回环）
"""
from ai.a2a import registry  # noqa: F401
from ai.a2a.agents import (  # noqa: F401
    CAP_REQUEST_LAW_RETRIEVAL,
    CAP_RETRIEVE_KNOWLEDGE,
    CAP_REVIEW_RETRIEVAL,
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REVISE,
    AuditAgent,
    LawAgent,
    ReviewerAgent,
    decide_on_retrieval,
    parse_result_payload,
    register_agents,
    server_agent,
)
from ai.a2a.multi_agent import (  # noqa: F401
    DEFAULT_MAX_ATTEMPTS,
    MAX_ATTEMPTS_CEILING,
    MultiAgentOrchestrator,
    WorkflowRun,
)
from ai.a2a.protocol import (  # noqa: F401
    SCHEMA_VERSION,
    A2AError,
    A2AResult,
    A2ATask,
    AgentCard,
)
from ai.a2a.registry import AUDIT_AGENT, LAW_AGENT, REVIEWER_AGENT, agent_registry  # noqa: F401
from ai.a2a.transport import HttpA2ATransport  # noqa: F401

# 触发声明式登记（三个 Agent Card；能力真实性由 AgentRegistry 校验）
register_agents()

__all__ = [
    "A2AError",
    "A2AResult",
    "A2ATask",
    "AUDIT_AGENT",
    "AgentCard",
    "AuditAgent",
    "CAP_REQUEST_LAW_RETRIEVAL",
    "CAP_RETRIEVE_KNOWLEDGE",
    "CAP_REVIEW_RETRIEVAL",
    "DECISION_APPROVE",
    "DECISION_REJECT",
    "DECISION_REVISE",
    "DEFAULT_MAX_ATTEMPTS",
    "HttpA2ATransport",
    "LAW_AGENT",
    "LawAgent",
    "MAX_ATTEMPTS_CEILING",
    "MultiAgentOrchestrator",
    "REVIEWER_AGENT",
    "ReviewerAgent",
    "SCHEMA_VERSION",
    "WorkflowRun",
    "agent_registry",
    "decide_on_retrieval",
    "parse_result_payload",
    "register_agents",
    "registry",
    "server_agent",
]
