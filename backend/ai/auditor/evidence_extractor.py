"""evidence_extractor.py — LLM 事实证据抽取层（v6.3：定点修复，其余冻结）

v6.3 相对 v6.2（按 GPT 定夺，一次只改四个问题）：
- R04：补全风险条款「独立输入」——从 content 里切出 supplement_clause 单独抽取，
  合并时 supplement 覆盖 body（解决"正文与补全条款冲突、后者权重不足"）。
- R09：回退 has_force_majeure + force_majeure_evidence（删除 evidence_source 枚举），
  代码做"含机制词才算条款"的 sanity check。
- R05：加 statutory_basis 布尔（法定义务 vs 合同约定），代码只对"合同约定无限期"判 R05。
- R02：加 ordinary_compensation 布尔（普通违约赔偿 vs 无限责任），代码排除普通赔偿。
- R08：暂不大改（保留 objective_basis 布尔 + basis_evidence）。
- R01/R03/R06/R07/R10/R11/R12：冻结。
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from ai.llm_client import llm_client
from ai.utils import extract_json
from ai.chunker import split_chunks

logger = logging.getLogger(__name__)

MAX_CHARS = 20000

SYSTEM_PROMPT_EVIDENCE = """你是一位合同条款事实抽取助手。唯一任务：从合同原文中**只抽取客观事实**，**绝对不要判断是否构成风险、不要下法律结论、不要输出 risk/is_risk 字段**。

评审视角固定：我方=甲方（采购人/委托人/披露方/发包人）；对方=乙方（供应商/受托人/服务方/承包人）。

规则：找不到填 null（数字）/ false（布尔）/ ""（文本），绝不臆测。比例用小数（5‰=0.005，20%=0.2）。

只输出 JSON 对象（不要任何前缀后缀）：
{
  "contract_type": "买卖/租赁/承揽/建设工程/技术/委托/物业服务/中介/保密/劳动/无名",
  "is_delivery_type": true或false,
  "R01_违约金": {"exists": true/false, "rate": 0.0, "unit": "daily/monthly/one_time", "basis": "合同总价/逾期金额/其他", "liable_party": "甲方/乙方/双方/未写明", "clause_text": "原文"},
  "R02_责任": {"absolute_wording": true/false, "absolute_text": "原文", "has_cap": true/false, "scope": "实际损失/直接损失/全部损失/含预期利润或间接损失/未明确", "liable_party": "甲方/乙方/双方/未写明"},
  "R03_单方权利": {
    "termination": {"party": "甲方/乙方/双方/无", "arbitrary": true/false, "no_compensation": true/false, "clause_text": "原文"},
    "suspension": {"party": "甲方/乙方/双方/无", "arbitrary": true/false, "no_compensation": true/false, "clause_text": "原文"},
    "change": {"party": "甲方/乙方/双方/无", "arbitrary": true/false, "no_compensation": true/false, "clause_text": "原文"}
  },
  "R04_管辖": {"dispute_method": "诉讼/仲裁/法院加仲裁并列/未约定", "location_text": "原文", "location_party": "甲方/乙方/原告/工程所在地/标的物所在地/合同签订地/其他/未约定", "evidence": "原文"},
  "R05_保密": {"duration": "永久/无限/不因终止而终止/直至公开/未写明", "statutory_basis": true/false, "duration_evidence": "原文"},
  "R06_IP": {"ownership": "归甲方/归乙方/双方共有/未明确", "has_consideration": true/false, "consideration_text": "原文", "imbalance_text": "原文"},
  "R07_付款": {"prepay_ratio": 0.0, "tail_ratio": 0.0, "has_milestone": true/false, "payment_evidence": "原文"},
  "R08_验收": {"objective_basis": true/false, "basis_evidence": "原文(客观验收依据的具体条款；objective_basis=true时必须有原文，否则填false)"},
  "R09_不可抗力": {"has_force_majeure": true/false, "force_majeure_evidence": "原文(合同不可抗力条款原文，若只是'可能存在不可抗力风险'这类提示句则填false且evidence留空)"},
  "R10_竞业": {"has_noncompete": true/false, "duration_years": null或数字, "scope": "全国/主营业务/全行业/其他/未写明", "clause_text": "原文"},
  "R11_续约": {"mode": "silence_auto_renewal/主动续签/固定期限/无", "has_exit_channel": true/false, "clause_text": "原文"},
  "R12_数据": {"has_personal_data": true/false, "data_type": "个人信息/敏感个人信息/业务数据/无", "has_authorization_boundary": true/false, "clause_text": "原文"}
}

