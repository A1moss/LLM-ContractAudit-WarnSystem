"""ai.skills.adapters — 12 个 Skill 适配器（**转发，不重写**）。

铁律
----
```
Skill → Adapter → 现有函数
```
**绝不** `Skill → 重新实现一套逻辑`。

具体保证（本轮硬约束）：
* 不修改任何现有 Skill 函数的**函数体**；
* 不修改任何现有 Skill 函数的**签名**；
* 不修改任何现有 Skill 函数的**返回结构**；
* 适配器只做三件事：① 从 dict 取字段；② 屏蔽不该暴露的参数；③ 原样返回值。

参数屏蔽的唯一例外（都是"因为进程内对象不可序列化"，不是"为了好看"）：

| Skill | 底层函数 | 屏蔽的参数 | 原因 |
|---|---|---|---|
| `classify_contract` | `rag_classifier.classify_by_rag` | `exclude_self` | 评测专用防泄漏参数，不属于能力契约 |
| `extract_evidence` | `evidence_extractor.extract_evidence_detailed` | `feedback_context` | 进程内 callable，不可序列化 |
| `build_recommendations` | `recommendation_engine.build_recommendations` | 无（但传副本，见下） | 该函数**原地 enrich**，语义已在 schema 中显式标注 |

关于 `build_recommendations` 的原地修改
--------------------------------------
底层函数确实会**原地修改并返回同一批 risk 对象**。Skill 层**不隐藏这一事实**：
* `mutates_input=True` 在 Skill 描述里显式暴露；
* `input_schema` 里用 `x-mutates-input: true` 再次标注；
* `notes` 写明"传入副本以避免调用方原有列表被意外改写"。

即：Skill 自己传副本（保护调用方），但绝**不**对外宣称"这个能力是无副作用的"。

延迟导入（lazy import）
----------------------
`ai.rag.vector_store` 会拖入 chromadb / sentence-transformers（torch），
`ai.auditor.*` / `ai.matcher` / `ai.reviser` 会拖入 openai。
因此适配器**全部在 handler 内部延迟导入**（与 `api/contracts.py:1353` 的既有写法一致），
使 `import ai.skills` 保持轻量 —— `main.py` 在启动链上导入它不会变慢。

只读边界
--------
`parse_document` 会对文件路径做约束（必须是已存在的普通文件，且位于后端 `data/` 目录内），
避免"统一 invoke 接口"变成任意文件读取原语。见 `_resolve_data_file`。
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ai.skills.protocol import (
    CATEGORY_AUDIT,
    CATEGORY_CLASSIFY,
    CATEGORY_EXTRACT,
    CATEGORY_PARSE,
    CATEGORY_RETRIEVE,
    CATEGORY_REVISE,
    SkillInputError,
    coerce_int,
    make_skill,
)
from ai.skills.registry import register

logger = logging.getLogger(__name__)

# 上传文件落盘目录（= api/contracts.py 的 UPLOAD_DIR，backend/data）。
# 路径必须与生产落盘目录**逐字一致**，否则 parse_document 的路径白名单会指向错误目录
# （静默全部拒绝、或错误放行）。本文件位于 backend/ai/skills/adapters.py，
# 因此需要上溯**三级**到 backend/ 再拼 data。
#
# 为什么这里独立计算而不 `from api.contracts import UPLOAD_DIR`：
# 那会把整个 api.contracts 模块（含 reportlab / docx_reviser / pdf_anchor 等）
# 拖进 `import ai.skills` 的导入图，违背"Skill 层导入必须轻量"的设计。
# 一致性由 tests/test_skills_registry.py::test_data_dir_is_backend_data 断言守护。
_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
)


# ══════════════════════════════════════════════════════════════════════
# 公共辅助
# ══════════════════════════════════════════════════════════════════════

def _require_str(payload: dict, key: str, *, allow_empty: bool = False) -> str:
    """取必填字符串字段（types 已在 schema 层校验，这里负责业务级完备性）。"""
    value = payload.get(key)
    if not isinstance(value, str):
        raise SkillInputError(f"缺少字符串字段 {key!r}")
    if not allow_empty and not value.strip():
        raise SkillInputError(f"字段 {key!r} 不能为空")
    return value


def _resolve_data_file(file_path: str) -> str:
    """把 Skill 输入的文件路径限制在 `backend/data/` 内（防任意文件读取）。

    保留仓库既有语义：`parse_document` 是给"已落盘的上传文件"用的。
    """
    candidate = os.path.realpath(os.path.abspath(file_path))
    root = os.path.realpath(os.path.abspath(_DATA_DIR))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise SkillInputError(
            "file_path 必须位于后端数据目录内（该 Skill 用于解析已落盘的上传文件）"
        )
    if not os.path.isfile(candidate):
        raise SkillInputError(f"file_path 不是已存在的普通文件：{file_path}")
    return candidate


# ── 延迟导入缓存（避免每次 invoke 都走 import 机制）──
_cache: dict[str, Any] = {}


def _lazy(module_name: str, attr: str):
    """延迟导入 `module.attr`，进程内缓存。（与仓库既有函数内 import 写法同效，仅省一次查表。）"""
    key = f"{module_name}:{attr}"
    fn = _cache.get(key)
    if fn is None:
        import importlib

        fn = getattr(importlib.import_module(module_name), attr)
        _cache[key] = fn
    return fn


# ══════════════════════════════════════════════════════════════════════
# 1. parse_document
# ══════════════════════════════════════════════════════════════════════

def _parse_document(payload: dict) -> dict:
    file_path = _resolve_data_file(_require_str(payload, "file_path"))
    detect_and_parse = _lazy("ai.parser", "detect_and_parse")
    return detect_and_parse(file_path)


register(make_skill(
    name="parse_document",
    description="把 DOCX / 文本 PDF / 扫描 PDF / 图片合同解析为统一 full_text（含混合 PDF 逐页判定与 OCR）。",
    category=CATEGORY_PARSE,
    source="ai.parser.detect_and_parse",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "待解析文件路径，必须位于后端 data/ 目录内（已落盘的上传文件）。",
            }
        },
        "required": ["file_path"],
    },
    output_schema={
        "type": "object",
        "description": "统一解析结果；三种输入格式只产出同一个 full_text。",
        "properties": {
            "full_text": {"type": "string"},
            "paragraphs": {"type": "array", "items": {"type": "object"}},
            "format": {"type": "string"},
            "page_count": {"type": "integer"},
            "ocr_quality": {"type": "string"},
            "ocr_confidence": {"type": "number"},
            "ocr_valid_chars": {"type": "integer"},
            "ocr_reason": {"type": "string"},
            "scanned_pages": {"type": "array", "items": {"type": "integer"}},
            "truncated_pages": {"type": "array", "items": {"type": "integer"}},
            "hybrid_pdf": {"type": "boolean"},
            "error": {"type": "string"},
        },
    },
    handler=_parse_document,
    permissions=("admin",),
    tags=("io", "read-only", "no-llm"),
    notes="ocr_* 字段仅 OCR 路径返回；hybrid_pdf 仅混合 PDF 返回。本 Skill 不调用 LLM，但会读磁盘。",
))


# ══════════════════════════════════════════════════════════════════════
# 2. classify_contract（生产主实现 = RAG 少样本分类）
# ══════════════════════════════════════════════════════════════════════

def _classify_contract(payload: dict) -> dict:
    full_text = _require_str(payload, "full_text")
    top_k = coerce_int(payload.get("top_k"), default=3, minimum=1, maximum=10, field="top_k")
    # 直接引用真实实现模块，不用包级别名 —— 避免"到底调的是哪个 classify_contract"的歧义。
    # 包级 ai.classifier.classify_contract 同样指向它（见 ai/classifier/__init__.py:1）。
    classify_by_rag = _lazy("ai.classifier.rag_classifier", "classify_by_rag")
    # 刻意不传 exclude_self：那是评测防泄漏参数，不属于能力契约。
    return classify_by_rag(full_text, top_k=top_k)


register(make_skill(
    name="classify_contract",
    description="合同类型识别（RAG 少样本主实现）：检索同类范本做 few-shot，取不到则降级为零样本 LLM。",
    category=CATEGORY_CLASSIFY,
    source="ai.classifier.rag_classifier.classify_by_rag",
    input_schema={
        "type": "object",
        "properties": {
            "full_text": {"type": "string", "description": "合同全文。"},
            "top_k": {"type": "integer", "description": "检索的示例范本数量，默认 3。"},
        },
        "required": ["full_text"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "contract_type": {"type": "string",
                              "description": "法理分类名（必须在 taxonomy.ENABLED_TYPES 内）。"
                                             "分类失败时为 null —— 调用方须按「待分类」处理，"
                                             "不得当成任何法理类别（尤其不得当成「无名合同」）。"},
            "is_outsourcing": {"type": "boolean"},
            "confidence": {"type": "number"},
            "method": {
                "type": "string",
                "enum": ["rag", "rag-fallback-llm"],
                "description": "rag = 走范本 few-shot；rag-fallback-llm = 检索无结果、降级零样本。",
            },
            "reason": {"type": "string"},
            "top_matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"type": {"type": "string"}, "score": {"type": "number"}},
                },
            },
            "fallback": {"type": "boolean"},
        },
    },
    handler=_classify_contract,
    requires_llm=True,
    permissions=("uploader",),
    tags=("llm", "rag", "classification"),
    notes=(
        "生产主实现是 RAG 版；零样本实现 ai.classifier.classifier.classify_contract "
        "仅作降级与评测基线，**不作为独立 Skill 注册**。exclude_self（评测防泄漏参数）已屏蔽。"
        "失败态：contract_type=null + fallback=true（表示分类未成功，**不是**「无名合同」）。"
    ),
))


# ══════════════════════════════════════════════════════════════════════
# 3. extract_elements
# ══════════════════════════════════════════════════════════════════════

def _extract_elements(payload: dict) -> dict:
    full_text = _require_str(payload, "full_text")
    contract_type = payload.get("contract_type")
    if contract_type is not None and not isinstance(contract_type, str):
        raise SkillInputError("contract_type 需为字符串")
    if not contract_type:
        # 与生产链路保持一致：api/contracts.py:485 在分类完成前用中性占位词启动要素抽取。
        contract_type = "合同"
    extract_elements = _lazy("ai.extractor.extractor", "extract_elements")
    return extract_elements(full_text, contract_type)


register(make_skill(
    name="extract_elements",
    description="合同要素抽取：当事人、金额、签署日期、履行期限、争议解决、适用法律。",
    category=CATEGORY_EXTRACT,
    source="ai.extractor.extractor.extract_elements",
    input_schema={
        "type": "object",
        "properties": {
            "full_text": {"type": "string", "description": "合同全文。"},
            "contract_type": {
                "type": "string",
                "description": "合同类型；缺省时用中性占位词（要素抽取对类型不敏感）。",
            },
        },
        "required": ["full_text"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "parties": {"type": "object"},
            "amount": {"type": "object"},
            "sign_date": {"type": "string"},
            "performance_period": {"type": "string"},
            "dispute_resolution": {"type": "string"},
            "governing_law": {"type": "string"},
            "fallback": {"type": "boolean"},
        },
    },
    handler=_extract_elements,
    requires_llm=True,
    permissions=("uploader",),
    tags=("llm", "extraction"),
    notes='多项为 None 表示「未抽到」（不是「缺失即错误」）。governing_law 目前仅供报告展示，不参与风险裁决。',
))


# ══════════════════════════════════════════════════════════════════════
# 4. rule_scan（纯 Python，0 LLM）
# ══════════════════════════════════════════════════════════════════════

def _rule_scan(payload: dict) -> list:
    text = _require_str(payload, "text")
    run_rules = _lazy("ai.auditor.rule_engine", "run_rules")
    return run_rules(text)


register(make_skill(
    name="rule_scan",
    description="规则引擎初筛：13 条正则规则（R01-R13）对合同全文做确定性风险召回，作为审核基线。",
    category=CATEGORY_AUDIT,
    source="ai.auditor.rule_engine.run_rules",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "合同全文。"}},
        "required": ["text"],
    },
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "risk_type": {"type": "string"},
                "level": {"type": "string", "enum": ["high", "medium", "low"]},
                "name": {"type": "string"},
                "clause_text": {"type": "string"},
                "reason": {"type": "string"},
                "suggestion": {"type": "string"},
                "detection_method": {"type": "string", "enum": ["rule"]},
                "confidence": {"type": "number"},
                "related_law": {"type": "string"},
            },
        },
    },
    handler=_rule_scan,
    permissions=("uploader", "reviewer"),
    tags=("deterministic", "no-llm", "risk"),
    notes="纯 Python 正则，**不调用 LLM**。规则表 RISK_RULES 保持单一真源，Skill 层不复制、不裁剪。",
))


# ══════════════════════════════════════════════════════════════════════
# 5. extract_evidence（LLM 只抽事实，不下结论）
# ══════════════════════════════════════════════════════════════════════

def _extract_evidence(payload: dict) -> dict:
    full_text = _require_str(payload, "full_text")
    # 刻意不传 feedback_context：进程内 callable，不属于可序列化 Skill 输入。
    # 也**不修改** extract_evidence_detailed 的默认 prompt ——
    # feedback_context=None 时 prompt 逐字节不变，是官方评测冻结的前提。
    extract_evidence_detailed = _lazy(
        "ai.auditor.evidence_extractor", "extract_evidence_detailed"
    )
    return extract_evidence_detailed(full_text)


register(make_skill(
    name="extract_evidence",
    description="LLM 事实证据抽取：只抽客观事实（R01-R12 字段），**不下风险结论**。",
    category=CATEGORY_EXTRACT,
    source="ai.auditor.evidence_extractor.extract_evidence_detailed",
    input_schema={
        "type": "object",
        "properties": {"full_text": {"type": "string", "description": "合同全文。"}},
        "required": ["full_text"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "evidence": {
                "type": "object",
                "description": "契约见下：is_delivery_type + R01_违约金 … R12_数据（由 prompt 固定）。",
            },
            "status": {
                "type": "string",
                "enum": ["success", "partial", "failed"],
                "description": "success=全部块成功；partial=部分块失败；failed=全部块失败（上层必须降级）。",
            },
            "failed_chunks": {"type": "integer"},
            "total_chunks": {"type": "integer"},
        },
    },
    handler=_extract_evidence,
    requires_llm=True,
    permissions=("uploader",),
    tags=("llm", "evidence", "facts-only"),
    notes=(
        "feedback_context（进程内 callable）已屏蔽。"
        "status=failed 时 **不得**当作 0 风险，生产链路的降级逻辑在 _run_audit 中，不在本 Skill。"
    ),
))


# ══════════════════════════════════════════════════════════════════════
# 6. adjudicate_risks（确定性 Verifier，0 LLM）
# ══════════════════════════════════════════════════════════════════════

def _adjudicate_risks(payload: dict) -> list:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise SkillInputError("evidence 需为对象（抽取到的证据 dict）")
    adjudicate_risks = _lazy("ai.auditor.evidence_adjudicator", "adjudicate_risks")
    return adjudicate_risks(evidence)


register(make_skill(
    name="adjudicate_risks",
    description="确定性风险裁决（v6.4 主口径）：按硬阈值把证据裁决为 R01-R12 风险对象。",
    category=CATEGORY_AUDIT,
    source="ai.auditor.evidence_adjudicator.adjudicate_risks",
    input_schema={
        "type": "object",
        "properties": {"evidence": {"type": "object", "description": "extract_evidence 产出的 evidence。"}},
        "required": ["evidence"],
    },
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "risk_type": {"type": "string"},
                "level": {"type": "string", "enum": ["high", "medium"]},
                "name": {"type": "string"},
                "clause_text": {"type": "string"},
                "reason": {"type": "string"},
                "suggestion": {"type": "string"},
                "detection_method": {"type": "string", "enum": ["evidence"]},
                "confidence": {"type": "number"},
                "related_law": {"type": "string"},
            },
        },
    },
    handler=_adjudicate_risks,
    permissions=("uploader", "reviewer"),
    tags=("deterministic", "no-llm", "verifier", "risk"),
    notes=(
        "**本 Skill 本身不调用 LLM**（纯 Python 硬阈值）。"
        "定位为后续 Multi-Agent 的\"确定性 Verifier\"：把 LLM 抽到的事实转成可复现的判定，"
        "而不是让 LLM 自己下风险结论。阈值与 SUGGESTIONS 表保持单一真源。"
    ),
))


# ══════════════════════════════════════════════════════════════════════
# 7. build_recommendations（原地 enrich，已显式标注）
# ══════════════════════════════════════════════════════════════════════

def _build_recommendations(payload: dict) -> list:
    risks = payload.get("risks")
    if not isinstance(risks, list):
        raise SkillInputError("risks 需为数组（裁决产出的风险列表）")
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise SkillInputError("evidence 需为对象")

    # 原地修改是底层函数的既有语义，Skill 层不隐藏也不删除它，
    # 但传副本以保护调用方原有的列表对象不被意外改写。
    import copy

    build_recommendations = _lazy(
        "ai.auditor.recommendation_engine", "build_recommendations"
    )
    return build_recommendations(copy.deepcopy(risks), evidence)


register(make_skill(
    name="build_recommendations",
    description="建议层：为每条风险补充风险描述、示例、法律依据与 grounding 校验结果。",
    category=CATEGORY_AUDIT,
    source="ai.auditor.recommendation_engine.build_recommendations",
    input_schema={
        "type": "object",
        "x-mutates-input": True,
        "properties": {
            "risks": {"type": "array", "items": {"type": "object"}},
            "evidence": {"type": "object"},
        },
        "required": ["risks", "evidence"],
    },
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "risk_type": {"type": "string"},
                "level": {"type": "string"},
                "clause_text": {"type": "string"},
                "risk_description": {"type": "string"},
                "example": {"type": "string"},
                "legal_basis": {"type": "string"},
                "grounding": {"type": "object"},
            },
            "description": "在传入风险字段基础上增补 suggestion / risk_description / example / legal_basis / grounding。",
        },
    },
    handler=_build_recommendations,
    mutates_input=True,
    requires_llm=True,
    permissions=("uploader",),
    tags=("llm", "recommendation"),
    notes=(
        "**底层函数原地 enrich（mutates_input=True）** —— 该事实已在 skill schema 与 "
        "input_schema 的 x-mutates-input 中显式声明，不做隐藏。"
        "Skill 层传入 deepcopy 以保护调用方原列表；返回值即增补后的结果。"
        "grounding 为纯 Python 校验（recommendation_grounding_check），不需要额外 LLM。"
    ),
))


# ══════════════════════════════════════════════════════════════════════
# 8. compare_clauses（本轮不加 jurisdiction）
# ══════════════════════════════════════════════════════════════════════

def _compare_clauses(payload: dict) -> dict:
    full_text = _require_str(payload, "full_text")
    contract_type = payload.get("contract_type")
    if not isinstance(contract_type, str) or not contract_type.strip():
        raise SkillInputError("contract_type 必填（条款比对按类型做结构过滤）")
    is_outsourcing = payload.get("is_outsourcing")
    standard_clauses = payload.get("standard_clauses")
    if standard_clauses is not None and not isinstance(standard_clauses, list):
        raise SkillInputError("standard_clauses 需为数组或省略")

    compare_clauses = _lazy("ai.matcher.matcher", "compare_clauses")
    kwargs: dict = {
        "full_text": full_text,
        "contract_type": contract_type,
        "is_outsourcing": bool(is_outsourcing),
    }
    if standard_clauses is not None:
        kwargs["standard_clauses"] = standard_clauses
    return compare_clauses(**kwargs)


register(make_skill(
    name="compare_clauses",
    description="条款比对：把合同与对应类型的标准条款逐条比对，输出覆盖/偏离/缺失与跨条款关联风险。",
    category=CATEGORY_AUDIT,
    source="ai.matcher.matcher.compare_clauses",
    input_schema={
        "type": "object",
        "properties": {
            "full_text": {"type": "string"},
            "contract_type": {"type": "string", "description": "法理分类，用于结构过滤标准条款。"},
            "is_outsourcing": {"type": "boolean", "description": "业务标签：为真时叠加服务外包条款。"},
            "standard_clauses": {
                "type": "array",
                "items": {"type": "object"},
                "description": "企业自定义模板 clauses；省略则回退内置 standard_clauses.json。",
            },
        },
        "required": ["full_text", "contract_type"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "clauses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "priority": {"type": "string"},
                        "status": {"type": "string", "enum": ["covered", "partial", "missing"]},
                        "matched_text": {"type": "string"},
                        "similarity": {"type": "number"},
                        "deviation": {"type": "string"},
                        "completion": {"type": "string"},
                        "risk": {"type": "string"},
                        "related_law": {"type": "string"},
                    },
                },
            },
            "summary": {
                "type": "object",
                "properties": {
                    "total": {"type": "integer"},
                    "covered": {"type": "integer"},
                    "partial": {"type": "integer"},
                    "missing": {"type": "integer"},
                    "coverage_rate": {"type": "number"},
                },
            },
            "missing_critical": {"type": "array", "items": {"type": "string"}},
            "cross_clause_risks": {"type": "array", "items": {"type": "object"}},
        },
    },
    handler=_compare_clauses,
    requires_llm=True,
    permissions=("uploader",),
    tags=("llm", "comparison"),
    notes=(
        "本轮**刻意不引入 jurisdiction 参数**（欧盟法域属后续阶段）。"
        "有 4 个调用点（api/contracts.py:815/2146/2176/2206），全部走原函数，本 Skill 不改其签名。"
    ),
))


# ══════════════════════════════════════════════════════════════════════
# 9. retrieve_knowledge（Retrieval primitive）
# ══════════════════════════════════════════════════════════════════════

def _retrieve_knowledge(payload: dict) -> list:
    query = _require_str(payload, "query")
    collection_name = payload.get("collection_name") or "laws"
    if not isinstance(collection_name, str):
        raise SkillInputError("collection_name 需为字符串")
    top_k = coerce_int(payload.get("top_k"), default=5, minimum=1, maximum=10, field="top_k")
    search_knowledge = _lazy("ai.rag.vector_store", "search_knowledge")
    return search_knowledge(query, collection_name, top_k)


register(make_skill(
    name="retrieve_knowledge",
    description="知识库混合检索（稠密 + BM25，经 RRF 融合）：法条 / 标准条款的检索原语。",
    category=CATEGORY_RETRIEVE,
    source="ai.rag.vector_store.search_knowledge",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "collection_name": {
                "type": "string",
                "enum": ["laws", "standard_clauses"],
                "description": "集合名，默认 laws。",
            },
            "top_k": {"type": "integer", "description": "返回条数，默认 5；底层 Chroma 上限 10。"},
        },
        "required": ["query"],
    },
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "score": {"type": "number"},
                "id": {},
                "title": {"type": "string"},
                "law": {"type": "string"},
                "article": {"type": "string"},
            },
        },
    },
    handler=_retrieve_knowledge,
    permissions=("uploader", "reviewer"),
    tags=("rag", "retrieval", "no-llm", "backing-tool"),
    notes=(
        "定位为 **retrieval primitive / backing tool**，不是业务能力本身："
        "它供 classify_contract / draft_clause / revise_clause 内部调用，也可被后续 Agent 直接当检索工具用。"
        "当前不支持 metadata filter（jurisdiction 属后续阶段）。top_k 会被底层钳到 <=10。"
    ),
))


# ══════════════════════════════════════════════════════════════════════
# 10. retrieve_templates
# ══════════════════════════════════════════════════════════════════════

def _retrieve_templates(payload: dict) -> list:
    query = _require_str(payload, "query")
    top_k = coerce_int(payload.get("top_k"), default=5, minimum=1, maximum=10, field="top_k")
    search_similar_templates = _lazy("ai.rag.vector_store", "search_similar_templates")
    return search_similar_templates(query, top_k)


register(make_skill(
    name="retrieve_templates",
    description="合同范本检索：按语义相似度取同类范本，供分类 few-shot 与新增条款起草参考。",
    category=CATEGORY_RETRIEVE,
    source="ai.rag.vector_store.search_similar_templates",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "查询文本（合同片段或自然语言检索词）。"},
            "top_k": {"type": "integer", "description": "返回条数，默认 5；底层 Chroma 上限 10。"},
        },
        "required": ["query"],
    },
    output_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "file": {"type": "string"},
                "text": {"type": "string"},
                "score": {"type": "number"},
            },
        },
    },
    handler=_retrieve_templates,
    permissions=("uploader", "reviewer"),
    tags=("rag", "retrieval", "no-llm", "backing-tool"),
    notes="同为检索原语。契约：建库源为仓库内 resources/contract_templates_source.json（363 条范本）。",
))


# ══════════════════════════════════════════════════════════════════════
# 11. revise_clause（Leader→Follower→Self-QA 三段链路，但这是一个 Skill）
# ══════════════════════════════════════════════════════════════════════

def _revise_clause(payload: dict) -> dict:
    clause_text = _require_str(payload, "clause_text")
    instruction = _require_str(payload, "instruction")
    contract_type = payload.get("contract_type") or ""
    history = payload.get("history")
    if history is not None and not isinstance(history, list):
        raise SkillInputError("history 需为数组或省略")
    rag_context = payload.get("rag_context")
    if rag_context is not None and not isinstance(rag_context, list):
        raise SkillInputError("rag_context 需为数组或省略")

    revise_clause = _lazy("ai.reviser", "revise_clause")
    return revise_clause(clause_text, instruction, contract_type, history, rag_context)


register(make_skill(
    name="revise_clause",
    description="多轮对话式条款修订：Leader 设约束 → Follower 修订 → Self-QA 复核，返回修订结果。",
    category=CATEGORY_REVISE,
    source="ai.reviser.revise_clause",
    input_schema={
        "type": "object",
        "properties": {
            "clause_text": {"type": "string", "description": "当前条款原文。"},
            "instruction": {"type": "string", "description": "用户的修改指令。"},
            "contract_type": {"type": "string"},
            "history": {
                "type": "array",
                "items": {"type": "object"},
                "description": "历史轮次 [{instruction, revised_clause}]，底层只取最近 5 轮。",
            },
            "rag_context": {
                "type": "array",
                "items": {"type": "object"},
                "description": "相关法条（可来自 retrieve_knowledge）。",
            },
        },
        "required": ["clause_text", "instruction"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "revised_clause": {"type": "string"},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "legal_basis": {"type": "array", "items": {"type": "string"}},
            "risk_type": {"type": "string"},
            "target": {"type": "string"},
            "changes": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string"},
            "verified": {"type": "boolean"},
            "remaining_risks": {"type": "array", "items": {"type": "string"}},
            "final_advice": {"type": "string"},
            "error": {"type": "string"},
        },
    },
    handler=_revise_clause,
    requires_llm=True,
    permissions=("uploader",),
    tags=("llm", "revision"),
    notes=(
        "底层是 3 次顺序 LLM 调用（Leader→Follower→Self-QA），Skill 层把它视为**一个能力**，"
        "不改变其调用次数与顺序。注意：**当前 verified / final_advice 不落库、不 gate 结果、"
        "前端不读取**（Self-QA 结论暂无消费者）—— 这是已知事实，Skill 层如实透出而不掩饰。"
        "契约明确：本 Skill 不是 Multi-Agent 实现。"
    ),
))


# ══════════════════════════════════════════════════════════════════════
# 12. draft_clause（单次 LLM 起草，非 Leader-Follower）
# ══════════════════════════════════════════════════════════════════════

def _draft_clause(payload: dict) -> dict:
    instruction = _require_str(payload, "instruction")
    contract_type = payload.get("contract_type") or ""
    rag_context = payload.get("rag_context")
    if rag_context is not None and not isinstance(rag_context, list):
        raise SkillInputError("rag_context 需为数组或省略")
    position_hint = payload.get("position_hint")
    if position_hint is not None and not isinstance(position_hint, str):
        raise SkillInputError("position_hint 需为字符串或省略")

    generate_clause = _lazy("ai.reviser", "generate_clause")
    return generate_clause(instruction, contract_type, rag_context, position_hint)


register(make_skill(
    name="draft_clause",
    description="新增条款起草：依据指令 + 法条 + 参考范本起草一条可直接插入合同的条款。",
    category=CATEGORY_REVISE,
    source="ai.reviser.generate_clause",
    input_schema={
        "type": "object",
        "properties": {
            "instruction": {"type": "string", "description": "起草要求（如\"补充不可抗力条款\"）。"},
            "contract_type": {"type": "string"},
            "rag_context": {
                "type": "array",
                "items": {"type": "object"},
                "description": "相关法条 / 参考范本（可来自 retrieve_knowledge / retrieve_templates）。",
            },
            "position_hint": {"type": "string", "description": "建议插入位置（仅提示，不决定落点）。"},
        },
        "required": ["instruction"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "clause_text": {"type": "string"},
            "explanation": {"type": "string"},
            "legal_basis": {"type": "array", "items": {"type": "string"}},
            "error": {"type": "string"},
        },
    },
    handler=_draft_clause,
    requires_llm=True,
    permissions=("uploader",),
    tags=("llm", "drafting"),
    notes=(
        '底层为**单次** LLM 调用（reviser.py:164 自述「新增是起草而非修订，不走 Leader-Follower」），'
        "与 revise_clause 是两个独立 Skill；调用点原本靠端点里的 if is_add 分派，注册后可按名分派。"
    ),
))


__all__ = [name for name in (
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
)]
