"""llm_auditor.py —— 已归档（非生产路径）。

旧「precise = 规则 + LLM 直审」引擎（v5-v124 口径）。生产链路已切换为
evidence_extractor + evidence_adjudicator（v6.4 冻结），本模块不再被生产调用，
也不再从 ai.auditor 包命名空间再导出。

当前唯一引用方（均为开发验证 / 旧基线，非生产）：
- evaluate/evaluate_risks.py（旧 precise 引擎基线）
- evaluate/export_fp.py（旧 FP 导出）
- test_llm.py（冒烟 test_07）

请勿重新接回生产链路。
"""
from ai.chunker import split_chunks
from ai.llm_client import llm_client
from ai.utils import extract_json_list
from ai.confidence import clamp_confidence, LLM_FALLBACK_CONFIDENCE
import logging

logger = logging.getLogger(__name__)

# 送入 LLM 的单块文本上限（字符）。超长合同会按条款边界分块逐块审核，
# 尾部内容不再被截断丢弃。
MAX_AUDIT_CHARS = 12000

# 注：R13「疑似名实不符」不在 LLM 审核范围内——由规则引擎 NAME_REALITY_SIGNALS
# 信号对主导（名义+实质同时命中才示警），LLM 语义审计易过检（历史 71 次误报 vs gold 2 条），
# 且定性（假外包真派遣/名为买卖实为借贷等）按产品设定交人工，故 R13 仅规则示警、不进 LLM。
SYSTEM_PROMPT_AUDIT = """你是一位资深合同审核律师。请逐条审核以下合同。评审视角固定：我方=甲方（采购人/委托人/披露方/发包人）；对方=乙方（供应商/受托人/服务方/承包人）。模棱两可的一律不标。

一、评分风险（R01–R12，严格按以下口径，缺一不可、宁缺勿滥）：

R01 违约金过高：仅按原始约定比例判断——日违约金≥5‰（千分之五）才标。不得用"固定金额、一次性百分比、违约金+赔偿叠加、履约保证金、潜在累计金额、年化折算"替代原始阈值；不得用"如果逾期很多天"等假设情景推导（民法典585）。
R02 无限责任：必须存在责任范围未限定、明确"全部损失/一切责任/全额赔偿/不设上限"等绝对化表述才标；普通"赔偿实际损失""赔偿因此造成的损失"、已限定为直接损失、或已设责任上限的，不标（民法典584）。
R03 单方解约权：必须存在明确的单方任意解除/终止合同权且无对等补偿才标（民法典933/787）；单方暂停、单方调整服务范围、报告履约、转租、考核、不续签、自动续约等不得归入；解除权属于我方甲方本身而非对方获得的，不标。
R04 管辖条款不利：仅当合同明确约定由明显偏向对方（乙方）一方的法院管辖才标；甲方所在地、我方所在地、原告住所地、工程所在地、标的物所在地等不得仅因"异地"判R04；仲裁地点/仲裁机构≠法院管辖；条款未填写、引用未展开、管辖空白不得臆测为对方所在地；"同时约定法院+仲裁"不构成R04（民诉法35）。
R05 保密期间不合理：仅识别明确"永久、无限期、不因合同终止而终止、直至信息公开/进入公共领域"等无限机制才标；未写期限不得推定永久；2/3/5/10年等明确期限单独判断；竞业限制、自动续约条款不得归入R05（民法典501）。
R06 知识产权权益失衡：不得仅因"合同未明确IP归属"判定；必须确认不存在相应对价、或存在明显单方权益失衡（乙方成果被无偿转移、甲方独占收益且乙方无合理补偿）才标；已明确约定成果归属且有对价买断的，不因背景IP/改进成果/使用权未逐项细化而标（民法典859）。
R07 付款义务失衡：不得仅根据预付款比例、质保金比例、付款周期长短、是否无息、是否约定逾期付款违约金判断；必须证明付款义务与实际交付/服务贡献明显脱钩、或付款期限/范围明显失衡；正常租金预付、工程进度款、合理质保金、居间成功报酬、验收后付款不得单独判（民法典525/526、965）。
R08 验收机制缺失：判断"是否存在客观可执行的合格判断依据"，不要求正文出现量化数字；明确引用国家/行业标准、招标文件、采购需求、技术规格书、第三方测试报告的，视为存在验收标准，不得仅因正文未重复列出指标而判缺失；仅对交付成果类合同（买卖/承揽/建设工程/技术开发）标（民法典621-623、845）。
R09 不可抗力缺失："缺失"指完全没有不可抗力处理机制；已有不可抗力定义/免责/通知/证明/解除/后果处理之一或多项的，不得因条款不够完整、未细化费用分担、未列举具体事件而判；仅交付型合同（买卖/承揽/建设工程/技术开发）缺失才标，租赁/中介/委托/物业/框架/劳动/保密不标（民法典180/590）。
R10 竞业限制过宽：必须同时满足"限制期限≥5年"且"地域全国范围或禁止对方全部/核心业务"，缺一不可；人员更换、分包限制、客户保护、特定交易期权不得认定（劳动合同法23/24）。
R11 自动续约陷阱：必须出现"期限届满后，在未通知/未提出终止/未提出异议的情况下自动延长"的沉默续约机制才标；仅有固定期限、履行完毕失效、双方协商续签、工程延期不得推定（民法典734）。
R12 数据隐私不当：不得依据合同类型或业务场景推测存在个人信息处理；必须从合同文本找到明确的个人信息/敏感个人信息/用户数据处理对象及相应处理行为或保护缺失；"可能涉及/通常会涉及/业务中可能产生数据"不足（个保法13/23）。

二、扩展风险（extended_risks，非评分，禁止用 R01-R12 编号）：
现实中值得法务关注、但不符合上述严格口径的问题，放入 extended_risks，例如：管辖在异地但不偏向对方、付款比例偏高但属正常商业结算、非交付型合同未写不可抗力、IP归属未写明但无对价失衡证据、竞业3年、保密2-5年、违约金20%但未达5‰/日等。每条含 type（简短中文名）、clause_text、reason。

风险等级：高风险=潜在损失超10万或违反强制性规定；中风险=可能引发争议；低风险=表述不精确。

只输出 JSON 对象（不要任何前缀或后缀）：
{"risks":[{"risk_type":"R01-R12","level":"high/medium/low","clause_text":"原文片段","reason":"理由(含法条)","suggestion":"建议","confidence":0.0-1.0}],"extended_risks":[{"type":"简短中文名","clause_text":"原文片段","reason":"理由"}]}
未发现则对应数组为 []。"""


