"""services.feedback_experience — 反馈 → 学习经验 的唯一编排层

职责（保持最小）：把"已批准的人工反馈"物化成一条可检索的 `FeedbackExperience`，
以及撤销时把经验从检索中排除。**不做任何规则改动、不做任何自动学习。**

## 三条不可违反的边界

1. **只有人工批准才产生经验**：本模块的所有写入口都要求 `FeedbackLog.status == 'reviewed'`
   且批准者与提交者/审核者分离；未批准的反馈永不进入 Feedback RAG。
2. **经验正文只描述"应重点核查什么"**：`experience_text` 由**静态模板 + 人工意见原文**拼成
   （见 `CHECKPOINT_HINTS`），逐字都是"去核查哪些字段/哪些位置"，
   **绝不包含"本次应判定为 X 风险"这类结论**，也不得让历史标签替代当前合同事实。
3. **不碰裁决层**：本模块不 import、不调用 `rule_engine` / `evidence_adjudicator`，
   也不修改任何 R01–R13 参数。

`adjudicator_rule_suspect` 类反馈只进"规则反馈池"，**明确不可批准进入学习**：
它暴露的是 Python 裁决规则本身的问题，必须由人工/开发单独改规则并跑全量回归。
"""
import logging

from datetime import datetime

from sqlalchemy.orm import Session

from database import utcnow_naive
from models.feedback_log import FeedbackLog
from models.feedback_experience import FeedbackExperience
from services import eval_isolation

logger = logging.getLogger(__name__)

# ── 归因枚举（与前端下拉、API 校验共用同一份定义）──────────────────────
REASON_CLAUSE_EXISTS = "clause_exists"
REASON_EVIDENCE_GAP = "evidence_gap"
REASON_SEMANTIC_ERROR = "semantic_error"
REASON_CONTRACT_TYPE_ERROR = "contract_type_error"
REASON_ADJUDICATOR_RULE_SUSPECT = "adjudicator_rule_suspect"
REASON_OTHER = "other"

FEEDBACK_REASONS = (
    REASON_CLAUSE_EXISTS,
    REASON_EVIDENCE_GAP,
    REASON_SEMANTIC_ERROR,
    REASON_CONTRACT_TYPE_ERROR,
    REASON_ADJUDICATOR_RULE_SUSPECT,
    REASON_OTHER,
)

# ── 生命周期状态（feedback_logs.status）────────────────────────────────
ST_PENDING = "pending"
ST_REVIEWED = "reviewed"
ST_REJECTED = "rejected"
ST_APPROVED_FOR_LEARNING = "approved_for_learning"
ST_ACTIVE = "active"
ST_REVOKED = "revoked"

FEEDBACK_STATUSES = (
    ST_PENDING, ST_REVIEWED, ST_REJECTED, ST_APPROVED_FOR_LEARNING, ST_ACTIVE, ST_REVOKED,
)

# 经验的可见性状态（feedback_experiences.status）
EXP_ACTIVE = "active"
EXP_REVOKED = "revoked"

# 提交动作 → 学习标签（human_label）。只映射"风险判断"类语义：
#   confirmed    → 人工确认风险成立
#   false_positive → 人工判定误报（重要负样本）
#   corrected    → 人工修正了等级/细节，但**认可风险成立** → 归入 confirmed
#   supplemented → 补充"模型漏掉的风险"，语义上不是对当前记录的风险判断，
#                  第一阶段不沉淀（需要另一套字段设计），只留痕
LABEL_CONFIRMED = "confirmed"
LABEL_FALSE_POSITIVE = "false_positive"

_ACTION_TO_LABEL = {
    "confirmed": LABEL_CONFIRMED,
    "corrected": LABEL_CONFIRMED,
    "false_positive": LABEL_FALSE_POSITIVE,
}

# 第一阶段支持的学习类型
KIND_RISK_JUDGEMENT = "risk_judgement"

MAX_QUERY_CHARS = 500
MAX_COMMENT_CHARS = 300

