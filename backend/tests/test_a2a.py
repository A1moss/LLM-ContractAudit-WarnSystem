"""省赛第二阶段：A2A 最小真实闭环测试。

覆盖（题面第十四节要求）
----------------------
* **Protocol**：A2ATask / A2AResult / AgentCard 的 schema、schema_version、task_id、trace_id
* **Registry**：Agent 注册 / 查询 / capability 查询 / 不存在 Agent / 不存在 capability
* **API**：未登录 / 非法 Agent / 非法 capability / 缺少字段 / 错误输入 / 正常任务
* **真实 E2E**（最关键）：
      AuditAgent ──HTTP POST（真实 socket）──▶ LawAgent ──▶ Skill Registry
                 ──▶ retrieve_knowledge ──▶ 现有真实函数 ──▶ A2AResult ──▶ AuditAgent

E2E 不做假的地方
--------------
`TestA2ARealHttpEndToEnd` 会**真的起一个 uvicorn 服务**（后台线程 + 真实 loopback socket），
然后用 httpx 发**真实 HTTP 请求**。整条链路

    HTTP → Task validation → Agent routing → LawAgent → Skill Registry → retrieve_knowledge
         → ai.rag.vector_store.search_knowledge → 真实 laws.json（BM25）

全部真实执行；只有**稠密向量检索那一段外部基础设施**（sentence-transformers / Chroma）
在本测试中被替换为"返回空"，使 BM25 成为唯一召回路径 —— 这样：
* 不需要 2GB 模型与 GPU；
* 召回结果来自**真实语料** `backend/ai/knowledge/laws.json`（真实法条、真实条文号）。

**A → HTTP → B → Skill Registry 这一段没有任何 mock。**

运行（backend 目录下）：
    python -m pytest tests/test_a2a.py -v

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
    CAP_REQUEST_LAW_RETRIEVAL,
    CAP_RETRIEVE_KNOWLEDGE,
    AuditAgent,
    LawAgent,
    parse_result_payload,
)
from ai.a2a.protocol import (  # noqa: E402
    ERR_INVALID_TASK,
    ERR_UNKNOWN_CAPABILITY,
    SCHEMA_VERSION,
    STATUS_COMPLETED,
    STATUS_REJECTED,
    A2AResult,
    A2ATask,
    A2ATaskValidationError,
    AgentCard,
)
from ai.a2a.registry import (  # noqa: E402
    AUDIT_AGENT,
    LAW_AGENT,
    REVIEWER_AGENT,
    AgentRegistrationError,
)
from ai.a2a.transport import (  # noqa: E402
    A2A_TASK_PATH,
    A2ATransportError,
    HttpA2ATransport,
    validate_task_url,
)
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


# ══════════════════════════════════════════════════════════════════════
# 1. Protocol
# ══════════════════════════════════════════════════════════════════════

class TestA2ATaskProtocol(unittest.TestCase):
    def test_from_payload_accepts_minimal_valid_task(self):
        task = A2ATask.from_payload({
            "to_agent": "law_agent",
            "capability": "retrieve_knowledge",
            "input": {"query": "合同违约金", "top_k": 3},
        })
        self.assertEqual(task.to_agent, "law_agent")
        self.assertEqual(task.capability, "retrieve_knowledge")
        self.assertEqual(task.input["top_k"], 3)
        self.assertEqual(task.schema_version, SCHEMA_VERSION)
        # task_id / trace_id / created_at 自动生成
        self.assertTrue(task.task_id)
        self.assertTrue(task.trace_id)
        self.assertNotEqual(task.task_id, task.trace_id)
        self.assertTrue(task.created_at.endswith("Z"))

    def test_payload_roundtrip_keeps_all_required_fields(self):
        task = A2ATask.from_payload({
            "schema_version": SCHEMA_VERSION,
            "task_id": "t-1",
            "from_agent": "audit_agent",
            "to_agent": "law_agent",
            "capability": "retrieve_knowledge",
            "input": {"query": "违约金"},
            "trace_id": "tr-1",
        })
        payload = task.to_payload()
        for field in A2ATask.REQUIRED_FIELDS:
            self.assertIn(field, payload, f"序列化后必须保留必要字段 {field}")
        self.assertEqual(payload["task_id"], "t-1")
        self.assertEqual(payload["trace_id"], "tr-1")
        self.assertEqual(payload["from_agent"], "audit_agent")

    def test_missing_required_fields_rejected(self):
        for dropped in ("to_agent", "capability", "input"):
            body = {"to_agent": "law_agent", "capability": "retrieve_knowledge", "input": {}}
            body.pop(dropped)
            with self.subTest(dropped=dropped):
                with self.assertRaises(A2ATaskValidationError):
                    A2ATask.from_payload(body)

    def test_wrong_types_rejected(self):
        cases = [
            {"to_agent": 1, "capability": "retrieve_knowledge", "input": {}},
            {"to_agent": "law_agent", "capability": None, "input": {}},
            {"to_agent": "law_agent", "capability": "retrieve_knowledge", "input": []},
            {"to_agent": "law_agent", "capability": "retrieve_knowledge", "input": {}, "timeout": "soon"},
            {"to_agent": "law_agent", "capability": "retrieve_knowledge", "input": {}, "task_id": 5},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(A2ATaskValidationError):
                    A2ATask.from_payload(payload)

    def test_unsupported_schema_version_rejected(self):
        with self.assertRaises(A2ATaskValidationError) as ctx:
            A2ATask.from_payload({
                "schema_version": "9.9",
                "to_agent": "law_agent",
                "capability": "retrieve_knowledge",
                "input": {},
            })
        self.assertIn("schema_version", str(ctx.exception))

    def test_blank_agent_or_capability_rejected(self):
        for field in ("to_agent", "capability"):
            body = {"to_agent": "law_agent", "capability": "retrieve_knowledge", "input": {}}
            body[field] = "   "
            with self.subTest(field=field):
                with self.assertRaises(A2ATaskValidationError):
                    A2ATask.from_payload(body)

    def test_non_mapping_payload_rejected(self):
        for bad in (None, [], "x", 3):
            with self.subTest(bad=bad):
                with self.assertRaises(A2ATaskValidationError):
                    A2ATask.from_payload(bad)


class TestA2AResultProtocol(unittest.TestCase):
    def setUp(self):
        self.task = A2ATask.from_payload({
            "task_id": "t-9", "trace_id": "tr-9", "from_agent": "audit_agent",
            "to_agent": "law_agent", "capability": "retrieve_knowledge", "input": {"query": "违约金"},
        })

    def test_completed_result_shape(self):
        result = A2AResult.completed(self.task, {"items": [{"law": "民法典"}]},
                                     from_agent="law_agent", skill="retrieve_knowledge",
                                     skill_source="ai.rag.vector_store.search_knowledge")
        payload = result.to_payload()
        self.assertEqual(payload["status"], STATUS_COMPLETED)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["task_id"], "t-9")
        self.assertEqual(payload["trace_id"], "tr-9")
        self.assertEqual(payload["from_agent"], "law_agent")
        self.assertEqual(payload["to_agent"], "audit_agent")
        self.assertIn("result", payload)
        self.assertNotIn("error", payload)
        self.assertEqual(payload["skill_source"], "ai.rag.vector_store.search_knowledge")

    def test_failed_result_shape_and_no_traceback_leak(self):
        result = A2AResult.failed(self.task, code=ERR_UNKNOWN_CAPABILITY,
                                  message="不支持该能力", from_agent="law_agent")
        payload = result.to_payload()
        self.assertNotEqual(payload["status"], STATUS_COMPLETED)
        self.assertIn("error", payload)
        self.assertNotIn("result", payload)
        self.assertEqual(payload["error"]["code"], ERR_UNKNOWN_CAPABILITY)
        self.assertNotIn("Traceback", json.dumps(payload, ensure_ascii=False))

    def test_rejected_result_is_distinct_status(self):
        result = A2AResult.rejected(self.task, code="UNAUTHORIZED", message="权限不足")
        payload = result.to_payload()
        self.assertEqual(payload["status"], STATUS_REJECTED)
        self.assertEqual(result.status, STATUS_REJECTED)

    def test_failed_without_task_generates_ids(self):
        result = A2AResult.failed(None, code=ERR_INVALID_TASK, message="坏的 Task")
        payload = result.to_payload()
        self.assertTrue(payload["task_id"])
        self.assertTrue(payload["trace_id"])

    def test_parse_result_payload_roundtrip(self):
        original = A2AResult.completed(self.task, {"items": [], "count": 0},
                                       from_agent="law_agent", skill="retrieve_knowledge")
        restored = parse_result_payload(original.to_payload())
        self.assertEqual(restored.status, STATUS_COMPLETED)
        self.assertEqual(restored.task_id, original.task_id)
        self.assertEqual(restored.skill, "retrieve_knowledge")
        self.assertEqual(restored.to_payload(), original.to_payload())


class TestAgentCard(unittest.TestCase):
    def test_cards_expose_required_fields(self):
        for name in (AUDIT_AGENT, LAW_AGENT):
            card = a2a_agents.get(name)
            self.assertIsNotNone(card, f"{name} 必须已登记")
            data = card.to_dict()
            for field in ("name", "description", "version", "capabilities"):
                self.assertIn(field, data)
            self.assertTrue(data["description"].strip())
            self.assertTrue(data["capabilities"], f"{name} 必须声明至少一个真实能力")
            json.dumps(data, ensure_ascii=False)

    def test_three_agents_registered(self):
        # 第三阶段新增 reviewer_agent（Multi-Agent 的决策点），故注册表为 3 个 Agent。
        self.assertEqual(sorted(a2a_agents.names()), [AUDIT_AGENT, LAW_AGENT, REVIEWER_AGENT])

    def test_law_agent_capability_matches_real_skill(self):
        card = a2a_agents.get(LAW_AGENT)
        self.assertEqual(card.capabilities, (CAP_RETRIEVE_KNOWLEDGE,))
        self.assertTrue(skill_registry.has(CAP_RETRIEVE_KNOWLEDGE),
                        "Agent 声明的能力必须是已注册的真实 Skill")
        self.assertEqual(card.implementation, "skill")

    def test_audit_agent_capability_matches_real_method(self):
        card = a2a_agents.get(AUDIT_AGENT)
        self.assertEqual(card.capabilities, (CAP_REQUEST_LAW_RETRIEVAL,))
        self.assertTrue(callable(getattr(AuditAgent, CAP_REQUEST_LAW_RETRIEVAL, None)),
                        "客户端侧能力必须是 Agent 上真实存在的方法")
        self.assertEqual(card.implementation, "method")
        # 反向：不是 Skill，因此不得谎称 skill-backed
        self.assertFalse(skill_registry.has(CAP_REQUEST_LAW_RETRIEVAL))


# ══════════════════════════════════════════════════════════════════════
# 2. Registry
# ══════════════════════════════════════════════════════════════════════

class TestAgentRegistry(unittest.TestCase):
    def setUp(self):
        self._snapshot = dict(a2a_agents._cards)

    def tearDown(self):
        a2a_agents._cards.clear()
        a2a_agents._cards.update(self._snapshot)

    def test_get_and_has(self):
        self.assertTrue(a2a_agents.has(LAW_AGENT))
        self.assertIsNotNone(a2a_agents.get(LAW_AGENT))
        self.assertFalse(a2a_agents.has("ghost_agent"))
        self.assertIsNone(a2a_agents.get("ghost_agent"))

    def test_list_and_filter_by_capability(self):
        self.assertEqual(len(a2a_agents.list_cards()), 3)   # 第三阶段新增 reviewer_agent
        only_law = a2a_agents.list_cards(capability=CAP_RETRIEVE_KNOWLEDGE)
        self.assertEqual([c.name for c in only_law], [LAW_AGENT])
        self.assertEqual(a2a_agents.list_cards(capability="no_such_cap"), [])

    def test_find_by_capability(self):
        self.assertEqual([c.name for c in a2a_agents.find_by_capability(CAP_RETRIEVE_KNOWLEDGE)],
                         [LAW_AGENT])
        self.assertEqual(a2a_agents.find_by_capability("nope"), [])

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(AgentRegistrationError):
            a2a_agents.register(a2a_agents.get(LAW_AGENT))

    def test_capability_must_be_a_registered_skill(self):
        """核心安全约束：capability 不能是任意 Python 函数名。"""
        with self.assertRaises(AgentRegistrationError) as ctx:
            a2a_agents.register(AgentCard(
                name="evil_agent", description="x",
                capabilities=("os.system",),
            ))
        self.assertIn("不是已注册的 Skill", str(ctx.exception))
        self.assertFalse(a2a_agents.has("evil_agent"))

    def test_method_capability_must_exist_on_owner(self):
        with self.assertRaises(AgentRegistrationError):
            a2a_agents.register(
                AgentCard(name="ghost_method_agent", description="x",
                          capabilities=("not_a_real_method",), implementation="method"),
                owner=AuditAgent,
            )

    def test_card_requires_at_least_one_capability(self):
        with self.assertRaises(AgentRegistrationError):
            a2a_agents.register(AgentCard(name="empty_agent", description="x", capabilities=()))

    def test_empty_name_rejected(self):
        with self.assertRaises(AgentRegistrationError):
            a2a_agents.register(AgentCard(name="  ", description="x",
                                          capabilities=(CAP_RETRIEVE_KNOWLEDGE,)))


# ══════════════════════════════════════════════════════════════════════
# 3. LawAgent 行为（不经 HTTP，直接单元测）
# ══════════════════════════════════════════════════════════════════════

class TestLawAgentHandling(unittest.TestCase):
    """只测"拒绝/权限"等边界；真实检索结果由 E2E 段落证明。"""

    def _law(self):
        return LawAgent()

    def test_undeclared_capability_rejected(self):
        task = A2ATask.from_payload({
            "to_agent": LAW_AGENT, "capability": "compare_clauses",
            "input": {"full_text": "x", "contract_type": "买卖合同"}, "from_agent": AUDIT_AGENT,
        })
        result = self._law().handle_task(task, _make_user("uploader"))
        self.assertNotEqual(result.status, STATUS_COMPLETED)
        self.assertEqual(result.error["code"], ERR_UNKNOWN_CAPABILITY)
        self.assertEqual(result.from_agent, LAW_AGENT)

    def test_unregistered_capability_rejected(self):
        task = A2ATask.from_payload({
            "to_agent": LAW_AGENT, "capability": "no_such_skill", "input": {}, "from_agent": AUDIT_AGENT,
        })
        result = self._law().handle_task(task, _make_user("admin"))
        self.assertEqual(result.error["code"], ERR_UNKNOWN_CAPABILITY)

    def test_permission_enforced_like_skill_api(self):
        """approver 不在 retrieve_knowledge 的 permissions 内 → 拒绝且不执行。"""
        task = A2ATask.from_payload({
            "to_agent": LAW_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE,
            "input": {"query": "违约金"}, "from_agent": AUDIT_AGENT,
        })
        result = self._law().handle_task(task, _make_user("approver"))
        self.assertEqual(result.status, STATUS_REJECTED)
        self.assertEqual(result.error["code"], "UNAUTHORIZED")

    def test_disabled_skill_rejected(self):
        task = A2ATask.from_payload({
            "to_agent": LAW_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE,
            "input": {"query": "违约金"}, "from_agent": AUDIT_AGENT,
        })
        skill = skill_registry.get(CAP_RETRIEVE_KNOWLEDGE)
        original = skill.enabled
        skill.enabled = False
        try:
            result = self._law().handle_task(task, _make_user("uploader"))
            self.assertEqual(result.error["code"], "SKILL_DISABLED")
        finally:
            skill.enabled = original

    def test_bad_input_mapped_to_skill_error_without_traceback(self):
        task = A2ATask.from_payload({
            "to_agent": LAW_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE,
            "input": {}, "from_agent": AUDIT_AGENT,   # 缺 query
        })
        result = self._law().handle_task(task, _make_user("uploader"))
        self.assertNotEqual(result.status, STATUS_COMPLETED)
        self.assertNotIn("Traceback", json.dumps(result.to_payload(), ensure_ascii=False))


class TestTransportSafety(unittest.TestCase):
    """出站 URL 白名单：防止 A2A 变成任意 URL 请求器（SSRF）。"""

    def test_loopback_allowed(self):
        for base in ("http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"):
            with self.subTest(base=base):
                self.assertEqual(validate_task_url(base + A2A_TASK_PATH), base + A2A_TASK_PATH)

    def test_external_host_rejected(self):
        for base in ("http://evil.example.com", "http://169.254.169.254", "http://10.0.0.5"):
            with self.subTest(base=base):
                with self.assertRaises(A2ATransportError):
                    validate_task_url(base + A2A_TASK_PATH)

    def test_wrong_scheme_and_path_rejected(self):
        with self.assertRaises(A2ATransportError):
            validate_task_url("file:///etc/passwd")
        with self.assertRaises(A2ATransportError):
            validate_task_url("http://127.0.0.1:8000/api/skills/rule_scan/invoke")

    def test_transport_constructor_validates(self):
        with self.assertRaises(A2ATransportError):
            HttpA2ATransport("http://evil.example.com")
        with self.assertRaises(A2ATransportError):
            HttpA2ATransport("")

    def test_connection_failure_raises_transport_error(self):
        transport = HttpA2ATransport(f"http://127.0.0.1:{_free_port()}", timeout=2.0)
        task = A2ATask.from_payload({
            "to_agent": LAW_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE,
            "input": {"query": "x"}, "from_agent": AUDIT_AGENT,
        })
        with self.assertRaises(A2ATransportError):
            transport.post_task(task)


# ══════════════════════════════════════════════════════════════════════
# 4. API（TestClient，覆盖状态码矩阵）
# ══════════════════════════════════════════════════════════════════════

class TestA2AApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = FastAPI()
        cls._app.include_router(a2a_api.router, prefix="/api")
        cls._current = {"user": _make_user("uploader")}
        cls._app.dependency_overrides[deps.get_current_user] = lambda: cls._current["user"]
        cls._client = TestClient(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls._app.dependency_overrides.clear()

    def _as(self, role: str):
        self._current["user"] = _make_user(role)

    def test_list_agents_shows_all_agents_with_capabilities(self):
        self._as("admin")
        resp = self._client.get("/api/a2a/agents")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["total"], 3)   # 第三阶段新增 reviewer_agent
        names = [a["name"] for a in data["agents"]]
        self.assertEqual(sorted(names), [AUDIT_AGENT, LAW_AGENT, REVIEWER_AGENT])
        by_name = {a["name"]: a for a in data["agents"]}
        self.assertIn(CAP_RETRIEVE_KNOWLEDGE, by_name[LAW_AGENT]["capabilities"])

    def test_get_agent_card(self):
        self._as("admin")
        resp = self._client.get(f"/api/a2a/agents/{LAW_AGENT}")
        self.assertEqual(resp.status_code, 200, resp.text)
        card = resp.json()["data"]
        self.assertEqual(card["name"], LAW_AGENT)
        self.assertEqual(card["capabilities"], [CAP_RETRIEVE_KNOWLEDGE])
        self.assertEqual(card["transport"], "http")

    def test_unknown_agent_404(self):
        self._as("admin")
        self.assertEqual(self._client.get("/api/a2a/agents/ghost").status_code, 404)

    def test_client_side_agent_cannot_receive_tasks(self):
        """audit_agent 是客户端侧：存在但 404（不接收任务）。"""
        self._as("admin")
        resp = self._client.post("/api/a2a/tasks", json={
            "to_agent": AUDIT_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE, "input": {"query": "x"},
        })
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertIn("客户端侧", json.dumps(resp.json(), ensure_ascii=False))

    def test_missing_fields_400(self):
        self._as("uploader")
        resp = self._client.post("/api/a2a/tasks", json={"to_agent": LAW_AGENT})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_bad_schema_version_400(self):
        self._as("uploader")
        resp = self._client.post("/api/a2a/tasks", json={
            "schema_version": "2.0", "to_agent": LAW_AGENT,
            "capability": CAP_RETRIEVE_KNOWLEDGE, "input": {"query": "x"},
        })
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_illegal_capability_403(self):
        self._as("uploader")
        resp = self._client.post("/api/a2a/tasks", json={
            "to_agent": LAW_AGENT, "capability": "os.system", "input": {"query": "x"},
        })
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_permission_denied_403_not_200(self):
        """被拒绝的任务不得看起来像成功。"""
        self._as("approver")
        resp = self._client.post("/api/a2a/tasks", json={
            "to_agent": LAW_AGENT, "capability": CAP_RETRIEVE_KNOWLEDGE, "input": {"query": "违约金"},
        })
        self.assertEqual(resp.status_code, 403, resp.text)
        payload = resp.json()["detail"]["a2a_result"]
        self.assertEqual(payload["status"], STATUS_REJECTED)
        self.assertEqual(payload["error"]["code"], "UNAUTHORIZED")

    def test_endpoints_require_authentication(self):
        bare = FastAPI()
        bare.include_router(a2a_api.router, prefix="/api")
        client = TestClient(bare)
        for method, path in (("get", "/api/a2a/agents"),
                             ("get", f"/api/a2a/agents/{LAW_AGENT}"),
                             ("post", "/api/a2a/tasks")):
            with self.subTest(path=path):
                call = getattr(client, method)
                resp = call(path, json={}) if method == "post" else call(path)
                self.assertIn(resp.status_code, (401, 403),
                              f"{path} 不应允许匿名访问")


# ══════════════════════════════════════════════════════════════════════
# 5. 真实 E2E：真实 uvicorn + 真实 loopback HTTP
# ══════════════════════════════════════════════════════════════════════

class TestA2ARealHttpEndToEnd(unittest.TestCase):
    """**不 mock Agent A → HTTP → Agent B → Skill Registry 这一段。**

    只把"稠密向量检索"这一外部基础设施替换为返回空（避免加载 2GB 模型），
    使召回路径落到 BM25 —— 它读取的是真实语料 `laws.json`。
    """

    @classmethod
    def setUpClass(cls):
        # 外部基础设施替身：稠密检索返回空 → 唯一召回路径是 BM25（真实 laws.json）
        cls._dense_original = vector_store._dense_search
        vector_store._dense_search = lambda query, collection_name, top_k: []

        from main import app as real_app  # noqa: E402  （真实生产 app，含 /api/skills 与 /api/a2a）

        cls._port = _free_port()
        real_app.dependency_overrides[deps.get_current_user] = lambda: _make_user("uploader")
        cls._app = real_app

        config = uvicorn.Config(
            real_app, host="127.0.0.1", port=cls._port,
            log_level="warning", lifespan="on", access_log=False,
        )
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

        cls._base = f"http://127.0.0.1:{cls._port}"

    @classmethod
    def tearDownClass(cls):
        cls._server.should_exit = True
        cls._thread.join(timeout=20)
        cls._app.dependency_overrides.clear()
        vector_store._dense_search = cls._dense_original

    # ── 步骤 1：看到全部 Agent（第三阶段后为 3 个）──
    def test_step1_list_agents_over_real_http(self):
        resp = httpx.get(f"{self._base}/api/a2a/agents", timeout=30)
        self.assertEqual(resp.status_code, 200, resp.text)
        names = [a["name"] for a in resp.json()["data"]["agents"]]
        self.assertEqual(sorted(names), [AUDIT_AGENT, LAW_AGENT, REVIEWER_AGENT])

    # ── 步骤 2：看到 law_agent 的真实能力 ──
    def test_step2_law_agent_card_over_real_http(self):
        resp = httpx.get(f"{self._base}/api/a2a/agents/{LAW_AGENT}", timeout=30)
        self.assertEqual(resp.status_code, 200, resp.text)
        card = resp.json()["data"]
        self.assertIn(CAP_RETRIEVE_KNOWLEDGE, card["capabilities"])
        self.assertEqual(card["endpoint"], "/api/a2a/tasks")

    # ── 步骤 3 + 4：真实 HTTP 任务 → 真实法规结果 ──
    def test_step3_and_4_real_a2a_task_returns_real_law_hits(self):
        """AuditAgent ──HTTP──▶ LawAgent ──▶ Skill Registry ──▶ retrieve_knowledge ──▶ 真实法条。"""
        resp = httpx.post(
            f"{self._base}/api/a2a/tasks",
            json={
                "to_agent": LAW_AGENT,
                "capability": CAP_RETRIEVE_KNOWLEDGE,
                "input": {"query": "违约金过高 调整", "top_k": 3},
                "from_agent": AUDIT_AGENT,
            },
            timeout=60,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        a2a_result = resp.json()["data"]["a2a_result"]

        # A2A Result 协议字段
        self.assertEqual(a2a_result["schema_version"], SCHEMA_VERSION)
        self.assertEqual(a2a_result["status"], STATUS_COMPLETED)
        self.assertEqual(a2a_result["from_agent"], LAW_AGENT)
        self.assertEqual(a2a_result["to_agent"], AUDIT_AGENT)
        self.assertTrue(a2a_result["task_id"])
        self.assertTrue(a2a_result["trace_id"])

        # A2A → Skill → 真实函数的证据链
        self.assertEqual(a2a_result["skill"], CAP_RETRIEVE_KNOWLEDGE)
        self.assertEqual(a2a_result["skill_source"], "ai.rag.vector_store.search_knowledge")

        # 真实业务结果（来自真实 laws.json 的 BM25 召回）
        items = a2a_result["result"]["items"]
        self.assertEqual(a2a_result["result"]["count"], len(items))
        self.assertGreater(len(items), 0, "必须返回真实的法条检索结果，而不是空壳")
        for item in items:
            self.assertIn("content", item)
            self.assertTrue(item["content"].strip())
        # 语料是真实中国法：命中的条文应带 law/article 字段
        self.assertTrue(any(item.get("law") for item in items), "应命中带 law 字段的真实法条")

    def test_skill_api_still_works_on_same_server(self):
        """Skill API 与 A2A 共存；A2A 没有替换 Skill 层。"""
        resp = httpx.get(f"{self._base}/api/skills", timeout=30)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["total"], 12)

    def test_audit_agent_real_outbound_http_loop(self):
        """完整客户端闭环：AuditAgent 发真实 HTTP → LawAgent → Skill → 回到 AuditAgent。

        `/api/a2a/audit-to-law` 内部由 AuditAgent 用 httpx 反向请求本服务的
        `/api/a2a/tasks` —— 这一跳是**真实 TCP/HTTP**，不是函数调用。
        """
        resp = httpx.post(
            f"{self._base}/api/a2a/audit-to-law",
            json={
                "to_agent": LAW_AGENT,
                "capability": CAP_RETRIEVE_KNOWLEDGE,
                "input": {"query": "不可抗力 免责", "top_k": 2},
            },
            timeout=90,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["initiated_by"], AUDIT_AGENT)
        self.assertEqual(data["transport"], "http")
        # 目标就是本服务的真实 A2A 端点
        self.assertTrue(data["target"].endswith(A2A_TASK_PATH))
        self.assertIn(f"127.0.0.1:{self._port}", data["target"])

        a2a_result = data["a2a_result"]
        self.assertEqual(a2a_result["status"], STATUS_COMPLETED)
        self.assertEqual(a2a_result["from_agent"], LAW_AGENT)
        self.assertEqual(a2a_result["to_agent"], AUDIT_AGENT)
        self.assertGreater(len(a2a_result["result"]["items"]), 0,
                           "AuditAgent 必须拿到 LawAgent 经真实 HTTP 回传的真实检索结果")

    def test_audit_agent_transport_is_http_not_direct_call(self):
        """证明是 HTTP：把目标指向一个关闭的端口，必须得到传输错误而不是正常结果。"""
        transport = HttpA2ATransport(f"http://127.0.0.1:{_free_port()}", timeout=3.0)
        audit = AuditAgent(transport)
        with self.assertRaises(A2ATransportError):
            audit.request_law_retrieval("违约金", top_k=1)


if __name__ == "__main__":
    unittest.main()
