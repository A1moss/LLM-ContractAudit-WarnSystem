"""省赛第三阶段：Multi-Agent 最小真实协作闭环测试。

本文件要证明的核心命题（题面第四、十三、二十二节）
------------------------------------------------
> **ReviewerAgent 的中间决策真实改变了后续执行路径。**

断言方式：对 **Agent 调用次数** 与 **步骤序列** 直接下断言 ——
* `approve` → LawAgent 恰好被调用 **1** 次；
* `revise` → LawAgent 被调用 **2** 次；
* 第二次仍 `revise` → 整体 `rejected`（**绝不无限循环**）。

覆盖
----
* Agent：3 个 Agent 注册 / Agent Card / capability 正确性
* Orchestrator：初始流程、approve、revise、二次 approve、二次 revise→rejected、
  最大次数限制、非法决策、Agent 失败、A2A 失败
* 真实 E2E：真实 uvicorn + 真实 socket + 真实 HTTP + 真实 A2A + 真实 Agent routing
  + 真实 Skill Registry + 真实法规数据

允许 mock 什么
------------
只允许 mock **外部基础设施**（Chroma / sentence-transformers / BM25 检索结果）。
`Agent → Agent` 的 A2A 通信链**从不 mock**：
* 单元测试用真实 `AuditAgent` + 真实 `LawAgent` + 真实 `ReviewerAgent` + 真实 Agent Registry，
  只把"网络那一跳"换成受控的 `StubA2ATransport`（它内部仍走真实的 Agent 逻辑）；
* E2E 测试用**真实 HTTP**，连网络那一跳都是真的。

运行（backend 目录下）：
    python -m pytest tests/test_multi_agent.py -v

安全注意：本文件**绝不**触发真实 LLM 调用。
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ai.rag.vector_store as vector_store  # noqa: E402
from ai.a2a import agent_registry as a2a_agents  # noqa: E402
from ai.a2a.agents import (  # noqa: E402
    AUDIT_AGENT_NAME,
    CAP_RETRIEVE_KNOWLEDGE,
    CAP_REVIEW_RETRIEVAL,
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REVISE,
    LAW_AGENT_NAME,
    REVIEWER_AGENT_NAME,
    SERVER_AGENTS,
    LawAgent,
    ReviewerAgent,
    decide_on_retrieval,
)
from ai.a2a.multi_agent import (  # noqa: E402
    MAX_ATTEMPTS_CEILING,
    NODE_COMPLETED,
    NODE_FAILED,
    NODE_REJECTED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_REJECTED,
    MultiAgentOrchestrator,
    WorkflowRun,
    WorkflowStateError,
)
from ai.a2a.protocol import (  # noqa: E402
    STATUS_COMPLETED as A2A_STATUS_COMPLETED,
    A2AResult,
    A2ATask,
)
from ai.a2a.registry import (  # noqa: E402
    AUDIT_AGENT,
    LAW_AGENT,
    REVIEWER_AGENT,
)
from ai.a2a.transport import A2ATransportError  # noqa: E402
from ai.skills import registry as skill_registry  # noqa: E402
from api import deps  # noqa: E402
from api import a2a as a2a_api  # noqa: E402
from models.user import User  # noqa: E402


def _make_user(role: str = "uploader") -> User:
    user = User()
    user.id = 1
    user.username = f"test-{role}"
    user.role = role
    user.is_active = True
    return user


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _real_law_items() -> list:
    """真实形状的法规依据（来自 laws.json 的真实字段结构）。"""
    return [
        {"id": "6", "law": "民法典", "article": "第五百八十五条", "title": "违约金调整",
         "content": "当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金。",
         "score": 0.9, "source": "民法典第五百八十五条"},
        {"id": "92", "law": "合同编司法解释", "article": "第六十六条", "title": "违约金调整举证责任",
         "content": "当事人主张约定的违约金过分高于违约造成的损失，应当就其主张承担举证责任。",
         "score": 0.8, "source": "合同编司法解释第六十六条"},
    ]


class StubA2ATransport:
    """受控的 **A2A 传输替身**（只替换"网络那一跳"）。

    它内部**仍然调用真实的 Agent 逻辑**：
    * `LawAgent.handle_task` → `skill_registry.invoke("retrieve_knowledge", ...)`
    * `ReviewerAgent.handle_task` → 真实决策

    因此 Agent → Agent 的调度与决策逻辑**没有被 mock**；
    只把"外部检索结果"换成测试可控的序列（Case B 需要"第一次空、第二次有"）。
    """

    def __init__(self, law_items_sequence=None, *, raise_on=None):
        self.law_items_sequence = law_items_sequence or [_real_law_items()]
        self.raise_on = raise_on
        self.calls = []

    def _invoke_law(self, task, user):
        """真实 LawAgent + 真实 Skill Registry，只把 Skill 的**外部检索结果**换成受控值。

        capability 白名单、权限校验、禁用校验、Skill 转发逻辑全部真实执行。
        """
        call_index = sum(1 for c in self.calls if c["capability"] == CAP_RETRIEVE_KNOWLEDGE) - 1
        items = self.law_items_sequence[min(call_index, len(self.law_items_sequence) - 1)]

        skill = skill_registry.get(CAP_RETRIEVE_KNOWLEDGE)
        original_handler = skill.handler
        skill.handler = lambda payload: items
        try:
            return LawAgent().handle_task(task, user)
        finally:
            skill.handler = original_handler

    def post_task(self, task, *, bearer_token=None):
        user = _make_user("uploader")
        self.calls.append({
            "to_agent": task.to_agent,
            "capability": task.capability,
            "input": dict(task.input),
            "from_agent": task.from_agent,
        })

        if self.raise_on and task.to_agent == self.raise_on:
            raise A2ATransportError(f"模拟传输失败: {task.to_agent}")

        if task.to_agent == LAW_AGENT_NAME:
            return self._invoke_law(task, user).to_payload()
        if task.to_agent == REVIEWER_AGENT_NAME:
            return ReviewerAgent().handle_task(task, user).to_payload()
        raise A2ATransportError(f"未知目标 Agent: {task.to_agent}")

    # ── 断言辅助 ──
    def calls_to(self, agent_name):
        return [c for c in self.calls if c["to_agent"] == agent_name]

    def law_call_count(self):
        return len(self.calls_to(LAW_AGENT_NAME))

    def review_call_count(self):
        return len(self.calls_to(REVIEWER_AGENT_NAME))


# ══════════════════════════════════════════════════════════════════════
# 1. Agent 层
# ══════════════════════════════════════════════════════════════════════

class TestThreeAgentsRegistered(unittest.TestCase):
    def test_exactly_three_agents(self):
        self.assertEqual(sorted(a2a_agents.names()),
                         [AUDIT_AGENT, LAW_AGENT, REVIEWER_AGENT])

    def test_agent_cards(self):
        expected = {
            AUDIT_AGENT: ("request_law_retrieval", "method"),
            LAW_AGENT: ("retrieve_knowledge", "skill"),
            REVIEWER_AGENT: ("review_retrieval", "method"),
        }
        for name, (capability, implementation) in expected.items():
            with self.subTest(agent=name):
                card = a2a_agents.get(name)
                self.assertIsNotNone(card)
                self.assertEqual(card.capabilities, (capability,))
                self.assertEqual(card.implementation, implementation)
                self.assertTrue(card.description.strip())
                self.assertEqual(card.transport, "http")
                json.dumps(card.to_dict(), ensure_ascii=False)

    def test_law_agent_capability_is_a_real_skill(self):
        self.assertTrue(skill_registry.has(CAP_RETRIEVE_KNOWLEDGE))

    def test_decision_agent_capability_is_a_real_method(self):
        self.assertTrue(callable(getattr(ReviewerAgent, "review_retrieval", None)))
        self.assertFalse(skill_registry.has(CAP_REVIEW_RETRIEVAL),
                         "决策能力不是 Skill，不得谎称 skill-backed")

    def test_server_agents_are_law_and_reviewer(self):
        self.assertEqual(sorted(SERVER_AGENTS), [LAW_AGENT, REVIEWER_AGENT])
        self.assertNotIn(AUDIT_AGENT, SERVER_AGENTS, "audit_agent 是客户端侧")

    def test_registry_rejects_unknown_capability(self):
        from ai.a2a.protocol import AgentCard
        from ai.a2a.registry import AgentRegistrationError
        with self.assertRaises(AgentRegistrationError):
            a2a_agents.register(AgentCard(name="evil", description="x",
                                          capabilities=("os.system",)))


class TestReviewerDecisionIsReal(unittest.TestCase):
    """决策必须来自输入的真实中间结果，不能恒为某个值。"""

    def _law_result(self, items, status=A2A_STATUS_COMPLETED, error=None):
        task = A2ATask.from_payload({"to_agent": LAW_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE,
                                    "input": {"query": "x"}})
        return A2AResult(task_id=task.task_id, from_agent=LAW_AGENT, to_agent=AUDIT_AGENT,
                         status=status, trace_id=task.trace_id,
                         result={"items": items, "count": len(items)}, error=error)

    def test_approve_when_valid_items(self):
        outcome = decide_on_retrieval(self._law_result(_real_law_items()))
        self.assertEqual(outcome["decision"], DECISION_APPROVE)

    def test_revise_when_empty(self):
        outcome = decide_on_retrieval(self._law_result([]))
        self.assertEqual(outcome["decision"], DECISION_REVISE)

    def test_revise_when_items_lack_citation_fields(self):
        outcome = decide_on_retrieval(self._law_result([{"content": "有内容但无法条标识"}]))
        self.assertEqual(outcome["decision"], DECISION_REVISE)

    def test_revise_when_upstream_failed(self):
        outcome = decide_on_retrieval(
            self._law_result([], status="failed", error={"code": "SKILL_ERROR", "message": "x"})
        )
        self.assertEqual(outcome["decision"], DECISION_REVISE)

    def test_revise_when_no_result_at_all(self):
        self.assertEqual(decide_on_retrieval(None)["decision"], DECISION_REVISE)

    def test_decision_is_not_constant(self):
        """反"恒为 approve"的断言：同一函数对不同输入给出不同决策。"""
        approve = decide_on_retrieval(self._law_result(_real_law_items()))["decision"]
        revise = decide_on_retrieval(self._law_result([]))["decision"]
        self.assertNotEqual(approve, revise)

    def test_reviewer_agent_returns_structured_output(self):
        law = self._law_result(_real_law_items())
        task = A2ATask.from_payload({
            "to_agent": REVIEWER_AGENT, "capability": CAP_REVIEW_RETRIEVAL,
            "input": {"law_result": law.to_payload(), "attempt": 1}, "from_agent": AUDIT_AGENT,
        })
        result = ReviewerAgent().handle_task(task, _make_user("uploader"))
        self.assertEqual(result.status, A2A_STATUS_COMPLETED)
        body = result.result
        self.assertEqual(body["agent"], REVIEWER_AGENT)
        self.assertEqual(body["decision"], DECISION_APPROVE)
        self.assertIn("reason", body)
        self.assertIsNone(body["next_agent"])

    def test_reviewer_sets_next_agent_on_revise(self):
        law = self._law_result([])
        task = A2ATask.from_payload({
            "to_agent": REVIEWER_AGENT, "capability": CAP_REVIEW_RETRIEVAL,
            "input": {"law_result": law.to_payload(), "attempt": 1}, "from_agent": AUDIT_AGENT,
        })
        body = ReviewerAgent().handle_task(task, _make_user("uploader")).result
        self.assertEqual(body["decision"], DECISION_REVISE)
        self.assertEqual(body["next_agent"], LAW_AGENT)

    def test_reviewer_rejects_undeclared_capability(self):
        task = A2ATask.from_payload({
            "to_agent": REVIEWER_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE,
            "input": {}, "from_agent": AUDIT_AGENT,
        })
        result = ReviewerAgent().handle_task(task, _make_user("uploader"))
        self.assertNotEqual(result.status, A2A_STATUS_COMPLETED)


# ══════════════════════════════════════════════════════════════════════
# 2. Orchestrator（含"决策改变控制流"的核心断言）
# ══════════════════════════════════════════════════════════════════════

class TestOrchestratorCaseA_Approve(unittest.TestCase):
    """Case A：有效法规结果 → approve → completed（**不再调用第二次 LawAgent**）。"""

    def test_approve_path_single_law_call(self):
        transport = StubA2ATransport([_real_law_items()])
        run = MultiAgentOrchestrator(transport).run("违约金过高如何处理")

        self.assertEqual(run.status, STATUS_COMPLETED)
        self.assertEqual(run.node, NODE_COMPLETED)
        self.assertEqual(run.attempts, 1)

        # ★ 决策真的改变了流程：approve ⇒ 只调 1 次 LawAgent
        self.assertEqual(transport.law_call_count(), 1)
        self.assertEqual(transport.review_call_count(), 1)

        agents = [s.agent for s in run.steps]
        self.assertEqual(agents, [LAW_AGENT, REVIEWER_AGENT])
        self.assertEqual(run.steps[0].status, A2A_STATUS_COMPLETED)
        self.assertEqual(run.steps[1].decision, DECISION_APPROVE)
        self.assertEqual(run.current_agent, REVIEWER_AGENT)

    def test_final_result_carries_real_law_items(self):
        transport = StubA2ATransport([_real_law_items()])
        run = MultiAgentOrchestrator(transport).run("违约金")
        final = run.final_result
        self.assertEqual(final["review"]["decision"], DECISION_APPROVE)
        self.assertEqual(final["approved_on_attempt"], 1)
        self.assertEqual(final["law"]["count"], 2)
        self.assertEqual(final["law"]["skill"], CAP_RETRIEVE_KNOWLEDGE)
        self.assertEqual(final["law"]["skill_source"], "ai.rag.vector_store.search_knowledge")
        self.assertTrue(final["law"]["items"][0]["law"])


class TestOrchestratorCaseB_ReviseThenApprove(unittest.TestCase):
    """Case B：第一次空结果 → revise → 第二次有效结果 → approve → completed。"""

    def test_revise_then_approve_path(self):
        transport = StubA2ATransport([[], _real_law_items()])
        run = MultiAgentOrchestrator(transport).run("违约金过高如何处理")

        self.assertEqual(run.status, STATUS_COMPLETED)
        self.assertEqual(run.attempts, 2)

        # ★ 决策真的改变了流程：revise ⇒ 第二次 LawAgent 被调用
        self.assertEqual(transport.law_call_count(), 2)
        self.assertEqual(transport.review_call_count(), 2)

        decisions = [s.decision for s in run.steps if s.decision]
        self.assertEqual(decisions, [DECISION_REVISE, DECISION_APPROVE])
        agents = [s.agent for s in run.steps]
        self.assertEqual(agents, [LAW_AGENT, REVIEWER_AGENT, LAW_AGENT, REVIEWER_AGENT])
        self.assertEqual(run.final_result["approved_on_attempt"], 2)
        self.assertEqual(run.final_result["law"]["count"], 2)

    def test_retry_query_is_widened_not_identical(self):
        """重试必须是有意义的重试，而不是逐字重复同一个请求。"""
        transport = StubA2ATransport([[], _real_law_items()])
        MultiAgentOrchestrator(transport).run("违约金过高")
        queries = [c["input"]["query"] for c in transport.calls_to(LAW_AGENT)]
        self.assertEqual(len(queries), 2)
        self.assertNotEqual(queries[0], queries[1])
        self.assertTrue(queries[1].startswith(queries[0]))

    def test_first_reviewer_call_reported_revise(self):
        transport = StubA2ATransport([[], _real_law_items()])
        run = MultiAgentOrchestrator(transport).run("违约金")
        first_review = next(s for s in run.steps if s.agent == REVIEWER_AGENT)
        self.assertEqual(first_review.decision, DECISION_REVISE)
        self.assertEqual(first_review.next_agent, LAW_AGENT)
        self.assertIn("法规依据不足", first_review.reason)


class TestOrchestratorCaseC_ReviseThenReviseRejected(unittest.TestCase):
    """第二次仍 revise → **rejected**，绝不无限循环。"""

    def test_second_revise_leads_to_rejected(self):
        transport = StubA2ATransport([[], []])
        run = MultiAgentOrchestrator(transport).run("无法检索到的内容")

        self.assertEqual(run.status, STATUS_REJECTED)
        self.assertEqual(run.node, NODE_REJECTED)
        self.assertEqual(run.attempts, 2)

        # 严格上界：LawAgent 2 次、ReviewerAgent 2 次，**不是无限**
        self.assertEqual(transport.law_call_count(), 2)
        self.assertEqual(transport.review_call_count(), 2)
        self.assertEqual(len(run.steps), 4)
        self.assertEqual(run.error["code"], "REJECTED")
        self.assertIn("最大尝试次数", run.error["message"])

    def test_loop_is_bounded_by_ceiling(self):
        """即使请求更大次数，也被服务端上限夹住（2 次）。"""
        transport = StubA2ATransport([[], [], [], []])
        run = MultiAgentOrchestrator(transport, max_attempts=99).run("x")
        self.assertEqual(run.max_attempts, MAX_ATTEMPTS_CEILING)
        self.assertLessEqual(transport.law_call_count(), MAX_ATTEMPTS_CEILING)
        self.assertEqual(run.status, STATUS_REJECTED)

    def test_max_attempts_one_never_loops(self):
        transport = StubA2ATransport([[]])
        run = MultiAgentOrchestrator(transport, max_attempts=1).run("x")
        self.assertEqual(run.status, STATUS_REJECTED)
        self.assertEqual(transport.law_call_count(), 1)
        self.assertEqual(transport.review_call_count(), 1)


class _DecisionOverrideTransport(StubA2ATransport):
    """把 Reviewer 的决策强制改成指定值，用于测试非法/拒绝决策路径。"""

    def __init__(self, law_items_sequence, forced_decision):
        super().__init__(law_items_sequence)
        self.forced_decision = forced_decision

    def post_task(self, task, *, bearer_token=None):
        if task.to_agent == REVIEWER_AGENT_NAME:
            self.calls.append({"to_agent": task.to_agent, "capability": task.capability,
                               "input": dict(task.input), "from_agent": task.from_agent})
            probe = A2ATask.from_payload({
                "to_agent": REVIEWER_AGENT, "capability": CAP_REVIEW_RETRIEVAL,
                "input": {"law_result": None}, "from_agent": AUDIT_AGENT,
            })
            result = ReviewerAgent().handle_task(probe, _make_user("uploader"))
            result.result["decision"] = self.forced_decision
            result.result["reason"] = "测试强制决策"
            return result.to_payload()
        return super().post_task(task, bearer_token=bearer_token)


class TestOrchestratorFailurePaths(unittest.TestCase):
    def test_agent_rejected_decision_rejects_workflow(self):
        """Reviewer 明确 reject → 工作流 rejected（且不再重试）。"""
        transport = _DecisionOverrideTransport([_real_law_items()], DECISION_REJECT)
        run = MultiAgentOrchestrator(transport).run("x")
        self.assertEqual(run.status, STATUS_REJECTED)
        self.assertEqual(transport.law_call_count(), 1)

    def test_invalid_decision_fails_workflow(self):
        transport = _DecisionOverrideTransport([_real_law_items()], "maybe")
        run = MultiAgentOrchestrator(transport).run("x")
        self.assertEqual(run.status, STATUS_FAILED)
        self.assertEqual(run.node, NODE_FAILED)
        self.assertEqual(run.error["code"], "INVALID_DECISION")

    def test_transport_failure_on_law_agent(self):
        transport = StubA2ATransport([_real_law_items()], raise_on=LAW_AGENT_NAME)
        run = MultiAgentOrchestrator(transport).run("x")
        self.assertEqual(run.status, STATUS_FAILED)
        self.assertEqual(run.node, NODE_FAILED)
        self.assertEqual(run.error["code"], "TRANSPORT_ERROR")
        self.assertEqual(run.steps[-1].status, "failed")
        self.assertNotIn("Traceback", json.dumps(run.to_dict(), ensure_ascii=False))
        # LawAgent 失败后必须立刻终止，不得继续问 Reviewer
        self.assertEqual(transport.review_call_count(), 0)

    def test_transport_failure_on_reviewer_agent(self):
        transport = StubA2ATransport([_real_law_items()], raise_on=REVIEWER_AGENT_NAME)
        run = MultiAgentOrchestrator(transport).run("x")
        self.assertEqual(run.status, STATUS_FAILED)
        self.assertEqual(run.error["code"], "TRANSPORT_ERROR")

    def test_reviewer_rejection_stops_workflow(self):
        """Reviewer 因权限/未知能力拒绝执行 → failed，且**不**被当成 approve。"""
        transport = StubA2ATransport([_real_law_items()])
        original = ReviewerAgent.handle_task

        def _deny(self, task, user):
            return A2AResult.failed(task, code="UNAUTHORIZED", message="权限不足",
                                    from_agent=REVIEWER_AGENT_NAME)

        ReviewerAgent.handle_task = _deny
        try:
            run = MultiAgentOrchestrator(transport).run("x")
        finally:
            ReviewerAgent.handle_task = original

        self.assertEqual(run.status, STATUS_FAILED)
        self.assertEqual(run.error["code"], "UNAUTHORIZED")
        self.assertEqual(transport.law_call_count(), 1, "Reviewer 拒绝后不得再重试")

    def test_workflow_state_error_when_agent_missing(self):
        transport = StubA2ATransport([_real_law_items()])
        snapshot = dict(a2a_agents._cards)
        a2a_agents._cards.pop(REVIEWER_AGENT, None)
        try:
            with self.assertRaises(WorkflowStateError):
                MultiAgentOrchestrator(transport).run("x")
        finally:
            a2a_agents._cards.clear()
            a2a_agents._cards.update(snapshot)


class TestOrchestratorUsesA2AOnly(unittest.TestCase):
    """Orchestrator 不得绕过 A2A / 不得直接调 Skill。"""

    def test_orchestrator_talks_only_through_a2a_client(self):
        transport = StubA2ATransport([_real_law_items()])
        MultiAgentOrchestrator(transport).run("x")
        self.assertTrue(transport.calls)
        for call in transport.calls:
            self.assertEqual(call["from_agent"], AUDIT_AGENT_NAME)
            self.assertIn(call["to_agent"], (LAW_AGENT_NAME, REVIEWER_AGENT_NAME))

    def test_multi_agent_module_does_not_import_skill_functions(self):
        """静态检查：编排层不得**导入**受保护的业务模块，也不得直接调用业务函数。

        检查 import 语句与调用形态，而不是"文中是否出现过这个词"
        （docstring 里说明"不直接调"是允许且必要的）。
        """
        source = (_BACKEND_DIR / "ai" / "a2a" / "multi_agent.py").read_text(encoding="utf-8")
        import_lines = "\n".join(
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        )
        for forbidden in ("ai.skills", "ai.rag", "api.contracts", "ai.auditor", "ai.matcher"):
            self.assertNotIn(forbidden, import_lines,
                             f"multi_agent.py 不得 import {forbidden}")
        for call_shape in ("search_knowledge(", "run_rules(", "adjudicate_risks(",
                           "compare_clauses("):
            self.assertNotIn(call_shape, source,
                             f"multi_agent.py 不得直接调用 {call_shape}")

    def test_workflow_run_is_in_memory_only(self):
        transport = StubA2ATransport([_real_law_items()])
        run = MultiAgentOrchestrator(transport).run("x")
        self.assertIsInstance(run, WorkflowRun)
        payload = run.to_dict()
        json.dumps(payload, ensure_ascii=False)
        for field in ("workflow_id", "status", "current_agent", "steps",
                      "attempts", "final_result"):
            self.assertIn(field, payload)


# ══════════════════════════════════════════════════════════════════════
# 3. API（TestClient）
# ══════════════════════════════════════════════════════════════════════

class TestMultiAgentApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = FastAPI()
        cls._app.include_router(a2a_api.router, prefix="/api")
        cls._current = {"user": _make_user("uploader")}
        cls._app.dependency_overrides[deps.get_current_user] = lambda: cls._current["user"]
        cls._client = TestClient(cls._app)
        cls._orig_self = os.environ.get("A2A_SELF_BASE_URL")

    @classmethod
    def tearDownClass(cls):
        cls._app.dependency_overrides.clear()
        if cls._orig_self is None:
            os.environ.pop("A2A_SELF_BASE_URL", None)
        else:
            os.environ["A2A_SELF_BASE_URL"] = cls._orig_self

    def setUp(self):
        os.environ.pop("A2A_SELF_BASE_URL", None)

    def _as(self, role):
        self._current["user"] = _make_user(role)

    def test_agents_endpoint_now_lists_three(self):
        self._as("admin")
        resp = self._client.get("/api/a2a/agents")
        self.assertEqual(resp.status_code, 200, resp.text)
        names = sorted(a["name"] for a in resp.json()["data"]["agents"])
        self.assertEqual(names, [AUDIT_AGENT, LAW_AGENT, REVIEWER_AGENT])

    def test_reviewer_card_exposed(self):
        self._as("admin")
        card = self._client.get(f"/api/a2a/agents/{REVIEWER_AGENT}").json()["data"]
        self.assertEqual(card["capabilities"], [CAP_REVIEW_RETRIEVAL])
        self.assertEqual(card["endpoint"], "/api/a2a/tasks")

    def test_run_requires_authentication(self):
        bare = FastAPI()
        bare.include_router(a2a_api.router, prefix="/api")
        client = TestClient(bare)
        resp = client.post("/api/a2a/multi-agent/run", json={"query": "x"})
        self.assertIn(resp.status_code, (401, 403))

    def test_empty_query_400(self):
        self._as("uploader")
        resp = self._client.post("/api/a2a/multi-agent/run", json={"query": "   "})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_bad_top_k_400(self):
        self._as("uploader")
        for bad in (0, 11, -1):
            with self.subTest(top_k=bad):
                resp = self._client.post("/api/a2a/multi-agent/run",
                                         json={"query": "x", "top_k": bad})
                self.assertEqual(resp.status_code, 400, resp.text)

    def test_client_cannot_set_max_attempts(self):
        """`max_attempts` 必须是服务端固定：请求体里给也应被忽略。"""
        field_names = set(a2a_api.MultiAgentRunRequest.model_fields)
        self.assertNotIn("max_attempts", field_names,
                         "请求模型不得暴露 max_attempts，避免客户端把循环改成无限")
        model = a2a_api.MultiAgentRunRequest.model_validate(
            {"query": "x", "max_attempts": 9999}
        )
        self.assertFalse(hasattr(model, "max_attempts"))

    def test_arbitrary_url_cannot_be_injected(self):
        """出站目标只允许本机回环 + 固定路径（防 SSRF）。"""
        self._as("uploader")
        os.environ["A2A_SELF_BASE_URL"] = "http://evil.example.com"
        try:
            resp = self._client.post("/api/a2a/multi-agent/run", json={"query": "x"})
            self.assertEqual(resp.status_code, 400, resp.text)
        finally:
            os.environ.pop("A2A_SELF_BASE_URL", None)

    def test_transport_failure_maps_to_502(self):
        self._as("uploader")
        os.environ["A2A_SELF_BASE_URL"] = f"http://127.0.0.1:{_free_port()}"
        try:
            resp = self._client.post("/api/a2a/multi-agent/run", json={"query": "x"})
            self.assertEqual(resp.status_code, 502, resp.text)
        finally:
            os.environ.pop("A2A_SELF_BASE_URL", None)


# ══════════════════════════════════════════════════════════════════════
# 4. 真实 E2E：真实 uvicorn + 真实 socket + 真实 HTTP + 真实 A2A
# ══════════════════════════════════════════════════════════════════════

class _empty_first_search:
    """上下文管理器：让前 N 次**检索**（= N 次 `search_knowledge`）返回空。

    这是**外部基础设施替身**（模拟"这次检索没查到东西"）。

    实现：用共享计数器在**底层召回函数**（`_dense_search` / `bm25_search`）上生效 ——
    每两次底层调用 = 一次 `search_knowledge`（稠密一路 + BM25 一路），
    因此 `empty_retrievals=N` 精确对应"前 N 次检索为空"。

    为什么打底层召回函数、而不是替换 `search_knowledge` 自身：
    Skill 适配器对 `search_knowledge` 做了**函数对象缓存**（`adapters._lazy`），
    直接改模块属性对该缓存无效；只有每次调用时才解析的底层函数才会生效。
    这样"只 mock 外部检索"的边界保持干净 —— Agent → Agent 通信始终真实。
    """

    _ARMS_PER_RETRIEVAL = 2  # 稠密 + BM25

    def __init__(self, empty_retrievals):
        self.empty_retrievals = empty_retrievals
        self._orig_dense = None
        self._orig_bm25 = None

    def __enter__(self):
        self._orig_dense = vector_store._dense_search
        self._orig_bm25 = vector_store.bm25_search
        real_dense, real_bm25 = self._orig_dense, self._orig_bm25
        budget = {"arms": self.empty_retrievals * self._ARMS_PER_RETRIEVAL}
        lock = threading.Lock()

        def _should_empty():
            with lock:
                if budget["arms"] > 0:
                    budget["arms"] -= 1
                    return True
                return False

        def _dense(query, collection_name, top_k):
            if _should_empty():
                return []
            return real_dense(query, collection_name, top_k)

        def _bm25(query, collection_name, docs, top_k=5, version=None):
            if _should_empty():
                return []
            return real_bm25(query, collection_name, docs, top_k, version)

        vector_store._dense_search = _dense
        vector_store.bm25_search = _bm25
        return self

    def __exit__(self, *exc):
        if self._orig_dense is not None:
            vector_store._dense_search = self._orig_dense
        if self._orig_bm25 is not None:
            vector_store.bm25_search = self._orig_bm25
        return False


class TestMultiAgentRealHttpEndToEnd(unittest.TestCase):
    """只 mock 外部基础设施（检索召回），**不 mock** Agent → Agent。

    真实 HTTP 链路（每一跳都经 socket）：
        测试客户端 ──HTTP──▶ /api/a2a/multi-agent/run
                              ↓
                        Orchestrator ──HTTP──▶ /api/a2a/tasks ──▶ LawAgent ──▶ Skill Registry
                                                                            ──▶ search_knowledge
                                                                            ──▶ 真实 laws.json
                              ↓
                        Orchestrator ──HTTP──▶ /api/a2a/tasks ──▶ ReviewerAgent ──▶ decision
    """

    @classmethod
    def setUpClass(cls):
        from main import app as real_app  # noqa: E402

        cls._port = _free_port()
        cls._base = f"http://127.0.0.1:{cls._port}"
        real_app.dependency_overrides[deps.get_current_user] = lambda: _make_user("uploader")
        cls._app = real_app

        cls._orig_self_url = os.environ.get("A2A_SELF_BASE_URL")
        os.environ["A2A_SELF_BASE_URL"] = cls._base

        config = uvicorn.Config(real_app, host="127.0.0.1", port=cls._port,
                                log_level="warning", lifespan="on", access_log=False)
        cls._server = uvicorn.Server(config)
        cls._thread = threading.Thread(target=cls._server.run, daemon=True)
        cls._thread.start()

        deadline = time.time() + 90
        while time.time() < deadline:
            if getattr(cls._server, "started", False):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn 测试服务器未能在 90s 内启动")

    @classmethod
    def tearDownClass(cls):
        cls._server.should_exit = True
        cls._thread.join(timeout=20)
        cls._app.dependency_overrides.clear()
        if cls._orig_self_url is None:
            os.environ.pop("A2A_SELF_BASE_URL", None)
        else:
            os.environ["A2A_SELF_BASE_URL"] = cls._orig_self_url

    def test_step_agents_listed_over_real_http(self):
        resp = httpx.get(f"{self._base}/api/a2a/agents", timeout=30)
        self.assertEqual(resp.status_code, 200, resp.text)
        names = sorted(a["name"] for a in resp.json()["data"]["agents"])
        self.assertEqual(names, [AUDIT_AGENT, LAW_AGENT, REVIEWER_AGENT])

    def test_full_loop_over_real_http_case_a_approve(self):
        """Case A：真实 HTTP 全链路，一次通过。"""
        with _empty_first_search(empty_retrievals=0):
            resp = httpx.post(f"{self._base}/api/a2a/multi-agent/run",
                              json={"query": "违约金过高 调整", "top_k": 3}, timeout=180)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]

        self.assertEqual(data["transport"], "http")
        self.assertTrue(data["target"].endswith("/api/a2a/tasks"))
        self.assertEqual(data["status"], STATUS_COMPLETED)
        self.assertEqual(data["attempts"], 1)

        agents = [s["agent"] for s in data["steps"]]
        self.assertEqual(agents, [LAW_AGENT, REVIEWER_AGENT])
        self.assertEqual(data["steps"][1]["decision"], DECISION_APPROVE)

        final = data["final_result"]
        self.assertEqual(final["law"]["skill"], CAP_RETRIEVE_KNOWLEDGE)
        self.assertEqual(final["law"]["skill_source"], "ai.rag.vector_store.search_knowledge")
        self.assertGreater(final["law"]["count"], 0, "必须返回真实法规结果")
        self.assertTrue(any(it.get("law") for it in final["law"]["items"]))

    def test_full_loop_over_real_http_case_b_revise_then_approve(self):
        """Case B：真实 HTTP 全链路，**第一次检索为空 → revise → 第二次通过**。

        这证明决策回路在**真实网络**上确实改变了执行路径：
        出站请求从 2 次（Law + Review）变成 4 次（Law, Review, Law, Review）。
        """
        with _empty_first_search(empty_retrievals=1):
            resp = httpx.post(f"{self._base}/api/a2a/multi-agent/run",
                              json={"query": "违约金过高 调整", "top_k": 3}, timeout=180)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]

        self.assertEqual(data["status"], STATUS_COMPLETED)
        self.assertEqual(data["attempts"], 2)
        agents = [s["agent"] for s in data["steps"]]
        self.assertEqual(agents, [LAW_AGENT, REVIEWER_AGENT, LAW_AGENT, REVIEWER_AGENT],
                         "revise 必须真的触发第二次 LawAgent")
        decisions = [s["decision"] for s in data["steps"] if "decision" in s]
        self.assertEqual(decisions, [DECISION_REVISE, DECISION_APPROVE])
        self.assertGreater(data["final_result"]["law"]["count"], 0)
        self.assertEqual(data["final_result"]["approved_on_attempt"], 2)

    def test_full_loop_over_real_http_case_c_rejected(self):
        """Case C：两次都空 → rejected（真实 HTTP 上的有界循环）。"""
        with _empty_first_search(empty_retrievals=99):
            resp = httpx.post(f"{self._base}/api/a2a/multi-agent/run",
                              json={"query": "无关内容 zzzz", "top_k": 2}, timeout=180)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["status"], STATUS_REJECTED)
        self.assertEqual(data["attempts"], MAX_ATTEMPTS_CEILING)
        self.assertEqual(len(data["steps"]), 4)
        self.assertEqual([s["decision"] for s in data["steps"] if "decision" in s],
                         [DECISION_REVISE, DECISION_REVISE])

    def test_skill_api_and_a2a_still_work(self):
        """前两阶段产物未被本阶段破坏。"""
        skills = httpx.get(f"{self._base}/api/skills", timeout=30)
        self.assertEqual(skills.status_code, 200)
        self.assertEqual(skills.json()["data"]["total"], 12)

        card = httpx.get(f"{self._base}/api/a2a/agents/{LAW_AGENT}", timeout=30)
        self.assertEqual(card.status_code, 200)
        self.assertEqual(card.json()["data"]["capabilities"], [CAP_RETRIEVE_KNOWLEDGE])


if __name__ == "__main__":
    unittest.main()