# ── 「应重点核查什么」静态提示（**不含任何风险结论**）────────────────────
# 逐条对应 SYSTEM_PROMPT_EVIDENCE 里该风险要抽取的字段与常见误判来源。
# 这张表是"经验"的真正价值所在：它告诉 LLM 去哪里找事实，而不是告诉它结论。
CHECKPOINT_HINTS = {
    "R01": {
        LABEL_FALSE_POSITIVE: (
            "重点核查违约金是否**写在原条款里**按比例约定：注意区分"
            "『合同总额 30% 为限』这类封顶表述、履约保证金、以及"
            "『固定金额/一次性百分比』，它们都不是日/月比例违约金。"
        ),
        LABEL_CONFIRMED: "重点核查是否存在**明确的日/月违约金比例**（rate+unit 两字段都要有原文依据）。",
    },
    "R02": {
        LABEL_FALSE_POSITIVE: (
            "重点核查赔偿范围是否**已被限定**：出现『实际损失』『直接损失』"
            "『以合同总价为限』等边界表述，或已约定责任上限的，都不属于无限责任。"
        ),
        LABEL_CONFIRMED: "重点核查是否存在『全部损失/一切责任/不设上限/含预期利润或间接损失』等绝对化表述。",
    },
    "R03": {
        LABEL_FALSE_POSITIVE: (
            "重点核查单方权利是否**归属于我方（甲方）**、以及是否已配套补偿或前置条件；"
            "另外『单方暂停、调整服务范围、不续签、考核』都不属于解除权。"
        ),
        LABEL_CONFIRMED: "重点核查是否存在**对方（乙方）**可任意解除/终止且无对等补偿的条款。",
    },
    "R04": {
        LABEL_FALSE_POSITIVE: (
            "重点核查管辖约定是否**真的偏向对方**：甲方所在地、原告住所地、工程所在地、"
            "标的物所在地均不属于不利；『法院+仲裁并列』也不构成；条款空白/未填写不得臆测。"
        ),
        LABEL_CONFIRMED: "重点核查是否明确约定由**明显偏向乙方**的法院管辖（location_party 字段要有原文依据）。",
    },
    "R05": {
        LABEL_FALSE_POSITIVE: (
            "重点核查保密期限是否为**法定义务**（statutory_basis），或已写明具体年限；"
            "注意『不因合同终止而终止』要与『直至依法公开』区分。"
        ),
        LABEL_CONFIRMED: "重点核查是否存在『永久/无限期/直至信息公开』等无边界保密期限的原文表述。",
    },
    "R06": {
        LABEL_FALSE_POSITIVE: (
            "重点核查成果归属**是否已约定且有对价**（has_consideration）；"
            "仅『未逐项细化背景IP/改进成果』不构成权益失衡。"
        ),
        LABEL_CONFIRMED: "重点核查是否存在无偿转移、甲方独占收益而乙方无合理补偿的原文依据。",
    },
    "R07": {
        LABEL_FALSE_POSITIVE: (
            "重点核查付款义务是否与交付节点**挂钩**（has_milestone）；"
            "正常进度款、合理质保金、验收后付款不单独构成失衡。"
        ),
        LABEL_CONFIRMED: "重点核查付款期限/范围是否与交付明显脱钩（要抽取 prepay_ratio 与 tail_ratio 原文依据）。",
    },
    "R08": {
        LABEL_FALSE_POSITIVE: (
            "重点核查是否**引用了客观可执行依据**：国家/行业标准、招标文件、采购需求、"
            "技术规格书、第三方测试报告。这类依据常写在「（N）」子项、附件或"
            "『技术协议另附』处；**不能因正文未重复列出指标就认为不存在**。"
        ),
        LABEL_CONFIRMED: "重点核查是否**只有『验收合格』这类无判据表述**，而没有任何标准/规格/报告的引用。",
    },
    "R09": {
        LABEL_FALSE_POSITIVE: (
            "重点核查是否已存在不可抗力处理机制（定义/免责/通知/证明/解除/后果处理**任一即可**）。"
            "这类条款常写在『其他约定』『免责』段，标题未必叫『不可抗力』；"
            "另外要区分『可能存在不可抗力风险』这类提示句（不算条款）。"
        ),
        LABEL_CONFIRMED: "重点核查是否**通篇没有任何不可抗力处理机制**的原文依据。",
    },
    "R10": {
        LABEL_FALSE_POSITIVE: (
            "重点核查是否**两个条件同时满足**：期限 ≥5 年 且 地域为全国/全行业/主营业务；"
            "人员更换、分包限制、客户保护均不属于竞业。"
        ),
        LABEL_CONFIRMED: "重点核查 duration_years 与 scope 两个字段是否都有原文依据且同时成立。",
    },
    "R11": {
        LABEL_FALSE_POSITIVE: (
            "重点核查续约模式：固定期限、履行完毕失效、双方协商续签、工程延期都不属于自动续约；"
            "注意 has_exit_channel（期满前书面通知退出机制）的存在与否。"
        ),
        LABEL_CONFIRMED: "重点核查是否存在『未通知/未提出异议即自动延长』的沉默续约原文表述。",
    },
    "R12": {
        LABEL_FALSE_POSITIVE: (
            "重点核查是否**确实存在个人信息/敏感个人信息的处理对象**；"
            "『可能涉及/通常会涉及』不足；业务数据不等于个人信息。"
        ),
        LABEL_CONFIRMED: "重点核查 has_personal_data 与 has_authorization_boundary 两个字段的原文依据。",
    },
    "R13": {
        LABEL_FALSE_POSITIVE: "重点核查名义类型与实际权利义务是否一致（本条由规则信号主导，人工结论仅供参考）。",
        LABEL_CONFIRMED: "重点核查是否存在名义与实质不一致的原文特征。",
    },
}