字段说明：
- R02 scope：该赔偿条款覆盖的损失范围——"全部损失"或"含预期利润或间接损失"才是无限责任；"实际损失/直接损失"属有边界赔偿，不是无限责任。只抽取原文写明的范围，不要判断是否合理。
- R05 statutory_basis：该保密义务是否源于法定义务（国家秘密/工作秘密/商业秘密法定保护，如"依法承担保密义务""直至依法公开"），而非合同额外约定的无限期保密（true=法定义务）。
- R09 force_majeure_evidence：必须是"合同约定的不可抗力条款"原文（含通知/解除/免责/顺延/不承担责任等机制性内容）。如果只是"可能存在不可抗力风险""存在不可预计因素"这类风险提示句，则 has_force_majeure 填 false、evidence 留空。"""


def _split_supplement(full_text: str):
    """把 content 切成 (body, supplement)：supplement = 文末「补全风险条款」段。"""
    marker = "## 补全风险条款"
    idx = full_text.find(marker)
    if idx < 0:
        return full_text, ""
    return full_text[:idx], full_text[idx:]


def _extract_chunk(chunk: str):
    """对单个文本块抽取证据。

    返回语义（BUG-008 收口：失败与「成功但无风险事实」不再混用 {}）：
    - 成功：返回 dict（含空 dict {} = 成功但该块无任何风险事实）
    - 失败：返回 None（LLM 调用异常 / JSON 解析失败 / 返回非 dict）

    之前失败与空结果都返回 {}，下游无法区分「failed」与「success + empty」，
    失败块会被当成正常空结果静默吞掉。现改为 None 表达失败，由 _extract_one 统计。
    """
    try:
        resp = llm_client.chat(
            prompt=SYSTEM_PROMPT_EVIDENCE + "\n\n请抽取以下合同的事实：\n" + chunk,
            temperature=0.0,
        )
        ev = extract_json(resp)
        if isinstance(ev, dict):
            return ev
        logger.warning("证据抽取：块返回非 dict（解析为 %s），视为失败", type(ev).__name__)
        return None
    except Exception as e:
        logger.error("证据抽取失败: %s", e)
        return None


def _extract_one(text: str) -> dict:
    """对一段文本抽取证据（超长分块并行合并）。

    返回 {"evidence", "failed_chunks", "total_chunks"}（BUG-008 收口）：
    - 失败块（_extract_chunk 返回 None）不计入 evidence，但计入 failed_chunks，失败可统计、可识别；
    - 成功块结果全部保留（一个块失败不丢弃其它成功块结果，要求4）；
    - 成功但无风险事实的块返回 {}（dict），与失败块 None 语义不同，不会与失败混淆（要求3）；
    - 空文本（0 块）返回 evidence={} 且 total=0，由上层按 failed 处理，不静默当 0 风险。
    """
    chunks = split_chunks(text, MAX_CHARS)
    if not chunks:
        return {"evidence": {}, "failed_chunks": 0, "total_chunks": 0}
    if len(chunks) == 1:
        ev = _extract_chunk(chunks[0])
        if ev is None:
            logger.warning("证据抽取：单块失败（可能超时/限流/JSON 解析失败），该块风险要素丢失")
            return {"evidence": {}, "failed_chunks": 1, "total_chunks": 1}
        return {"evidence": ev, "failed_chunks": 0, "total_chunks": 1}
    merged = {}
    fail = 0
    with ThreadPoolExecutor(max_workers=min(len(chunks), 6)) as ex:
        for ev in ex.map(_extract_chunk, chunks):
            if ev is None:
                fail += 1
            else:
                merged = _merge(merged, ev)
    if fail:
        logger.warning("证据抽取：%d/%d 块失败（可能超时/限流/JSON 解析失败），相关风险要素可能丢失", fail, len(chunks))
    return {"evidence": merged, "failed_chunks": fail, "total_chunks": len(chunks)}


def _merge(base: dict, new: dict) -> dict:
    """分块合并：非空字段覆盖空字段（不覆盖已有值）。"""
    if not base:
        return new
    if not new:
        return base
    for k, v in new.items():
        if k not in base:
            base[k] = v
            continue
        bv = base[k]
        if isinstance(v, dict) and isinstance(bv, dict):
            for kk, vv in v.items():
                if _is_empty(bv.get(kk)) and not _is_empty(vv):
                    bv[kk] = vv
        elif _is_empty(bv) and not _is_empty(v):
            base[k] = v
    return base


_NEUTRAL = {"", "无", "未写明", "未约定", "其他", "none", "固定期限", "未明确", "不适用"}


def _is_neutral(v) -> bool:
    if v is None or v is False or v == "":
        return True
    if isinstance(v, str) and v.strip() in _NEUTRAL:
        return True
    return False


def _merge_override(base: dict, new: dict) -> dict:
    """补全风险条款字段级覆盖：只在 supplement 给出明确非空信号时才覆盖。
    "无/未写明/固定期限/未明确"等中性值不覆盖正文（避免误杀写在正文里的风险，如 R11）。"""
    if not new:
        return base
    if not base:
        return new
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            for kk, vv in v.items():
                if not _is_neutral(vv):
                    base[k][kk] = vv
        elif not _is_neutral(v):
            base[k] = v
    return base


def _is_empty(v) -> bool:
    return v is None or v == "" or v is False


def _status_for(failed: int, total: int) -> str:
    """由失败块数推导抽取状态：success / partial / failed。"""
    if total <= 0 or failed >= total:
        return "failed"
    if failed == 0:
        return "success"
    return "partial"


def extract_evidence_detailed(full_text: str) -> dict:
    """抽取证据并返回状态信封（BUG-003/BUG-008 收口）。

    返回 {"evidence", "status", "failed_chunks", "total_chunks"}：
    - status == "success"：全部块抽取成功（evidence 可能为空 dict = 无风险证据，属正常 0 风险）
    - status == "partial"：部分块失败，保留成功块结果继续裁决（不静默吞失败、不丢弃成功结果）
    - status == "failed"：全部块失败，evidence 为空 dict（上层必须降级，不得当 0 风险）

    正文与补全条款（"## 补全风险条款"段）各自分块并行抽取，失败块数跨两者累加。
    """
    body, supplement = _split_supplement(full_text)
    if supplement.strip():
        # 正文与补全条款并行抽取（各自动分块），缩短审核等待
        with ThreadPoolExecutor(max_workers=2) as ex:
            body_fut = ex.submit(_extract_one, body)
            supp_fut = ex.submit(_extract_one, supplement)
            body_r = body_fut.result()
            supp_r = supp_fut.result()
        evidence = _merge_override(body_r["evidence"], supp_r["evidence"])
        total = body_r["total_chunks"] + supp_r["total_chunks"]
        failed = body_r["failed_chunks"] + supp_r["failed_chunks"]
    else:
        r = _extract_one(body)
        evidence = r["evidence"]
        total = r["total_chunks"]
        failed = r["failed_chunks"]
    return {
        "evidence": evidence,
        "status": _status_for(failed, total),
        "failed_chunks": failed,
        "total_chunks": total,
    }


def extract_evidence(full_text: str) -> dict:
    """兼容旧调用（评测脚本 run_evidence / ab_normalize 等）：仅返回合并后的 evidence dict。"""
    return extract_evidence_detailed(full_text)["evidence"]
