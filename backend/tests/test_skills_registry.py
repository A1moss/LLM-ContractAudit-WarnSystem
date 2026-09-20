"""省赛第一阶段：Skill Registry 独立测试。

覆盖（题面第十、十一节要求）：
* registry 注册 / 查询 / 枚举
* 重复注册处理、不存在 Skill、disabled Skill
* schema 返回
* 12 个 adapter 可解析（且**逐一转发到真实底层函数**，不是重新实现）
* 统一执行：输入校验、真实 handler 调用、异常传播
* 边界回归：`_run_audit` 未改、`extract_evidence` 默认 prompt 未改、
  `ai.skills` 不 import 被禁止注册的模块、parse_document 的路径收窄
* API：GET /api/skills、GET /api/skills/{name}、POST /api/skills/{name}/invoke
  （含 404 / 403 / 409 / 400 与"非 LLM Skill 不需要 Key"）

运行（backend 目录下）：
    python -m pytest tests/test_skills_registry.py -v

安全注意：本文件**绝不**触发真实 LLM 调用。项目根 .env 中配置了真实
DEEPSEEK_API_KEY，因此凡会走到 LLM 的 Skill 都通过 `_fake_llm()` 打桩，
并在 setUp 中把默认 Key 置空，杜绝任何外呼。
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

# ── 被测对象 ──
import ai.skills as skills_pkg  # noqa: E402
from ai.skills import registry  # noqa: E402
from ai.skills.protocol import (  # noqa: E402
    CATEGORIES,
    Skill,
    SkillAlreadyRegisteredError,
    SkillDisabledError,
    SkillInputError,
    SkillNotFoundError,
    coerce_int,
    make_skill,
    validate_against_schema,
)

# ── 真实底层函数（用于"确认适配器确实转发到它们"）──
import ai.llm_client as llm_client_module  # noqa: E402
import ai.reviser as reviser  # noqa: E402
from ai.auditor import evidence_adjudicator, rule_engine  # noqa: E402
from ai.auditor import evidence_extractor  # noqa: E402
from ai.extractor import extractor  # noqa: E402
from ai.matcher import matcher  # noqa: E402

# 12 个 Skill 的确切名单（题面第二节；不得增删改名）
EXPECTED_SKILLS = [
    "parse_document",
    "classify_contract",
    "extract_elements",
    "rule_scan",
    "extract_evidence",
    "adjudicate_risks",
    "build_recommendations",
    "compare_clauses",
    "retrieve_knowledge",
    "retrieve_templates",
    "revise_clause",
    "draft_clause",
]

SKILL_SOURCE = {
    "parse_document": "ai.parser.detect_and_parse",
    "classify_contract": "ai.classifier.rag_classifier.classify_by_rag",
    "extract_elements": "ai.extractor.extractor.extract_elements",
    "rule_scan": "ai.auditor.rule_engine.run_rules",
    "extract_evidence": "ai.auditor.evidence_extractor.extract_evidence_detailed",
    "adjudicate_risks": "ai.auditor.evidence_adjudicator.adjudicate_risks",
    "build_recommendations": "ai.auditor.recommendation_engine.build_recommendations",
    "compare_clauses": "ai.matcher.matcher.compare_clauses",
    "retrieve_knowledge": "ai.rag.vector_store.search_knowledge",
    "retrieve_templates": "ai.rag.vector_store.search_similar_templates",
    "revise_clause": "ai.reviser.revise_clause",
    "draft_clause": "ai.reviser.generate_clause",
}

# 每个 Skill 的最小安全输入（用于"链路真实可调用"冒烟）
MINIMAL_PAYLOAD = {
    "classify_contract": {"full_text": "甲方：A 公司；乙方：B 公司。本合同为买卖合同。"},
    "extract_elements": {"full_text": "甲方：A 公司；乙方：B 公司。合同金额 10 万元。", "contract_type": "买卖合同"},
    "rule_scan": {"text": "乙方逾期付款的，按日千分之五支付违约金。"},
    "extract_evidence": {"full_text": "乙方逾期付款的，按日千分之五支付违约金。"},
    "adjudicate_risks": {
        "evidence": {
            "is_delivery_type": True,
            "R01_违约金": {"exists": True, "unit": "daily", "rate": 0.005, "clause_text": "按日千分之五"},
        }
    },
    "build_recommendations": {
        "risks": [{"risk_type": "R01", "level": "high", "clause_text": "按日千分之五"}],
        "evidence": {"is_delivery_type": False},
    },
    "compare_clauses": {"full_text": "第一条 合同主体。", "contract_type": "买卖合同"},
    "retrieve_knowledge": {"query": "违约金", "top_k": 1},
    "retrieve_templates": {"query": "买卖合同", "top_k": 1},
    "revise_clause": {"clause_text": "乙方逾期付款的，按日千分之五支付违约金。", "instruction": "把违约金降到合理水平"},
    "draft_clause": {"instruction": "补充不可抗力条款"},
}

_LLM_PROMPT_MARKER = "合同条款事实抽取助手"


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [type("_C", (), {"message": type("_M", (), {"content": content})()})()]


class _FakeLLM:
    """假 LLM：按 prompt 里出现的标记返回合法 JSON，并记录所有调用。"""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, prompt: str, temperature: float = 0.1) -> str:
        self.calls.append(prompt)
        if _LLM_PROMPT_MARKER in prompt:
            # evidence_extractor：成功但无风险事实
            return json.dumps({"contract_type": "买卖合同", "is_delivery_type": False})
        if "合同分类" in prompt or "contract_type" in prompt:
            return json.dumps({"contract_type": "买卖合同", "is_outsourcing": False,
                               "confidence": 0.9, "reason": "买卖特征明显"})
        if "法律信息抽取" in prompt or "parties" in prompt:
            return json.dumps({"parties": {"甲方": "A 公司", "乙方": "B 公司"}, "amount": None,
                               "sign_date": None, "performance_period": None,
                               "dispute_resolution": None, "governing_law": None})
        # 建议层 / 修订 / 起草 / 比对 —— 返回一个通用可解析对象
        return json.dumps({"revised_clause": "修订后的条款", "changes": ["调整违约金"],
                           "explanation": "按比例调整", "constraints": ["不超过 20%"],
                           "legal_basis": ["民法典第585条"], "risk_type": "R01",
                           "clause_text": "新增条款正文", "verified": True,
                           "remaining_risks": [], "final_advice": "建议采用"})


def _patch_all_llm(fake: _FakeLLM):
    """把假 LLM 打到**所有**持有 llm_client 单例的模块上（避免漏掉某条真实外呼路径）。

    注意：`ai.rag.feedback_store` 本身不 import llm_client（它只做检索），故不在列表内。
    """
    import contextlib

    import ai.classifier.classifier as classifier_mod
    import ai.classifier.rag_classifier as rag_classifier_mod
    import ai.matcher.matcher as matcher_mod

    patchers = [
        unittest.mock.patch.object(llm_client_module.llm_client, "chat", side_effect=fake),
        unittest.mock.patch.object(reviser.llm_client, "chat", side_effect=fake),
        unittest.mock.patch.object(evidence_extractor.llm_client, "chat", side_effect=fake),
        unittest.mock.patch.object(classifier_mod.llm_client, "chat", side_effect=fake),
        unittest.mock.patch.object(rag_classifier_mod.llm_client, "chat", side_effect=fake),
        unittest.mock.patch.object(matcher_mod.llm_client, "chat", side_effect=fake),
    ]
    stack = contextlib.ExitStack()
    for p in patchers:
        stack.enter_context(p)
    return stack


class SkillTestBase(unittest.TestCase):
    def setUp(self):
        # 杜绝任何真实外呼：默认 Key 置空（个人 Key 上下文在本进程也没有）
        self._orig_default_key = llm_client_module.DEFAULT_API_KEY
        llm_client_module.DEFAULT_API_KEY = ""

    def tearDown(self):
        llm_client_module.DEFAULT_API_KEY = self._orig_default_key


# ══════════════════════════════════════════════════════════════════════
# 1. Registry：注册 / 查询 / 枚举 / 重复 / 不存在 / disabled
# ══════════════════════════════════════════════════════════════════════

class TestRegistryBasics(unittest.TestCase):
    def test_exactly_twelve_skills_registered(self):
        self.assertEqual(registry.count(), 12, "本轮验收标准：list() == 12")
        self.assertEqual(registry.names(), EXPECTED_SKILLS)

    def test_get_returns_skill_and_unknown_raises(self):
        skill = registry.get("rule_scan")
        self.assertIsInstance(skill, Skill)
        self.assertEqual(skill.name, "rule_scan")
        self.assertTrue(registry.has("rule_scan"))
        with self.assertRaises(SkillNotFoundError):
            registry.get("no_such_skill")
        self.assertFalse(registry.has("no_such_skill"))

    def test_list_returns_skills_in_declaration_order(self):
        listed = registry.list_skills()
        self.assertEqual([s.name for s in listed], EXPECTED_SKILLS)
        self.assertTrue(all(isinstance(s, Skill) for s in listed))

    def test_list_filters_by_category_and_enabled(self):
        audit = registry.list_skills(category="审核")
        self.assertEqual([s.name for s in audit], ["rule_scan", "adjudicate_risks",
                                                   "build_recommendations", "compare_clauses"])
        self.assertEqual(len(registry.list_skills(enabled_only=True)), 12)
        for skill in registry.list_skills():
            self.assertIn(skill.category, CATEGORIES)

    def test_duplicate_registration_raises_and_does_not_overwrite(self):
        original = registry.get("rule_scan")
        with self.assertRaises(SkillAlreadyRegisteredError):
            registry.register(make_skill(
                name="rule_scan", description="dup", category="审核", source="x",
                input_schema={}, output_schema={}, handler=lambda payload: None,
            ))
        self.assertIs(registry.get("rule_scan"), original, "重复注册不得静默覆盖")


class TestRegistryStubSkills(unittest.TestCase):
    """用临时注册表验证 disabled / 不存在 / enabled_only 过滤（测后完整复原）。"""

    def setUp(self):
        self._snapshot = dict(registry._skills)

    def tearDown(self):
        registry._skills.clear()
        registry._skills.update(self._snapshot)

    def _stub(self, name, enabled=True):
        returns = []
        return registry.register(make_skill(
            name=name, description="stub", category="审核", source="test.stub",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            output_schema={"type": "object"},
            handler=lambda payload: returns.append(payload) or {"ok": True},
            enabled=enabled,
        )), returns

    def test_disabled_skill_rejects_invoke(self):
        self._stub("stub_disabled", enabled=False)
        with self.assertRaises(SkillDisabledError):
            registry.invoke("stub_disabled", {"x": "1"})

    def test_enabled_skill_invokes_real_handler(self):
        _, captured = self._stub("stub_enabled")
        result = registry.invoke("stub_enabled", {"x": "1"})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured, [{"x": "1"}], "handler 必须收到调用方传入的 payload")

    def test_enabled_only_filter_excludes_disabled(self):
        self._stub("stub_disabled", enabled=False)
        self.assertIn("stub_disabled", registry.names())
        self.assertNotIn("stub_disabled", registry.names(enabled_only=True))

    def test_invoke_unknown_skill_raises_not_found(self):
        with self.assertRaises(SkillNotFoundError):
            registry.invoke("definitely_not_registered", {})


# ══════════════════════════════════════════════════════════════════════
# 2. Schema：返回与输入校验
# ══════════════════════════════════════════════════════════════════════

class TestSkillSchema(unittest.TestCase):
    def test_all_skills_expose_complete_descriptions(self):
        described = registry.describe()
        self.assertEqual(len(described), 12)
        for item in described:
            for field in ("name", "description", "category", "enabled",
                          "input_schema", "output_schema", "mutates_input",
                          "requires_llm", "source"):
                self.assertIn(field, item, f"{item.get('name')} 缺少字段 {field}")
            self.assertTrue(item["description"].strip())
            self.assertTrue(item["input_schema"], f"{item['name']} 的 input_schema 不应为空")
            self.assertTrue(item["output_schema"], f"{item['name']} 的 output_schema 不应为空")
            self.assertTrue(item["source"].strip(), f"{item['name']} 应标注底层真实函数")

    def test_describe_is_json_serializable(self):
        json.dumps(registry.describe(), ensure_ascii=False)  # 不得抛 TypeError

    def test_sources_match_audited_real_functions(self):
        for name, expected_source in SKILL_SOURCE.items():
            self.assertEqual(registry.get(name).source, expected_source)

    def test_source_attribute_resolves_to_real_callable(self):
        """每个 Skill 声明的 source 必须真的能 import 到。"""
        import importlib

        for name, dotted in SKILL_SOURCE.items():
            module_name, attr = dotted.rsplit(".", 1)
            obj = getattr(importlib.import_module(module_name), attr)
            self.assertTrue(callable(obj), f"{name} 的 source {dotted} 不是可调用对象")

    def test_mutates_input_declared_only_where_true(self):
        mutating = [s.name for s in registry.list_skills() if s.mutates_input]
        self.assertEqual(mutating, ["build_recommendations"],
                         "只有 build_recommendations 是原地 enrich，必须如实标注")
        schema = registry.get("build_recommendations").input_schema
        self.assertTrue(schema.get("x-mutates-input"), "input_schema 也要标注原地修改")

    def test_requires_llm_flags(self):
        needs_llm = sorted(s.name for s in registry.list_skills() if s.requires_llm)
        self.assertEqual(needs_llm, sorted([
            "classify_contract", "extract_elements", "extract_evidence",
            "build_recommendations", "compare_clauses", "revise_clause", "draft_clause",
        ]))
        for deterministic in ("rule_scan", "adjudicate_risks",
                              "retrieve_knowledge", "retrieve_templates", "parse_document"):
            self.assertFalse(registry.get(deterministic).requires_llm,
                             f"{deterministic} 不应被标记为需要 LLM")


class TestSchemaValidation(unittest.TestCase):
    def test_missing_required_field_rejected(self):
        with self.assertRaises(SkillInputError):
            validate_against_schema({}, {"type": "object", "properties": {"a": {"type": "string"}},
                                         "required": ["a"]})

    def test_none_treated_as_not_provided(self):
        with self.assertRaises(SkillInputError):
            validate_against_schema({"a": None},
                                    {"type": "object", "properties": {"a": {"type": "string"}},
                                     "required": ["a"]})

    def test_wrong_type_rejected(self):
        with self.assertRaises(SkillInputError):
            validate_against_schema({"a": 1},
                                    {"type": "object", "properties": {"a": {"type": "string"}}})
        with self.assertRaises(SkillInputError):
            validate_against_schema({"a": "x"},
                                    {"type": "object", "properties": {"a": {"type": "array"}}})

    def test_bool_is_not_accepted_as_integer(self):
        with self.assertRaises(SkillInputError):
            validate_against_schema({"n": True},
                                    {"type": "object", "properties": {"n": {"type": "integer"}}})

    def test_unknown_keyword_ignored_and_optional_absent_ok(self):
        validate_against_schema({}, {"type": "object", "properties": {"a": {"type": "string"}},
                                     "x-unknown": 1})

    def test_enum_violation_rejected(self):
        with self.assertRaises(SkillInputError):
            validate_against_schema({"c": "bad"},
                                    {"type": "object", "properties": {"c": {"type": "string",
                                                                            "enum": ["laws"]}}})

    def test_coerce_int_bounds_and_errors(self):
        self.assertEqual(coerce_int(None, default=3), 3)
        self.assertEqual(coerce_int("5", default=3), 5)
        with self.assertRaises(SkillInputError):
            coerce_int(True, default=3)
        with self.assertRaises(SkillInputError):
            coerce_int("abc", default=3)
        with self.assertRaises(SkillInputError):
            coerce_int(0, default=3, minimum=1)


# ══════════════════════════════════════════════════════════════════════
# 3. 12 个 adapter 可解析 + 链路真实可调用
# ══════════════════════════════════════════════════════════════════════

class TestAdaptersForwardToRealFunctions(SkillTestBase):
    def test_every_skill_has_callable_handler(self):
        for skill in registry.list_skills():
            self.assertTrue(callable(skill.handler), f"{skill.name} 的 handler 不可调用")

    def test_all_twelve_skills_invokable_with_minimal_safe_input(self):
        """Registry → Adapter → 原函数：12 条链路全部真实跑通。"""
        fake = _FakeLLM()
        with _patch_all_llm(fake):
            for name in EXPECTED_SKILLS:
                if name == "parse_document":
                    continue  # 需要真实文件，单独测（见 TestParseDocumentBoundary）
                payload = MINIMAL_PAYLOAD[name]
                with self.subTest(skill=name):
                    result = registry.invoke(name, payload)
                    self.assertIsNotNone(result, f"{name} 返回了 None")

    def test_adapter_returns_identical_object_to_direct_call(self):
        """证明"适配器只转发"：结果与直接调用原函数逐字段一致。"""
        text = "乙方逾期付款的，按日千分之五支付违约金。"
        direct = rule_engine.run_rules(text)
        via_skill = registry.invoke("rule_scan", {"text": text})
        self.assertEqual(via_skill, direct)

        evidence = {"is_delivery_type": True}
        self.assertEqual(registry.invoke("adjudicate_risks", {"evidence": evidence}),
                         evidence_adjudicator.adjudicate_risks(evidence))

    def test_input_validation_blocks_bad_payload_before_handler(self):
        with self.assertRaises(SkillInputError):
            registry.invoke("rule_scan", {})                       # 缺必填
        with self.assertRaises(SkillInputError):
            registry.invoke("rule_scan", {"text": ""})             # 空文本
        with self.assertRaises(SkillInputError):
            registry.invoke("rule_scan", {"text": 123})            # 类型错
        with self.assertRaises(SkillInputError):
            registry.invoke("adjudicate_risks", {"evidence": []})  # 应为 object

    def test_revise_clause_does_not_expose_evaluate_only_params(self):
        schema = registry.get("classify_contract").input_schema
        self.assertNotIn("exclude_self", schema["properties"],
                         "评测防泄漏参数 exclude_self 不得进入 Skill 契约")
        self.assertNotIn("feedback_context", registry.get("extract_evidence").input_schema["properties"],
                         "进程内 callable 不得进入 Skill 契约")

    def test_build_recommendations_copies_input_but_declares_mutation(self):
        """契约：传副本保护调用方；同时如实声明底层原地修改。"""
        risk = {"risk_type": "R01", "level": "high", "clause_text": "按日千分之五"}
        payload = {"risks": [risk], "evidence": {"is_delivery_type": False}}
        fake = _FakeLLM()
        with _patch_all_llm(fake):
            result = registry.invoke("build_recommendations", payload)
        self.assertNotIn("suggestion", risk,
                         "Skill 层应传副本，调用方原列表不得被改写")
        self.assertEqual(len(result), 1)
        self.assertTrue(registry.get("build_recommendations").mutates_input)

    def test_exception_propagates_unwrapped(self):
        """底层异常必须原样向上抛（不吞、不伪装成功）。"""
        boom = RuntimeError("底层炸了")
        skill = registry.get("rule_scan")
        original_handler = skill.handler
        skill.handler = lambda payload: (_ for _ in ()).throw(boom)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                registry.invoke("rule_scan", {"text": "任意文本"})
            self.assertIs(ctx.exception, boom)
        finally:
            skill.handler = original_handler


class TestParseDocumentBoundary(unittest.TestCase):
    def test_parse_document_rejects_path_outside_data_dir(self):
        with self.assertRaises(SkillInputError):
            registry.invoke("parse_document", {"file_path": str(_BACKEND_DIR / "main.py")})

    def test_parse_document_rejects_nonexistent_file(self):
        from ai.skills.adapters import _DATA_DIR

        with self.assertRaises(SkillInputError):
            registry.invoke("parse_document",
                            {"file_path": os.path.join(_DATA_DIR, "definitely-missing.docx")})

    def test_data_dir_is_backend_data(self):
        """适配器计算出的数据目录必须与生产 UPLOAD_DIR 一致（backend/data）。"""
        import api.contracts as contracts
        from ai.skills.adapters import _DATA_DIR

        self.assertEqual(os.path.realpath(_DATA_DIR), os.path.realpath(contracts.UPLOAD_DIR))

    def test_parse_document_real_docx_inside_data_dir(self):
        """在 data/ 内造一个临时 docx，验证链路真实可调用（读取后删除）。"""
        from docx import Document

        from ai.skills.adapters import _DATA_DIR

        os.makedirs(_DATA_DIR, exist_ok=True)
        path = os.path.join(_DATA_DIR, "_skill_registry_test_tmp.docx")
        try:
            doc = Document()
            doc.add_paragraph("第一条 本合同为买卖合同。")
            doc.save(path)
            result = registry.invoke("parse_document", {"file_path": path})
            self.assertIn("full_text", result)
            self.assertIn("买卖合同", result["full_text"])
        finally:
            if os.path.exists(path):
                os.remove(path)


# ══════════════════════════════════════════════════════════════════════
# 4. 边界回归：本轮不得触碰的东西
# ══════════════════════════════════════════════════════════════════════

class TestBoundaryRegression(SkillTestBase):
    def test_forbidden_capabilities_are_not_registered(self):
        for forbidden in ("audit_with_llm", "llm_auditor", "bm25", "rrf_fuse",
                          "taxonomy", "confidence", "chunker", "utils", "llm_client"):
            self.assertFalse(registry.has(forbidden), f"{forbidden} 不得注册为 Skill")

    def test_zero_shot_classifier_not_exposed_as_skill(self):
        """零样本实现只作降级/评测基线，不作为独立 Skill。"""
        self.assertNotIn("classify_contract_zeroshot", registry.names())
        self.assertEqual(registry.get("classify_contract").source,
                         "ai.classifier.rag_classifier.classify_by_rag")

    def test_auditor_package_reexports_are_additive(self):
        """ai/auditor/__init__.py 的再导出是纯加法：原有两个名字必须仍在。"""
        import ai.auditor as auditor_pkg

        self.assertIs(auditor_pkg.run_rules, rule_engine.run_rules)
        self.assertIs(auditor_pkg.RISK_RULES, rule_engine.RISK_RULES)
        self.assertIs(auditor_pkg.extract_evidence_detailed, evidence_extractor.extract_evidence_detailed)
        self.assertIs(auditor_pkg.adjudicate_risks, evidence_adjudicator.adjudicate_risks)

    def test_extract_evidence_default_prompt_unchanged(self):
        """官方评测冻结前提：默认 prompt 里必须仍无任何 feedback 注入内容。"""
        prompt = evidence_extractor.SYSTEM_PROMPT_EVIDENCE
        self.assertIn(_LLM_PROMPT_MARKER, prompt)
        self.assertNotIn("人工历史参考", prompt,
                         "默认 prompt 不得包含 feedback 注入块（那是 _FEEDBACK_HEADER，只在注入时拼接）")
        self.assertIn("绝对不要判断是否构成风险", prompt)

    def test_skills_package_import_is_lightweight(self):
        """`import ai.skills` 必须是轻量的：不拖入 torch / chromadb（重依赖全部延迟导入）。

        用**子进程**验证，避免被同一 pytest 进程里其他测试文件的导入污染。
        """
        import subprocess

        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "import ai.skills;"
            "print('torch' in sys.modules, 'chromadb' in sys.modules,"
            " 'sentence_transformers' in sys.modules,"
            " 'ai.auditor.llm_auditor' in sys.modules, 'ai.rag.bm25' in sys.modules,"
            " 'ai.chunker' in sys.modules, 'ai.taxonomy' in sys.modules)"
        ) % str(_BACKEND_DIR)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        torch_loaded, chroma_loaded, st_loaded, llm_auditor, bm25, chunker, taxonomy = (
            proc.stdout.strip().splitlines()[-1].split()
        )
        # 重依赖：导入期不得出现
        self.assertEqual(torch_loaded, "False", "ai.skills 导入期不应拖入 torch")
        self.assertEqual(chroma_loaded, "False", "ai.skills 导入期不应拖入 chromadb")
        self.assertEqual(st_loaded, "False", "ai.skills 导入期不应拖入 sentence_transformers")
        # 被禁止注册的能力/工具：不得出现在导入图里
        self.assertEqual(llm_auditor, "False", "不得引入已归档的 llm_auditor")
        self.assertEqual(bm25, "False", "不得引入算法原语 bm25")
        self.assertEqual(chunker, "False", "不得引入基础设施 chunker")
        self.assertEqual(taxonomy, "False", "不得引入基础设施 taxonomy")

    def test_main_pipeline_untouched_by_skill_layer(self):
        """_run_audit 不得被 Skill 层改动：其源码全文不得出现 skills / registry。"""
        import inspect

        import api.contracts as contracts

        source = inspect.getsource(contracts._run_audit)
        self.assertNotIn("skills", source)
        self.assertNotIn("registry", source)

    def test_run_audit_still_uses_original_imports(self):
        """主链路仍走原函数：确认 contracts.py 的 import 与调用未被 Skill 层替换。"""
        import api.contracts as contracts

        self.assertIs(contracts.run_rules, rule_engine.run_rules)
        self.assertIs(contracts.extract_evidence_detailed, evidence_extractor.extract_evidence_detailed)
        self.assertIs(contracts.adjudicate_risks, evidence_adjudicator.adjudicate_risks)
        self.assertIs(contracts.compare_clauses, matcher.compare_clauses)


# ══════════════════════════════════════════════════════════════════════
# 5. API：GET /api/skills、GET /api/skills/{name}、POST .../invoke
# ══════════════════════════════════════════════════════════════════════

class TestSkillsApi(SkillTestBase):
    @classmethod
    def setUpClass(cls):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api import deps
        from api.skills import router as skills_router
        from models.user import User

        def _user(role):
            user = User()
            user.id = 1
            user.username = f"test-{role}"
            user.role = role
            user.is_active = True
            return user

        cls._app = FastAPI()
        cls._app.include_router(skills_router, prefix="/api")
        cls._client = TestClient(cls._app)
        cls._deps = deps

        # 可切换的当前用户（按角色）
        cls._current = {"user": _user("admin")}
        cls._app.dependency_overrides[deps.get_current_user] = lambda: cls._current["user"]

    @classmethod
    def tearDownClass(cls):
        cls._app.dependency_overrides.clear()

    def _as(self, role: str):
        from models.user import User

        user = User()
        user.id = 1
        user.username = f"test-{role}"
        user.role = role
        user.is_active = True
        self._current["user"] = user
        return user

    def test_list_returns_twelve_with_schemas(self):
        self._as("admin")
        resp = self._client.get("/api/skills")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        data = body["data"]
        self.assertEqual(data["total"], 12)
        self.assertEqual(data["registered_total"], 12)
        self.assertEqual([s["name"] for s in data["skills"]], EXPECTED_SKILLS)
        for item in data["skills"]:
            self.assertTrue(item["input_schema"])
            self.assertTrue(item["output_schema"])
            self.assertNotIn("handler", item, "handler 不得对外暴露")

    def test_list_category_filter(self):
        self._as("admin")
        resp = self._client.get("/api/skills", params={"category": "检索"})
        self.assertEqual(resp.status_code, 200)
        names = [s["name"] for s in resp.json()["data"]["skills"]]
        self.assertEqual(names, ["retrieve_knowledge", "retrieve_templates"])

    def test_detail_returns_schema_and_404_for_unknown(self):
        self._as("admin")
        resp = self._client.get("/api/skills/rule_scan")
        self.assertEqual(resp.status_code, 200, resp.text)
        detail = resp.json()["data"]
        self.assertEqual(detail["name"], "rule_scan")
        self.assertEqual(detail["source"], "ai.auditor.rule_engine.run_rules")
        self.assertFalse(detail["requires_llm"])

        missing = self._client.get("/api/skills/not_a_skill")
        self.assertEqual(missing.status_code, 404)

    def test_endpoints_require_authentication(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.skills import router as skills_router

        # 不带 dependency_overrides 的应用：必须 401/403（不得匿名可读）
        bare = FastAPI()
        bare.include_router(skills_router, prefix="/api")
        client = TestClient(bare)
        for path in ("/api/skills", "/api/skills/rule_scan"):
            self.assertIn(client.get(path).status_code, (401, 403),
                          f"{path} 不应允许匿名访问")

    def test_invoke_deterministic_skill_without_llm_key(self):
        """rule_scan 不需要 LLM：默认 Key 已置空，仍必须成功。"""
        self._as("uploader")
        resp = self._client.post("/api/skills/rule_scan/invoke",
                                 json={"payload": {"text": "乙方逾期付款，按日千分之五支付违约金。"}})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["skill"], "rule_scan")
        self.assertFalse(data["requires_llm"])
        self.assertIsInstance(data["result"], list)

    def test_invoke_llm_skill_without_key_returns_400(self):
        """需要 LLM 的 Skill 且没有任何 Key → 400 + 明确指引（不静默降级）。"""
        self._as("uploader")
        resp = self._client.post("/api/skills/classify_contract/invoke",
                                 json={"payload": {"full_text": "买卖合同"}})
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("DeepSeek", resp.json()["detail"])

    def test_invoke_llm_skill_with_fake_llm_succeeds(self):
        fake = _FakeLLM()
        self._as("uploader")
        self._orig_key = llm_client_module.DEFAULT_API_KEY
        llm_client_module.DEFAULT_API_KEY = "sk-test-not-real"
        try:
            with _patch_all_llm(fake):
                resp = self._client.post("/api/skills/rule_scan/invoke",
                                         json={"payload": {"text": "买卖合同，违约金按日千分之五。"}})
                self.assertEqual(resp.status_code, 200, resp.text)
        finally:
            llm_client_module.DEFAULT_API_KEY = self._orig_key

    def test_invoke_unknown_skill_404(self):
        self._as("admin")
        resp = self._client.post("/api/skills/nope/invoke", json={"payload": {}})
        self.assertEqual(resp.status_code, 404)

    def test_invoke_invalid_payload_400(self):
        self._as("uploader")
        resp = self._client.post("/api/skills/rule_scan/invoke", json={"payload": {}})
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("输入不合法", resp.json()["detail"])

    def test_invoke_requires_role_permission(self):
        """reviewer 不得调用 uploader 专属能力（统一 invoke 不绕过权限）。"""
        self._as("reviewer")
        resp = self._client.post("/api/skills/classify_contract/invoke",
                                 json={"payload": {"full_text": "买卖合同"}})
        self.assertEqual(resp.status_code, 403, resp.text)

        # 反向：reviewer 可以调用 rule_scan（其 permissions 含 reviewer）
        ok = self._client.post("/api/skills/rule_scan/invoke",
                               json={"payload": {"text": "任意文本"}})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_parse_document_is_admin_only(self):
        self._as("uploader")
        resp = self._client.post("/api/skills/parse_document/invoke",
                                 json={"payload": {"file_path": "whatever.docx"}})
        self.assertEqual(resp.status_code, 403, resp.text)


if __name__ == "__main__":
    unittest.main()