_GENERIC_HINT = "请重点核查该风险对应的事实字段是否有原文依据，逐字摘录原文，找不到就留空。"

_RULE_POOL_SCOPE_NOTE = (
    "该反馈指向 Python 裁决规则本身（adjudicator_rule_suspect），"
    "只进入规则反馈池，禁止自动修改规则。"
)


def feedback_rag_enabled() -> bool:
    """Feedback RAG 是否启用（默认 False）。

    只由生产审核链路读取；**官方评测路径不读本开关**，因此评测 prompt 恒定不变。
    """
    try:
        from config import FEEDBACK_RAG_ENABLED
        return bool(FEEDBACK_RAG_ENABLED)
    except Exception:
        return False


def _risk_name(risk_type: str) -> str:
    try:
        from ai.auditor.recommendation_engine import RISK_NAMES  # 延迟导入，避免无谓的重依赖
        return RISK_NAMES.get(risk_type, risk_type or "")
    except Exception:
        return risk_type or ""


def _clip(text, limit: int) -> str:
    t = str(text or "").replace("\r", " ").strip()
    return t[:limit]


def action_to_label(action_type: str) -> str | None:
    """提交动作 → 学习标签；不可学习的动作返回 None。"""
    return _ACTION_TO_LABEL.get(action_type)


def build_experience_text(
    risk_type: str, human_label: str, feedback_reason: str | None,
    human_comment: str | None, corrected_risk: dict | None = None,
) -> str:
    """构造给 LLM 看的经验正文。

    **只写"应重点核查什么"**：
    - 结论段只做历史陈述（"曾被人工判定为误报/成立"），不写"本次应判什么"；
    - 核查点来自静态白名单表 `CHECKPOINT_HINTS`（确定性、可审计、无 LLM）；
    - 人工意见原文附在【人工意见】下，并显式标注为参考、不得替代当前合同事实。
    """
    rt = (risk_type or "").strip()
    name = _risk_name(rt)
    label_cn = "误报（该风险实际不成立）" if human_label == LABEL_FALSE_POSITIVE else "成立（风险确实存在）"

    parts = [f"[历史人工结论] {rt} {name} 曾被人工判定为{label_cn}。"]
    if feedback_reason:
        parts.append(f"[归因] {feedback_reason}。")
    if corrected_risk:
        try:
            lvl = (corrected_risk or {}).get("risk_level")
        except Exception:
            lvl = None
        if lvl:
            parts.append(f"[人工修正] 风险等级修正为 {lvl}（仅等级修正，风险成立）。")
    if human_comment:
        parts.append(f"[人工意见] {_clip(human_comment, MAX_COMMENT_CHARS)}")

    hint = (CHECKPOINT_HINTS.get(rt) or {}).get(human_label) or _GENERIC_HINT
    parts.append(f"[应重点核查] {hint}")
    parts.append(
        "[约束] 以上仅为历史人工参考：不得据此输出任何风险判定字段；"
        "当前合同没有的事实一律照实填 false/空，与当前合同原文冲突时以原文为准。"
    )
    return "\n".join(parts)