def _dedup(risks: list[dict]) -> list[dict]:
    """按 (风险类型, 原文片段前缀) 去重，保留首次出现。"""
    seen = {}
    order = []
    for r in risks:
        if not isinstance(r, dict):
            continue
        key = (r.get("risk_type", ""), (r.get("clause_text") or "")[:30])
        if key not in seen:
            seen[key] = r
            order.append(key)
    return [seen[k] for k in order]


def _audit_chunk(chunk_text: str, system_prompt: str) -> list[dict]:
    """对单个文本块调用 LLM 审核，返回规范化后的风险列表。"""
    try:
        response = llm_client.chat(
            prompt=f"{system_prompt}\n\n请审核以下合同：\n{chunk_text}",
            temperature=0.0,
        )
        risks = extract_json_list(response)
        if risks is None:
            return []
        validated = []
        for r in risks:
            if not isinstance(r, dict):
                continue
            validated.append({
                "risk_type": r.get("risk_type", ""),
                "level": r.get("level", "medium"),
                "clause_text": r.get("clause_text", ""),
                "reason": r.get("reason", ""),
                "suggestion": r.get("suggestion", ""),
                # LLM 自报置信度优先；缺失时给中性 0.6（诚实标注不确定性），不再硬编码 0.7
                "confidence": clamp_confidence(r.get("confidence", LLM_FALLBACK_CONFIDENCE)),
                "detection_method": "llm",
            })
        return validated
    except Exception as e:
        logger.error(f"LLM 审核失败: {e}")
        return []


def audit_with_llm(full_text: str, rag_context: list = None) -> list[dict]:
    rag_text = ""
    if rag_context:
        rag_items = []
        for item in rag_context[:5]:
            content = item.get("content", "") if isinstance(item, dict) else str(item)
            rag_items.append(f"- {content[:200]}")
        rag_text = "\n".join(rag_items)

    system_prompt = SYSTEM_PROMPT_AUDIT
    if rag_text:
        system_prompt = SYSTEM_PROMPT_AUDIT.replace(
            "请以 JSON 数组格式输出",
            f"参考法条和案例（来自知识库）：\n{rag_text}\n\n请以 JSON 数组格式输出"
        )

    # 分块：长合同逐块审核，尾部不再截断
    chunks = split_chunks(full_text, MAX_AUDIT_CHARS)
    if len(chunks) > 1:
        logger.info("合同 %d 字超过单块上限，分为 %d 块逐块 LLM 审核", len(full_text), len(chunks))

    all_risks = []
    for chunk in chunks:
        all_risks.extend(_audit_chunk(chunk, system_prompt))

    result = _dedup(all_risks)
    logger.info(f"LLM 审核完成，检出 {len(result)} 条风险（{len(chunks)} 块）")
    return result
