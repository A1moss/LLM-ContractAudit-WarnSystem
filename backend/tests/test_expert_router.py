"""省赛第四阶段：Task-level Expert Router 测试。

本文件要证明的四件事（题面第二十八节）
------------------------------------
① **Router 存在**：Expert Registry + Expert Descriptor + Router
② **Router 真决策**：不同任务 → 不同 Expert（`legal_retrieval` / `contract_analysis` /
   `clause_revision` 三者互不相同，且都不是 `general_expert` 之类的兜底）
③ **Router 真执行**：`Router → Expert → Skill Registry → 现有真实函数 → 真实结果`
④ **原审核链路没被污染**：`_run_audit` 不依赖 router / expert / a2a / multi_agent / skills

并且明确：本轮实现的是 **Task-level Expert Router**，不是 **Model-level MoE**
（有静态断言：本层不得 import `ai.llm_client` 之外无、不得出现 MoE 相关命名）。

运行（backend 目录下）：
    python -m pytest tests/test_expert_router.py -v

安全注意：需要 LLM 的能力一律打桩，**绝不**触发真实 LLM 调用。
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ai.llm_client as llm_client_module  # noqa: E402
import ai.rag.vector_store as vector_store  # noqa: E402
from ai.router.protocol import (  # noqa: E402
    KNOWN_TASK_TYPES,
    REASON_DISABLED,
    REASON_UNKNOWN_TASK_TYPE,
    STATUS_ROUTED,
    STATUS_UNROUTABLE,
    TASK_CLAUSE_REVISION,
    TASK_CONTRACT_ANALYSIS,
    TASK_LEGAL_RETRIEVAL,
    ExpertDescriptor,
    ExpertRegistrationError,
    TaskDescriptor,
    TaskValidationError,
)
from ai.router.registry import expert_registry  # noqa: E402
from ai.router.router import ExpertRouter  # noqa: E402
from ai.skills import registry as skill_registry  # noqa: E402
from api import deps  # noqa: E402
from api import router as router_api  # noqa: E402
from models.user import User  # noqa: E402

EXPECTED_EXPERTS = [
    "legal_retrieval_expert",
    "contract_analysis_expert",
    "clause_revision_expert",
]


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


def _fake_llm_response(prompt: str, temperature: float = 0.1) -> str:
    """通用假 LLM：按 prompt 特征返回可解析 JSON（覆盖 extract/recommend/reviser 路径）。

    签名必须与真实 `llm_client.chat(prompt, temperature=...)` 一致。
    """
    if "合同条款事实抽取助手" in prompt:
        return json.dumps({
            "contract_type": "买卖合同", "is_delivery_type": True,
            "R01_违约金": {"exists": True, "unit": "daily", "rate": 0.005,
                           "clause_text": "乙方逾期付款的，按日千分之五支付违约金"},
            "R08_验收": {"objective_basis": False, "basis_evidence": ""},
        })
    if "法律信息抽取" in prompt or "parties" in prompt:
        return json.dumps({"parties": {"甲方": "A公司", "乙方": "B公司"}, "amount": None,
                           "sign_date": None, "performance_period": None,
                           "dispute_resolution": None, "governing_law": None})
    if "合同分类" in prompt or "contract_type" in prompt:
        return json.dumps({"contract_type": "买卖合同", "is_outsourcing": False,
                           "confidence": 0.9, "reason": "买卖特征明显"})
    return json.dumps({
        "revised_clause": "修订后的条款正文", "changes": ["调整违约金比例"],
        "explanation": "按比例调整", "constraints": ["不超过20%"],
        "legal_basis": ["民法典第585条"], "risk_type": "R01",
        "clause_text": "新增条款正文", "verified": True,
        "remaining_risks": [], "final_advice": "建议采用",
    })


def _patch_llm():
    """把所有持有 llm_client 单例的模块都打桩，杜绝真实外呼。"""
    import contextlib

    import ai.classifier.classifier as classifier_mod
    import ai.classifier.rag_classifier as rag_classifier_mod
    import ai.extractor.extractor as extractor_mod
    import ai.matcher.matcher as matcher_mod
    import ai.reviser as reviser_mod
    from ai.auditor import evidence_adjudicator  # noqa: F401  (确保模块已加载)
    from ai.auditor import evidence_extractor
    from ai.auditor import recommendation_engine

    fake = _fake_llm_response
    stack = contextlib.ExitStack()
    for module in (llm_client_module, reviser_mod, evidence_extractor, recommendation_engine,
                   classifier_mod, rag_classifier_mod, extractor_mod, matcher_mod):
        stack.enter_context(
            unittest.mock.patch.object(module.llm_client, "chat", side_effect=fake)
        )
    return stack


# ══════════════════════════════════════════════════════════════════════
# 1. Protocol
# ══════════════════════════════════════════════════════════════════════

class TestTaskDescriptor(unittest.TestCase):
    def test_minimal_valid_task(self):
        task = TaskDescriptor.from_payload({"task_type": TASK_LEGAL_RETRIEVAL})
        self.assertEqual(task.task_type, TASK_LEGAL_RETRIEVAL)
        self.assertEqual(task.query, "")
        self.assertEqual(task.context, {})

    def test_full_task(self):
        task = TaskDescriptor.from_payload({
            "task_type": TASK_CONTRACT_ANALYSIS,
            "query": "分析这份合同",
            "contract_type": "买卖合同",
            "capability": "rule_scan",
            "context": {"full_text": "第一条 ..."},
        })
        self.assertEqual(task.capability, "rule_scan")
        self.assertEqual(task.context["full_text"], "第一条 ...")
        payload = task.to_payload()
        for field in ("task_type", "query", "contract_type", "capability", "context"):
            self.assertIn(field, payload)

    def test_missing_task_type_rejected(self):
        for bad in ({}, {"task_type": ""}, {"task_type": "   "}, {"task_type": None}):
            with self.subTest(payload=bad):
                with self.assertRaises(TaskValidationError):
                    TaskDescriptor.from_payload(bad)

    def test_wrong_types_rejected(self):
        cases = [
            {"task_type": 1},
            {"task_type": "x", "query": 5},
            {"task_type": "x", "contract_type": []},
            {"task_type": "x", "capability": 3},
            {"task_type": "x", "context": []},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(TaskValidationError):
                    TaskDescriptor.from_payload(payload)

    def test_non_mapping_rejected(self):
        for bad in (None, [], "x", 3):
            with self.subTest(bad=bad):
                with self.assertRaises(TaskValidationError):
                    TaskDescriptor.from_payload(bad)

    def test_required_fields_declared(self):
        self.assertEqual(TaskDescriptor.REQUIRED_FIELDS, ("task_type",))


class TestExpertDescriptor(unittest.TestCase):
    def test_primary_capability(self):
        expert = ExpertDescriptor(name="e", description="d",
                                  capabilities=("a", "b"), task_types=(TASK_LEGAL_RETRIEVAL,))
        self.assertEqual(expert.primary_capability(), "a")
        self.assertEqual(expert.primary_capability("b"), "b")
        # 请求了不属于该 Expert 的能力 → 回退到主能力（由 Router 记录 detail）
        self.assertEqual(expert.primary_capability("zzz"), "a")

    def test_supports_task(self):
        expert = ExpertDescriptor(name="e", description="d",
                                  capabilities=("a",), task_types=(TASK_LEGAL_RETRIEVAL,))
        self.assertTrue(expert.supports_task(TASK_LEGAL_RETRIEVAL))
        self.assertFalse(expert.supports_task(TASK_CONTRACT_ANALYSIS))

    def test_to_dict_schema(self):
        expert = ExpertDescriptor(name="e", description="d", capabilities=("retrieve_knowledge",),
                                  task_types=(TASK_LEGAL_RETRIEVAL,), priority=7, enabled=False)
        data = expert.to_dict()
        for field in ("name", "description", "capabilities", "task_types",
                      "priority", "enabled", "version", "tags"):
            self.assertIn(field, data)
        self.assertEqual(data["priority"], 7)
        self.assertFalse(data["enabled"])
        json.dumps(data, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# 2. Registry
# ══════════════════════════════════════════════════════════════════════

class TestExpertRegistry(unittest.TestCase):
    def setUp(self):
        self._snapshot = dict(expert_registry._experts)

    def tearDown(self):
        expert_registry._experts.clear()
        expert_registry._experts.update(self._snapshot)

    def test_three_experts_registered(self):
        self.assertEqual(expert_registry.names(), EXPECTED_EXPERTS)

    def test_get_and_has(self):
        self.assertTrue(expert_registry.has("legal_retrieval_expert"))
        self.assertIsNotNone(expert_registry.get("legal_retrieval_expert"))
        self.assertFalse(expert_registry.has("ghost_expert"))
        self.assertIsNone(expert_registry.get("ghost_expert"))

    def test_all_capabilities_are_real_skills(self):
        """核心约束：Expert 只能聚合**已注册的真实 Skill**。"""
        for expert in expert_registry.list_experts():
            with self.subTest(expert=expert.name):
                self.assertTrue(expert.capabilities)
                for capability in expert.capabilities:
                    self.assertTrue(skill_registry.has(capability),
                                    f"{expert.name} 的能力 {capability} 不是已注册 Skill")

    def test_experts_are_not_simply_renamed_skills(self):
        """Expert ≠ Skill 改名：至少两个 Expert 聚合了多个能力。"""
        multi = [e.name for e in expert_registry.list_experts() if len(e.capabilities) > 1]
        self.assertGreaterEqual(len(multi), 3 - 1, "Expert 必须是能力路径组合，而非单个 Skill 的别名")
        # 且 Expert 名与任何 Skill 名都不相同
        for expert in expert_registry.list_experts():
            self.assertNotIn(expert.name, skill_registry.names())

    def test_duplicate_registration_rejected(self):
        existing = expert_registry.get("legal_retrieval_expert")
        with self.assertRaises(ExpertRegistrationError):
            expert_registry.register(existing)

    def test_unknown_capability_rejected(self):
        with self.assertRaises(ExpertRegistrationError) as ctx:
            expert_registry.register(ExpertDescriptor(
                name="evil_expert", description="x",
                capabilities=("os.system",), task_types=(TASK_LEGAL_RETRIEVAL,)))
        self.assertIn("不是已注册的 Skill", str(ctx.exception))
        self.assertFalse(expert_registry.has("evil_expert"))

    def test_unknown_task_type_rejected(self):
        with self.assertRaises(ExpertRegistrationError):
            expert_registry.register(ExpertDescriptor(
                name="typo_expert", description="x",
                capabilities=("retrieve_knowledge",), task_types=("legal_retrival",)))

    def test_empty_capabilities_or_task_types_rejected(self):
        with self.assertRaises(ExpertRegistrationError):
            expert_registry.register(ExpertDescriptor(
                name="no_caps", description="x", capabilities=(),
                task_types=(TASK_LEGAL_RETRIEVAL,)))
        with self.assertRaises(ExpertRegistrationError):
            expert_registry.register(ExpertDescriptor(
                name="no_tasks", description="x",
                capabilities=("retrieve_knowledge",), task_types=()))

    def test_empty_name_rejected(self):
        with self.assertRaises(ExpertRegistrationError):
            expert_registry.register(ExpertDescriptor(
                name="  ", description="x",
                capabilities=("retrieve_knowledge",), task_types=(TASK_LEGAL_RETRIEVAL,)))

    def test_describe_and_filters(self):
        self.assertEqual(len(expert_registry.describe()), 3)
        only_rev = expert_registry.describe(task_type=TASK_CLAUSE_REVISION)
        self.assertEqual([e["name"] for e in only_rev], ["clause_revision_expert"])
        self.assertEqual(expert_registry.describe(task_type="nope"), [])


# ══════════════════════════════════════════════════════════════════════
# 3. Router（路由决策）
# ══════════════════════════════════════════════════════════════════════

class TestRouterDecisions(unittest.TestCase):
    def setUp(self):
        self._snapshot = dict(expert_registry._experts)
        self.router = ExpertRouter()

    def tearDown(self):
        expert_registry._experts.clear()
        expert_registry._experts.update(self._snapshot)

    def test_three_tasks_route_to_three_distinct_experts(self):
        """★ 核心断言：不同任务**真的**走不同 Expert（不是都落到一个兜底 Expert）。"""
        mapping = {
            TASK_LEGAL_RETRIEVAL: "legal_retrieval_expert",
            TASK_CONTRACT_ANALYSIS: "contract_analysis_expert",
            TASK_CLAUSE_REVISION: "clause_revision_expert",
        }
        got = {}
        for task_type, expected_expert in mapping.items():
            decision = self.router.route(TaskDescriptor(task_type=task_type, query="违约金"))
            self.assertEqual(decision.status, STATUS_ROUTED, task_type)
            self.assertEqual(decision.expert, expected_expert, task_type)
            got[task_type] = decision.expert

        self.assertEqual(len(set(got.values())), 3, "三个任务必须得到三个不同的 Expert")
        self.assertNotIn("general_expert", set(got.values()), "不得存在兜底 Expert")

    def test_capability_differs_per_task(self):
        caps = {}
        for task_type in KNOWN_TASK_TYPES:
            d = self.router.route(TaskDescriptor(task_type=task_type, query="x"))
            caps[task_type] = d.capability
            self.assertTrue(d.capability)
            self.assertTrue(skill_registry.has(d.capability),
                            "选中的能力必须是已注册 Skill")
        self.assertEqual(len(set(caps.values())), 3)
        self.assertEqual(caps[TASK_LEGAL_RETRIEVAL], "retrieve_knowledge")
        self.assertEqual(caps[TASK_CLAUSE_REVISION], "revise_clause")

    def test_explicit_capability_within_expert(self):
        d = self.router.route(TaskDescriptor(
            task_type=TASK_CLAUSE_REVISION, query="补充不可抗力条款", capability="draft_clause"))
        self.assertEqual(d.status, STATUS_ROUTED)
        self.assertEqual(d.capability, "draft_clause")
        self.assertIn("draft_clause", d.capabilities)

    def test_capability_outside_expert_falls_back_with_detail(self):
        d = self.router.route(TaskDescriptor(
            task_type=TASK_LEGAL_RETRIEVAL, query="x", capability="revise_clause"))
        self.assertEqual(d.status, STATUS_ROUTED)
        self.assertEqual(d.capability, "retrieve_knowledge", "不属于该 Expert 的能力应回退到主能力")
        self.assertIn("不属于", d.detail)

    def test_unknown_task_is_unroutable_no_match(self):
        for bad in ("unknown_task", "legal_retrival", "", "LEGAL_RETRIEVAL"):
            with self.subTest(task_type=bad):
                d = self.router.route(TaskDescriptor(task_type=bad, query="x"))
                self.assertEqual(d.status, STATUS_UNROUTABLE)
                self.assertEqual(d.reason, REASON_UNKNOWN_TASK_TYPE)
                self.assertEqual(d.expert, "", "未知任务绝不随便选 Expert")
                self.assertIn("没有匹配的 Expert", d.detail)

    def test_unknown_task_never_falls_back_to_an_expert(self):
        d = self.router.route(TaskDescriptor(task_type="unknown_task"))
        self.assertEqual(d.capability, "")
        self.assertEqual(d.capabilities, ())

    def test_disabled_expert_not_routable(self):
        expert_registry.get("clause_revision_expert").enabled = False
        d = self.router.route(TaskDescriptor(task_type=TASK_CLAUSE_REVISION, query="x"))
        self.assertEqual(d.status, STATUS_UNROUTABLE)
        self.assertEqual(d.reason, REASON_DISABLED)
        self.assertEqual(d.expert, "")

        # 其它 Expert 不受影响
        ok = self.router.route(TaskDescriptor(task_type=TASK_LEGAL_RETRIEVAL, query="x"))
        self.assertEqual(ok.status, STATUS_ROUTED)

    def test_enabled_expert_routable_after_reenable(self):
        expert = expert_registry.get("clause_revision_expert")
        expert.enabled = False
        self.assertEqual(self.router.route(TaskDescriptor(task_type=TASK_CLAUSE_REVISION)).status,
                         STATUS_UNROUTABLE)
        expert.enabled = True
        self.assertEqual(self.router.route(TaskDescriptor(task_type=TASK_CLAUSE_REVISION)).status,
                         STATUS_ROUTED)

    def test_priority_wins(self):
        """多命中时 priority 高者胜（确定性）。"""
        expert_registry.register(ExpertDescriptor(
            name="low_priority_legal_expert", description="低优先级",
            capabilities=("retrieve_templates",), task_types=(TASK_LEGAL_RETRIEVAL,),
            priority=10))
        d = self.router.route(TaskDescriptor(task_type=TASK_LEGAL_RETRIEVAL, query="x"))
        self.assertEqual(d.expert, "legal_retrieval_expert")   # priority=100
        self.assertIn("low_priority_legal_expert", d.candidates)
        self.assertEqual(d.candidates[0], "legal_retrieval_expert")
        # 候选按确定性顺序：先是高优先级
        self.assertLess(d.candidates.index("legal_retrieval_expert"),
                        d.candidates.index("low_priority_legal_expert"))

    def test_tie_broken_by_registration_order(self):
        expert_registry.register(ExpertDescriptor(
            name="tie_expert", description="同优先级", priority=100,
            capabilities=("retrieve_templates",), task_types=(TASK_LEGAL_RETRIEVAL,)))
        d = self.router.route(TaskDescriptor(task_type=TASK_LEGAL_RETRIEVAL, query="x"))
        # 同优先级 → 先注册的 legal_retrieval_expert 胜
        self.assertEqual(d.expert, "legal_retrieval_expert")

    def test_routing_is_deterministic(self):
        """相同输入 + 相同注册状态 ⇒ 相同结果（重复 20 次断言完全一致）。"""
        task = TaskDescriptor(task_type=TASK_CONTRACT_ANALYSIS, query="分析", contract_type="买卖合同")
        baseline = self.router.route(task).to_dict()
        for _ in range(20):
            self.assertEqual(self.router.route(task).to_dict(), baseline)

    def test_router_layer_does_not_import_business_modules(self):
        """Router 层只负责路由：不得 import 业务实现（防止绕过 Skill Registry）。"""
        for filename in ("router.py", "registry.py", "protocol.py"):
            source = (_BACKEND_DIR / "ai" / "router" / filename).read_text(encoding="utf-8")
            import_lines = "\n".join(
                line.strip() for line in source.splitlines()
                if line.strip().startswith(("import ", "from "))
            )
            for forbidden in ("ai.rag", "ai.auditor", "ai.reviser", "ai.matcher",
                              "ai.extractor", "ai.parser"):
                self.assertNotIn(forbidden, import_lines,
                                 f"ai/router/{filename} 不得 import {forbidden}")
            # 也不得直接调用业务函数
            for call_shape in ("search_knowledge(", "run_rules(", "revise_clause(",
                               "adjudicate_risks(", "extract_elements("):
                self.assertNotIn(call_shape, source,
                                 f"ai/router/{filename} 不得直接调用 {call_shape}")

    def test_router_layer_has_no_model_routing(self):
        """本轮不得做模型级路由：不切换模型、代码中不出现 MoE 相关命名。

        只检查**可执行代码**（排除 docstring/注释）—— 文档里出现"不是 MoE"这类
        澄清说明是必要的，不应被误判。
        """
        import io
        import tokenize

        for filename in ("router.py", "registry.py", "protocol.py"):
            path = _BACKEND_DIR / "ai" / "router" / filename
            source = path.read_text(encoding="utf-8")
            import_lines = "\n".join(
                line.strip() for line in source.splitlines()
                if line.strip().startswith(("import ", "from "))
            )
            self.assertNotIn("ai.llm_client", import_lines,
                             "Router 层不得依赖 LLM 客户端（本轮不做模型路由）")

            # 去掉注释与字符串字面量后，代码中不得出现模型级路由/MoE 命名
            code_only = []
            with tokenize.open(path) as handle:
                for token in tokenize.generate_tokens(handle.readline):
                    if token.type in (tokenize.COMMENT, tokenize.STRING):
                        continue
                    code_only.append(token.string)
            code = " ".join(code_only)
            for word in ("TASK_MODEL_MAP", "chat_model", "deepseek-chat",
                         "Mixture", "MoE", "Neural"):
                self.assertNotIn(word, code,
                                 f"ai/router/{filename} 代码中不得出现模型级路由/MoE 命名：{word}")


# ══════════════════════════════════════════════════════════════════════
# 4. Execution（Router → Expert → Skill Registry → 真实函数）
# ══════════════════════════════════════════════════════════════════════

class TestRouterExecution(unittest.TestCase):
    """直接测执行层（不经过 HTTP）。只打桩外部检索 / LLM。"""

    @classmethod
    def setUpClass(cls):
        cls._orig_dense = vector_store._dense_search
        vector_store._dense_search = lambda q, c, k: []   # 只留 BM25（真实 laws.json）
        cls._app = FastAPI()
        cls._app.include_router(router_api.router, prefix="/api")
        cls._current = {"user": _make_user("uploader")}
        cls._app.dependency_overrides[deps.get_current_user] = lambda: cls._current["user"]
        cls._client = TestClient(cls._app)

    @classmethod
    def tearDownClass(cls):
        vector_store._dense_search = cls._orig_dense
        cls._app.dependency_overrides.clear()

    def _as(self, role):
        self._current["user"] = _make_user(role)

    def test_legal_retrieval_executes_real_skill(self):
        """Case A：Router → Expert → retrieve_knowledge → 真实 laws.json。"""
        self._as("uploader")
        resp = self._client.post("/api/router/execute",
                                 json={"task_type": TASK_LEGAL_RETRIEVAL,
                                       "query": "违约金过高 调整",
                                       "context": {"top_k": 3}})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["expert"], "legal_retrieval_expert")
        self.assertEqual(data["capability"], "retrieve_knowledge")
        self.assertEqual(data["executed"], ["retrieve_knowledge"])

        entry = data["results"][0]
        self.assertEqual(entry["capability"], "retrieve_knowledge")
        self.assertEqual(entry["source"], "ai.rag.vector_store.search_knowledge")
        self.assertFalse(entry["requires_llm"])
        items = entry["result"]
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0, "必须返回真实法规结果")
        self.assertTrue(any(it.get("law") for it in items), "应命中带 law 字段的真实法条")

    def test_contract_type_reaches_multi_capability_chain(self):
        """Case C：contract_analysis 的最小能力链（4 个真实能力全跑，不复制 _run_audit）。"""
        self._as("uploader")
        full_text = "第一条 乙方逾期付款的，按日千分之五支付违约金。第二条 合同总价十万元。"
        with _patch_llm():
            resp = self._client.post(
                "/api/router/execute",
                json={"task_type": TASK_CONTRACT_ANALYSIS, "query": "分析合同",
                      "contract_type": "买卖合同",
                      "context": {"full_text": full_text}, "all_capabilities": True},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["expert"], "contract_analysis_expert")
        self.assertEqual(data["executed"],
                         ["extract_elements", "rule_scan", "extract_evidence", "adjudicate_risks"])

        by_cap = {r["capability"]: r for r in data["results"]}
        # 每个能力都来自真实 Skill source
        self.assertEqual(by_cap["rule_scan"]["source"], "ai.auditor.rule_engine.run_rules")
        self.assertEqual(by_cap["adjudicate_risks"]["source"],
                         "ai.auditor.evidence_adjudicator.adjudicate_risks")
        self.assertEqual(by_cap["extract_evidence"]["source"],
                         "ai.auditor.evidence_extractor.extract_evidence_detailed")
        self.assertEqual(by_cap["extract_elements"]["source"],
                         "ai.extractor.extractor.extract_elements")

        # 两个确定性能力给出**真实**结果（分工正是二者存在的意义）：
        # * rule_scan 是正则召回：本例命中"缺失型"的 R08/R09；
        # * adjudicate_risks 是硬阈值裁决：能识别"按日千分之五"（rate=0.005 ≥ 5‰）→ R01。
        scan_types = {r["risk_type"] for r in by_cap["rule_scan"]["result"]}
        self.assertTrue(scan_types, "rule_scan 必须产出真实召回结果")
        adj_types = {r["risk_type"] for r in by_cap["adjudicate_risks"]["result"]}
        self.assertIn("R01", adj_types,
                      "确定性裁决应识别日违约金 5‰（rule_scan 的正则只认百分号写法，故这里靠裁决兜住）")
        # 裁决的入参确实来自抽取能力（能力链真实串联，而不是各自跑空输入）
        self.assertIsInstance(by_cap["extract_evidence"]["result"], dict)
        self.assertIn("evidence", by_cap["extract_evidence"]["result"])

    def test_clause_revision_executes_with_llm_stub(self):
        """Case B：clause_revision → revise_clause（LLM 打桩，链路真实）。"""
        self._as("uploader")
        with _patch_llm():
            resp = self._client.post(
                "/api/router/execute",
                json={"task_type": TASK_CLAUSE_REVISION, "query": "把违约金降到合理水平",
                      "contract_type": "买卖合同",
                      "context": {"clause_text": "乙方逾期付款的，按日千分之五支付违约金。"}},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["expert"], "clause_revision_expert")
        self.assertEqual(data["capability"], "revise_clause")
        entry = data["results"][0]
        self.assertEqual(entry["source"], "ai.reviser.revise_clause")
        self.assertTrue(entry["requires_llm"])
        self.assertIn("revised_clause", entry["result"])

    def test_draft_capability_selected_explicitly(self):
        self._as("uploader")
        with _patch_llm():
            resp = self._client.post(
                "/api/router/execute",
                json={"task_type": TASK_CLAUSE_REVISION, "query": "补充不可抗力条款",
                      "capability": "draft_clause"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["capability"], "draft_clause")
        self.assertEqual(data["results"][0]["source"], "ai.reviser.generate_clause")

    def test_unknown_task_never_executes(self):
        self._as("uploader")
        resp = self._client.post("/api/router/execute", json={"task_type": "unknown_task"})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_disabled_expert_never_executes(self):
        self._as("uploader")
        expert = expert_registry.get("legal_retrieval_expert")
        original = expert.enabled
        expert.enabled = False
        try:
            resp = self._client.post("/api/router/execute",
                                     json={"task_type": TASK_LEGAL_RETRIEVAL, "query": "违约金"})
            self.assertEqual(resp.status_code, 400, resp.text)
            self.assertEqual(resp.json()["detail"]["reason"], REASON_DISABLED)
        finally:
            expert.enabled = original


# ══════════════════════════════════════════════════════════════════════
# 5. API（TestClient）
# ══════════════════════════════════════════════════════════════════════

class TestRouterApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_dense = vector_store._dense_search
        vector_store._dense_search = lambda q, c, k: []
        cls._app = FastAPI()
        cls._app.include_router(router_api.router, prefix="/api")
        cls._current = {"user": _make_user("uploader")}
        cls._app.dependency_overrides[deps.get_current_user] = lambda: cls._current["user"]
        cls._client = TestClient(cls._app)

    @classmethod
    def tearDownClass(cls):
        vector_store._dense_search = cls._orig_dense
        cls._app.dependency_overrides.clear()

    def _as(self, role):
        self._current["user"] = _make_user(role)

    def test_experts_endpoint(self):
        self._as("admin")
        resp = self._client.get("/api/router/experts")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["total"], 3)
        names = [e["name"] for e in data["experts"]]
        self.assertEqual(names, EXPECTED_EXPERTS)
        for expert in data["experts"]:
            self.assertTrue(expert["capabilities"])
            self.assertTrue(expert["task_types"])

    def test_endpoints_require_authentication(self):
        bare = FastAPI()
        bare.include_router(router_api.router, prefix="/api")
        client = TestClient(bare)
        for method, path in (("get", "/api/router/experts"),
                             ("post", "/api/router/route"),
                             ("post", "/api/router/execute")):
            with self.subTest(path=path):
                call = getattr(client, method)
                resp = call(path, json={"task_type": TASK_LEGAL_RETRIEVAL}) if method == "post" else call(path)
                self.assertIn(resp.status_code, (401, 403))

    def test_route_returns_routed_for_three_tasks(self):
        self._as("uploader")
        for task_type, expert in ((TASK_LEGAL_RETRIEVAL, "legal_retrieval_expert"),
                                  (TASK_CONTRACT_ANALYSIS, "contract_analysis_expert"),
                                  (TASK_CLAUSE_REVISION, "clause_revision_expert")):
            with self.subTest(task_type=task_type):
                resp = self._client.post("/api/router/route",
                                         json={"task_type": task_type, "query": "x"})
                self.assertEqual(resp.status_code, 200, resp.text)
                data = resp.json()["data"]
                self.assertEqual(data["status"], STATUS_ROUTED)
                self.assertEqual(data["expert"], expert)
                self.assertEqual(data["task_type"], task_type)
                self.assertIn(data["capability"], data["capabilities"])

    def test_route_is_200_even_when_unroutable(self):
        """不可路由是"路由决策的一种结果"，`/route` 用 200 + status 表达。"""
        self._as("uploader")
        resp = self._client.post("/api/router/route", json={"task_type": "unknown_task"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["status"], STATUS_UNROUTABLE)
        self.assertEqual(data["reason"], REASON_UNKNOWN_TASK_TYPE)

    def test_route_missing_task_type_400(self):
        self._as("uploader")
        resp = self._client.post("/api/router/route", json={})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_route_bad_type_400(self):
        self._as("uploader")
        # task_type 类型错：由 TaskDescriptor 统一校验 → 400（而非 Pydantic 的 422）
        for bad in (123, [], {}, True):
            with self.subTest(task_type=bad):
                resp = self._client.post("/api/router/route", json={"task_type": bad})
                self.assertEqual(resp.status_code, 400, resp.text)

    def test_execute_unknown_task_400_with_known_types(self):
        self._as("uploader")
        resp = self._client.post("/api/router/execute", json={"task_type": "nope"})
        self.assertEqual(resp.status_code, 400, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["status"], STATUS_UNROUTABLE)
        self.assertEqual(sorted(detail["known_task_types"]), sorted(KNOWN_TASK_TYPES))

    def test_execute_skill_permission_denied_403(self):
        """approver 不在 retrieve_knowledge 的 permissions 内 → 403（不得伪装成功）。"""
        self._as("approver")
        resp = self._client.post("/api/router/execute",
                                 json={"task_type": TASK_LEGAL_RETRIEVAL, "query": "违约金"})
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_execute_disabled_skill_409(self):
        self._as("uploader")
        skill = skill_registry.get("retrieve_knowledge")
        original = skill.enabled
        skill.enabled = False
        try:
            resp = self._client.post("/api/router/execute",
                                     json={"task_type": TASK_LEGAL_RETRIEVAL, "query": "违约金"})
            self.assertEqual(resp.status_code, 409, resp.text)
        finally:
            skill.enabled = original

    def test_execute_llm_capability_without_key_400(self):
        """需要 LLM 的能力在没有 Key 时必须明确 400，不绕过 Key 检查。"""
        self._as("uploader")
        original = llm_client_module.DEFAULT_API_KEY
        llm_client_module.DEFAULT_API_KEY = ""
        try:
            resp = self._client.post("/api/router/execute",
                                     json={"task_type": TASK_CLAUSE_REVISION,
                                           "query": "改违约金",
                                           "context": {"clause_text": "乙方应按日千分之五支付违约金。"}})
            self.assertEqual(resp.status_code, 400, resp.text)
            self.assertIn("DeepSeek", resp.text)
        finally:
            llm_client_module.DEFAULT_API_KEY = original

    def test_execute_skill_error_maps_to_502(self):
        """底层真实失败必须可见（502），不伪装成功。"""
        self._as("uploader")
        skill = skill_registry.get("retrieve_knowledge")
        original = skill.handler

        def _boom(payload):
            raise RuntimeError("底层炸了")

        skill.handler = _boom
        try:
            resp = self._client.post("/api/router/execute",
                                     json={"task_type": TASK_LEGAL_RETRIEVAL, "query": "违约金"})
            self.assertEqual(resp.status_code, 502, resp.text)
            self.assertNotIn("Traceback", resp.text)
        finally:
            skill.handler = original

    def test_execute_bad_skill_input_400(self):
        self._as("uploader")
        resp = self._client.post("/api/router/execute",
                                 json={"task_type": TASK_LEGAL_RETRIEVAL, "query": ""})
        self.assertEqual(resp.status_code, 400, resp.text)


# ══════════════════════════════════════════════════════════════════════
# 6. 真实 E2E：真实 uvicorn + 真实 socket + 真实 HTTP
# ══════════════════════════════════════════════════════════════════════

class TestRouterRealHttpEndToEnd(unittest.TestCase):
    """只打桩外部检索（Chroma 稠密那一路），其余全部真实。"""

    @classmethod
    def setUpClass(cls):
        from main import app as real_app  # noqa: E402

        cls._orig_dense = vector_store._dense_search
        vector_store._dense_search = lambda q, c, k: []

        cls._port = _free_port()
        cls._base = f"http://127.0.0.1:{cls._port}"
        real_app.dependency_overrides[deps.get_current_user] = lambda: _make_user("uploader")
        cls._app = real_app

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
        vector_store._dense_search = cls._orig_dense

    def test_experts_listed_over_real_http(self):
        resp = httpx.get(f"{self._base}/api/router/experts", timeout=30)
        self.assertEqual(resp.status_code, 200, resp.text)
        names = [e["name"] for e in resp.json()["data"]["experts"]]
        self.assertEqual(names, EXPECTED_EXPERTS)

    def test_route_over_real_http_three_distinct_experts(self):
        experts = set()
        for task_type in KNOWN_TASK_TYPES:
            resp = httpx.post(f"{self._base}/api/router/route",
                              json={"task_type": task_type, "query": "违约金过高如何调整"},
                              timeout=30)
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()["data"]
            self.assertEqual(data["status"], STATUS_ROUTED)
            experts.add(data["expert"])
        self.assertEqual(len(experts), 3, "三个任务必须路由到三个不同 Expert")

    def test_execute_over_real_http_returns_real_law_data(self):
        """★ 完整真实链路：HTTP → Router → Expert → Skill Registry → 真实函数 → 真实结果。"""
        resp = httpx.post(f"{self._base}/api/router/execute",
                          json={"task_type": TASK_LEGAL_RETRIEVAL,
                                "query": "违约金过高 调整",
                                "context": {"top_k": 3}},
                          timeout=90)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]

        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["task_type"], TASK_LEGAL_RETRIEVAL)
        self.assertEqual(data["expert"], "legal_retrieval_expert")
        self.assertEqual(data["capability"], "retrieve_knowledge")

        entry = data["results"][0]
        self.assertEqual(entry["source"], "ai.rag.vector_store.search_knowledge")
        items = entry["result"]
        self.assertGreater(len(items), 0, "必须返回真实法规结果")
        self.assertTrue(any(it.get("law") for it in items))
        # 真实语料特征：应含中国法名称与条文号
        self.assertTrue(any("民法典" in str(it.get("law", "")) or
                            "司法解释" in str(it.get("law", "")) for it in items),
                        f"应为真实中国法条命中：{items[:1]}")

    def test_contract_analysis_over_real_http(self):
        full_text = "第一条 乙方逾期付款的，按日千分之五支付违约金。第二条 合同总价十万元。"
        with _patch_llm():
            resp = httpx.post(f"{self._base}/api/router/execute",
                              json={"task_type": TASK_CONTRACT_ANALYSIS, "query": "分析合同",
                                    "contract_type": "买卖合同",
                                    "context": {"full_text": full_text},
                                    "all_capabilities": True},
                              timeout=120)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["expert"], "contract_analysis_expert")
        self.assertEqual(len(data["executed"]), 4)

    def test_unknown_task_over_real_http_400(self):
        resp = httpx.post(f"{self._base}/api/router/execute",
                          json={"task_type": "unknown_task"}, timeout=30)
        self.assertEqual(resp.status_code, 400)

    def test_previous_phases_still_work(self):
        """前三阶段产物未被本阶段破坏。"""
        self.assertEqual(httpx.get(f"{self._base}/api/skills", timeout=30).json()["data"]["total"], 12)
        self.assertEqual(len(httpx.get(f"{self._base}/api/a2a/agents", timeout=30)
                             .json()["data"]["agents"]), 3)


if __name__ == "__main__":
    unittest.main()