def check_approvable(fb: FeedbackLog) -> None:
    """校验某条反馈是否允许被批准进入学习；不允许时抛 ValueError（调用方转 4xx）。"""
    if fb.status not in (ST_REVIEWED, ST_APPROVED_FOR_LEARNING):
        raise ValueError(
            f"反馈当前状态为 {fb.status}，只有 reviewed（已审核）的反馈才可批准进入学习。"
        )
    if fb.feedback_reason == REASON_ADJUDICATOR_RULE_SUSPECT:
        raise ValueError(_RULE_POOL_SCOPE_NOTE)
    if (fb.target_type or "risk") != "risk":
        raise ValueError("第一阶段只支持 risk（风险判断）类反馈进入学习。")
    if action_to_label(fb.action_type) is None:
        raise ValueError(
            "该反馈类型（supplemented 补充类）第一阶段不沉淀为学习经验："
            "它描述的是模型漏报，需要另一套字段设计，已在第二阶段规划中。"
        )


def build_experience_payload(
    fb: FeedbackLog, contract=None, record=None, eval_check: str = "",
) -> dict:
    """把一条已审核反馈整理成经验字段（纯函数，便于单测）。"""
    label = action_to_label(fb.action_type)
    if label is None:
        raise ValueError("该反馈动作不产生学习经验")

    original_text = ""
    clause_text = ""
    if record is not None:
        pos = record.clause_position if isinstance(record.clause_position, dict) else None
        original_text = (pos or {}).get("original_text") or ""
        clause_text = record.clause_text or ""
    model_result = {}
    if record is not None:
        model_result = {
            "risk_type": record.risk_type,
            "risk_level": record.risk_level,
            "clause_text": record.clause_text,
            "clause_position": record.clause_position,
            "reason": record.reason,
            "suggestion": record.suggestion,
            "detection_method": record.detection_method,
            "confidence": record.confidence,
            "audit_batch": record.audit_batch,
        }

    query_text = (original_text or clause_text or fb.contract_type or "").strip()
    if not query_text:
        raise ValueError("该反馈缺少可用的条款原文（锚点与 clause_text 均为空），无法构成可检索经验。")

    return {
        "kind": KIND_RISK_JUDGEMENT,
        "status": EXP_ACTIVE,
        "contract_type": fb.contract_type,
        "risk_type": fb.risk_type,
        "human_label": label,
        "feedback_reason": fb.feedback_reason,
        "query_text": _clip(query_text, MAX_QUERY_CHARS),
        "experience_text": build_experience_text(
            fb.risk_type or "", label, fb.feedback_reason, fb.comment, fb.corrected_risk
        ),
        "original_text": original_text or None,
        "contract_name": (getattr(contract, "file_name", None) or None),
        "model_evidence": fb.model_evidence,
        "model_result": model_result or fb.model_result,
        "human_comment": fb.comment,
        "approved_by": fb.approved_by,
        "approved_at": utcnow_naive(),
        "collection_name": "feedback_experiences",
        "eval_check": eval_check,
    }


# ══════════════════════════════════════════════════════════════════════
# 编排：批准 → 建经验 → 入索引；撤销 → 出索引
# ══════════════════════════════════════════════════════════════════════

def _index_module():
    """延迟导入 Feedback RAG 模块。

    延迟的原因：`ai.rag.vector_store` 在导入期会拉起 chromadb / sentence_transformers，
    而本模块被 `api/feedback.py` 引用；审核与反馈接口都不应因此付出冷启动成本。
    """
    from ai.rag import feedback_store
    return feedback_store


