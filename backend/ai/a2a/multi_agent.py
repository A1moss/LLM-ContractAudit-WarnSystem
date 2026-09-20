"""ai.a2a.multi_agent — Multi-Agent 最小真实协作闭环（本阶段核心）。

本阶段到底证明了什么（不得夸大）
------------------------------
    AuditAgent
        │  A2A
        ▼
    LawAgent ──retrieve_knowledge Skill──▶ 真实法规依据
        │  A2A（把中间结果原样传给下一个 Agent）
        ▼
    ReviewerAgent ──▶ decision ∈ {approve, revise}
        │
        ├── approve → COMPLETED（**不再调用第二次 LawAgent**）
        └── revise  → 回到 LawAgent（第二次）
                        │  A2A
                        ▼
                   ReviewerAgent
                        ├── approve → COMPLETED
                        └── revise  → REJECTED（到达上限，绝不无限循环）

**这不是"三个顺序调用的 Agent"**：ReviewerAgent 的 `decision` 是
`MultiAgentOrchestrator` 的**控制流输入** —— 它决定"是否再调用一次 LawAgent"。
测试用调用计数断言了这一点（approve 时 LawAgent 恰好被调 1 次，
revise 时恰好 2 次）。

状态机（刻意用有限状态，不做 workflow engine）
--------------------------------------------
    START → LAW_RETRIEVAL → REVIEW ─┬─ approve → COMPLETED
                                    └─ revise  → LAW_RETRIEVAL → REVIEW ─┬─ approve → COMPLETED
                                                                         └─ revise  → REJECTED

上界：最多 2 次 LawAgent + 2 次 ReviewerAgent。`max_attempts` 由**服务端固定**，
不接受客户端参数覆盖（防止把循环次数改成无限）。

边界（本模块**不做**的事）
------------------------
* 不调用任何 Skill、不直接调检索函数、不重写审核/法规检索逻辑；
* 不绕过 A2A：与 Agent 的所有交互都经 `A2ATaskClient`（HTTP）；
* 不落库（优先内存对象，本轮明确不建表）；
* 不接 LLM：Reviewer 的决策是确定性函数（`decide_on_retrieval`），
  这样"决策影响控制流"才可复现、可断言。将来换成 LLM Reviewer 只需替换该函数。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ai.a2a.agents import (
    AUDIT_AGENT_NAME,
    CAP_RETRIEVE_KNOWLEDGE,
    CAP_REVIEW_RETRIEVAL,
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REVISE,
    DECISIONS,
    LAW_AGENT_NAME,
    REVIEWER_AGENT_NAME,
    AuditAgent,
)
from ai.a2a.protocol import A2AResult, A2ATask, new_id
from ai.a2a.registry import agent_registry

logger = logging.getLogger(__name__)

# ── 工作流状态 ──
STATUS_COMPLETED = "completed"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUSES = (STATUS_COMPLETED, STATUS_REJECTED, STATUS_FAILED)

# ── 状态机节点（对外暴露，便于测试断言"状态非法"）──
NODE_START = "START"
NODE_LAW_RETRIEVAL = "LAW_RETRIEVAL"
NODE_REVIEW = "REVIEW"
NODE_COMPLETED = "COMPLETED"
NODE_REJECTED = "REJECTED"
NODE_FAILED = "FAILED"

# 服务端固定上限：最多 2 次检索尝试（= 最多 1 次 revise 回路）
DEFAULT_MAX_ATTEMPTS = 2
MAX_ATTEMPTS_CEILING = 2

# 第二次检索的查询扩写后缀（让重试**不是**完全相同的请求，具备可解释性）
RETRY_QUERY_SUFFIX = " 相关司法解释 与 处理方式"


class A2ATaskClient(Protocol):
    """Orchestrator 与 Agent 通信的唯一出口（真实 HTTP 实现见 transport.py）。"""

    def post_task(self, task: A2ATask, *, bearer_token: str | None = None) -> dict:
        ...


@dataclass
class WorkflowStep:
    """工作流中的一步（谁做的、结果如何、以及决策型 Agent 的决策）。"""

    agent: str
    status: str
    capability: str = ""
    decision: str = ""
    reason: str = ""
    next_agent: str | None = None
    attempt: int = 0
    task_id: str = ""
    trace_id: str = ""
    error: dict | None = None

    def to_dict(self) -> dict:
        payload = {
            "agent": self.agent,
            "status": self.status,
            "capability": self.capability,
            "attempt": self.attempt,
        }
        if self.decision:
            payload["decision"] = self.decision
            payload["reason"] = self.reason
            payload["next_agent"] = self.next_agent
        if self.task_id:
            payload["task_id"] = self.task_id
            payload["trace_id"] = self.trace_id
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class WorkflowRun:
    """一次 Multi-Agent 运行的内存对象（本轮**不落库**）。"""

    workflow_id: str = field(default_factory=new_id)
    trace_id: str = field(default_factory=new_id)
    query: str = ""
    status: str = NODE_START
    node: str = NODE_START
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    steps: list[WorkflowStep] = field(default_factory=list)
    final_result: dict | None = None
    error: dict | None = None

    @property
    def current_agent(self) -> str:
        """当前（最后一个）产生结果的 Agent —— 初始为发起方 AuditAgent。"""
        for step in reversed(self.steps):
            if step.agent:
                return step.agent
        return AUDIT_AGENT_NAME

    def add_step(self, step: WorkflowStep) -> WorkflowStep:
        self.steps.append(step)
        return step

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "trace_id": self.trace_id,
            "query": self.query,
            "status": self.status,
            "current_agent": self.current_agent,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "steps": [s.to_dict() for s in self.steps],
            "final_result": self.final_result or {},
            "error": self.error,
        }


class WorkflowStateError(RuntimeError):
    """状态机被非法推进（防御性：正常路径不应出现）。"""


class MultiAgentOrchestrator:
    """极薄的 Multi-Agent 编排器。

    只做 6 件事（题面第九节）：建任务 → 调 Agent → 收中间结果 →
    按 Reviewer 的 decision 选下一步 → 控制最大循环次数 → 汇总最终结果。

    它**不**重新实现法规检索 / 合同审核 / Skill，也**不**直接调用 `search_knowledge`；
    与 Agent 的全部交互都经 `A2ATaskClient`（真实 HTTP）。
    """

    def __init__(
        self,
        client: A2ATaskClient,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        bearer_token: str | None = None,
    ):
        # 服务端固定上限：夹到 [1, MAX_ATTEMPTS_CEILING]，杜绝无限循环
        try:
            wanted = int(max_attempts)
        except (TypeError, ValueError):
            wanted = DEFAULT_MAX_ATTEMPTS
        self.max_attempts = max(1, min(wanted, MAX_ATTEMPTS_CEILING))
        self._client = client
        self._bearer_token = bearer_token
        # 发起方身份：复用第二阶段的 AuditAgent（它自己持有 transport）
        self._audit_agent = AuditAgent(client)

    # ── 对外唯一入口 ──
    def run(
        self,
        query: str,
        *,
        top_k: int = 3,
        collection_name: str | None = None,
        trace_id: str | None = None,
    ) -> WorkflowRun:
        """跑一次完整的 Multi-Agent 闭环，返回 `WorkflowRun`。"""
        run = WorkflowRun(
            query=query,
            trace_id=trace_id or new_id(),
            max_attempts=self.max_attempts,
            node=NODE_START,
            status=NODE_START,
        )
        self._assert_agents_available()

        attempt = 0
        while True:
            attempt += 1
            run.attempts = attempt

            # ── 节点：LAW_RETRIEVAL（经 A2A 请 LawAgent 调 retrieve_knowledge Skill）──
            run.node = NODE_LAW_RETRIEVAL
            law_result = self._call_law_agent(
                run, attempt=attempt, top_k=top_k, collection_name=collection_name
            )
            if law_result is None:
                # 传输层已失败（`_call_law_agent` 已把 run 置为 failed）：
                # **必须立刻终止**，不能继续进入 REVIEW —— 否则会用一个"没拿到结果"
                # 的状态去问 Reviewer，把 failed 误报成 rejected（状态说谎）。
                if run.status != STATUS_FAILED:
                    self._fail(run, "TRANSPORT_ERROR", "未从 LawAgent 获得结果")
                return run

            # ── 节点：REVIEW（经 A2A 请 ReviewerAgent 基于中间结果做决策）──
            run.node = NODE_REVIEW
            review_result = self._call_reviewer_agent(run, law_result, attempt=attempt)
            if review_result is None:
                # Reviewer 拒绝执行（权限/未知能力）：链路中止，不当作 approve
                run.status = STATUS_FAILED
                run.node = NODE_FAILED
                return run

            decision = self._extract_decision(review_result)
            if decision not in DECISIONS:
                # 非法决策：交给 Orchestrator 兜底，绝不默默继续
                self._fail(run, "INVALID_DECISION", f"ReviewerAgent 返回了非法决策 {decision!r}")
                return run

            # ── 决策即控制流 ──
            if decision == DECISION_APPROVE:
                run.status = STATUS_COMPLETED
                run.node = NODE_COMPLETED
                run.final_result = self._final_result(law_result, review_result, attempt)
                return run

            if decision == DECISION_REJECT:
                self._reject(run, review_result, "ReviewerAgent 明确拒绝")
                return run

            # decision == revise
            if attempt >= self.max_attempts:
                self._reject(
                    run,
                    review_result,
                    f"已达到最大尝试次数（{self.max_attempts}）仍未通过复核",
                )
                return run
            # 否则回到 LAW_RETRIEVAL（下一次循环），这就是"协作回路"

    # ── A2A 调用（唯一对外通道）──
    def _call_law_agent(
        self, run: WorkflowRun, *, attempt: int, top_k: int, collection_name: str | None
    ) -> A2AResult | None:
        """经 A2A 请 LawAgent 执行 `retrieve_knowledge`。

        第 2 次尝试会**扩写查询**，让重试是基于上一次失败的有意义重试，
        而不是发出一个逐字相同的请求。
        """
        query = run.query if attempt == 1 else (run.query + RETRY_QUERY_SUFFIX)
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if collection_name:
            payload["collection_name"] = collection_name

        try:
            result = self._audit_agent.request(
                CAP_RETRIEVE_KNOWLEDGE,
                payload,
                to_agent=LAW_AGENT_NAME,
                bearer_token=self._bearer_token,
                trace_id=run.trace_id,
            )
        except Exception as exc:  # noqa: BLE001 —— 传输层失败不能吞
            logger.warning("Multi-Agent: 调 LawAgent 失败 attempt=%s: %s", attempt, exc)
            run.add_step(WorkflowStep(
                agent=LAW_AGENT_NAME, status="failed", capability=CAP_RETRIEVE_KNOWLEDGE,
                attempt=attempt, error={"code": "TRANSPORT_ERROR", "message": str(exc)},
            ))
            self._fail(run, "TRANSPORT_ERROR", f"调用 LawAgent 失败：{exc}")
            return None

        run.add_step(WorkflowStep(
            agent=result.from_agent or LAW_AGENT_NAME,
            status=result.status,
            capability=CAP_RETRIEVE_KNOWLEDGE,
            attempt=attempt,
            task_id=result.task_id,
            trace_id=result.trace_id,
            error=result.error,
        ))
        return result

    def _call_reviewer_agent(
        self, run: WorkflowRun, law_result: A2AResult | None, *, attempt: int
    ) -> A2AResult | None:
        """经 A2A 把**上一跳的真实中间结果**交给 ReviewerAgent 做决策。"""
        payload = {
            # 原样透传上一跳的 A2AResult（Reviewer 自己解析）
            "law_result": law_result.to_payload() if law_result is not None else None,
            "attempt": attempt,
        }
        try:
            result = self._audit_agent.request(
                CAP_REVIEW_RETRIEVAL,
                payload,
                to_agent=REVIEWER_AGENT_NAME,
                bearer_token=self._bearer_token,
                trace_id=run.trace_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Multi-Agent: 调 ReviewerAgent 失败 attempt=%s: %s", attempt, exc)
            run.add_step(WorkflowStep(
                agent=REVIEWER_AGENT_NAME, status="failed", capability=CAP_REVIEW_RETRIEVAL,
                attempt=attempt, error={"code": "TRANSPORT_ERROR", "message": str(exc)},
            ))
            self._fail(run, "TRANSPORT_ERROR", f"调用 ReviewerAgent 失败：{exc}")
            return None

        body = result.result or {}
        decision = str(body.get("decision") or "")
        run.add_step(WorkflowStep(
            agent=result.from_agent or REVIEWER_AGENT_NAME,
            status=result.status,
            capability=CAP_REVIEW_RETRIEVAL,
            decision=decision,
            reason=str(body.get("reason") or ""),
            next_agent=body.get("next_agent"),
            attempt=attempt,
            task_id=result.task_id,
            trace_id=result.trace_id,
            error=result.error,
        ))

        if result.status != "completed":
            # Reviewer 拒绝执行（权限不足 / 未知能力 / 输入非法）：链路中止
            self._fail(
                run,
                (result.error or {}).get("code", "REVIEW_FAILED"),
                (result.error or {}).get("message", "ReviewerAgent 未完成复核"),
            )
            return None
        return result

    # ── 辅助 ──
    @staticmethod
    def _extract_decision(review_result: A2AResult) -> str:
        return str((review_result.result or {}).get("decision") or "")

    def _assert_agents_available(self) -> None:
        """白名单校验：编排只允许调用 Agent Registry 中真实存在的 Agent。"""
        for name in (LAW_AGENT_NAME, REVIEWER_AGENT_NAME):
            if not agent_registry.has(name):
                raise WorkflowStateError(f"Multi-Agent 依赖的 Agent {name!r} 未注册")

    @staticmethod
    def _final_result(
        law_result: A2AResult | None, review_result: A2AResult, attempt: int
    ) -> dict:
        """最终结果 = Reviewer 的决策 + LawAgent 的真实检索结果（原样透出）。"""
        return {
            "review": {
                "agent": review_result.from_agent,
                "decision": (review_result.result or {}).get("decision"),
                "reason": (review_result.result or {}).get("reason"),
            },
            "law": {
                "agent": law_result.from_agent if law_result else None,
                "skill": law_result.skill if law_result else None,
                "skill_source": law_result.skill_source if law_result else None,
                "status": law_result.status if law_result else None,
                "items": ((law_result.result or {}).get("items") if law_result else []) or [],
                "count": ((law_result.result or {}).get("count") if law_result else 0) or 0,
            },
            "approved_on_attempt": attempt,
        }

    @staticmethod
    def _fail(run: WorkflowRun, code: str, message: str) -> None:
        run.status = STATUS_FAILED
        run.node = NODE_FAILED
        run.error = {"code": code, "message": message}
        run.final_result = {"error": run.error}

    @staticmethod
    def _reject(run: WorkflowRun, review_result: A2AResult | None, reason: str) -> None:
        run.status = STATUS_REJECTED
        run.node = NODE_REJECTED
        run.error = {"code": "REJECTED", "message": reason}
        run.final_result = {
            "review": {
                "agent": review_result.from_agent if review_result else REVIEWER_AGENT_NAME,
                "decision": (review_result.result or {}).get("decision") if review_result else None,
                "reason": (review_result.result or {}).get("reason") if review_result else reason,
            },
            "reason": reason,
        }


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_ATTEMPTS_CEILING",
    "NODE_COMPLETED",
    "NODE_FAILED",
    "NODE_LAW_RETRIEVAL",
    "NODE_REJECTED",
    "NODE_REVIEW",
    "NODE_START",
    "RETRY_QUERY_SUFFIX",
    "STATUSES",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_REJECTED",
    "A2ATaskClient",
    "MultiAgentOrchestrator",
    "WorkflowRun",
    "WorkflowStateError",
    "WorkflowStep",
]