def approve_and_materialize(db: Session, fb: FeedbackLog, approver) -> tuple[FeedbackExperience, dict]:
    """批准一条反馈并物化为经验（含入库索引）。

    流程与状态迁移（严格对应设计生命周期）：
        reviewed → approved_for_learning →（经验行落库 + 尝试入索引）→ active

    索引写入是 **best-effort**：失败时经验行仍保留（状态 active）、
    `doc_id` 为空，反馈停在 `approved_for_learning` 以便重试；
    由于检索时 BM25 一路直接从数据库取正文，入索引失败不会让整条经验失效。

    Returns:
        (experience, index_result)
    """
    check_approvable(fb)
    if fb.approved_by is not None and fb.approved_by != getattr(approver, "id", None):
        raise ValueError("该反馈已由其他管理员批准（批准人不可变更）。")
    if fb.approved_by is None:
        fb.approved_by = getattr(approver, "id", None)
        fb.approved_at = utcnow_naive()
    fb.status = ST_APPROVED_FOR_LEARNING
    db.flush()

    contract = db.query(_contract_model()).filter(_contract_model().id == fb.contract_id).first() if fb.contract_id else None
    record = db.query(_audit_model()).filter(_audit_model().id == fb.record_id).first()

    # 数据泄漏防护：合同正文若命中官方评测语料，拒绝沉淀为经验
    eval_check = ""
    if contract is not None and getattr(contract, "parsed_text", None):
        eval_check = eval_isolation.check_not_eval_sample(contract.parsed_text)

    existing = db.query(FeedbackExperience).filter(
        FeedbackExperience.source_feedback_id == fb.id
    ).first()
    if existing is not None and existing.status == EXP_ACTIVE:
        # 幂等：已存在则只补索引，不重复建行
        exp = existing
    else:
        payload = build_experience_payload(fb, contract=contract, record=record, eval_check=eval_check)
        payload.pop("eval_check", None)
        exp = FeedbackExperience(source_feedback_id=fb.id, **payload)
        db.add(exp)
        db.flush()

    index_result = {"indexed": False, "doc_id": None, "index_version": None, "error": None}
    try:
        store = _index_module()
        index_result = store.add_experience(exp)
        if index_result.get("indexed"):
            exp.doc_id = index_result.get("doc_id")
            exp.index_version = index_result.get("index_version")
    except Exception as e:  # 入索引失败绝不影响反馈批准本身
        index_result = {"indexed": False, "doc_id": None, "index_version": None, "error": str(e)}
        logger.error("经验入索引失败（经验行已保留，可重试）: feedback_id=%s, %s", fb.id, e)

    fb.status = ST_ACTIVE if index_result.get("indexed") else ST_APPROVED_FOR_LEARNING
    db.commit()
    db.refresh(exp)
    return exp, index_result


def revoke_experience(db: Session, exp: FeedbackExperience, admin, reason: str = "") -> dict:
    """撤销一条经验：从检索中排除 + 精确删除 Chroma 文档 + 留痕。"""
    if exp.status == EXP_REVOKED:
        return {"removed": False, "error": None, "already_revoked": True}
    result = {"removed": False, "error": None}
    try:
        store = _index_module()
        result = store.remove_experience(exp.id)
    except Exception as e:
        result = {"removed": False, "error": str(e)}
        logger.error("经验从索引删除失败（DB 状态仍会置 revoked，检索已排除）: exp_id=%s, %s", exp.id, e)

    exp.status = EXP_REVOKED
    exp.revoked_by = getattr(admin, "id", None)
    exp.revoked_at = utcnow_naive()
    exp.revoke_reason = reason or None

    fb = db.query(FeedbackLog).filter(FeedbackLog.id == exp.source_feedback_id).first()
    if fb is not None:
        fb.status = ST_REVOKED
    db.commit()
    return result


# 延迟取模型类，避免 services → models 的导入顺序问题（并让测试可替换）
def _contract_model():
    from models.contract import Contract
    return Contract


def _audit_model():
    from models.audit_record import AuditRecord
    return AuditRecord
