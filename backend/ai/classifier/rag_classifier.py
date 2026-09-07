"""
ai.classifier.rag_classifier — RAG 少样本分类

检索 top-K 相似合同范本作为「少样本示例」，连同其法理类型喂给 LLM，
让 LLM 参照真实范本做最终法律判断。结合 kNN（找到参照物）与 LLM（法律推理）长处，
用于补纯 LLM 零样本 / 纯 kNN 各自的短板（尤其服务类合同的细粒度区分）。
"""
import logging

from ai.llm_client import llm_client
from ai.taxonomy import ENABLED_TYPES
from ai.utils import extract_json_dict

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 12000   # 查询全文上限（示例+查询要在 LLM 上下文内）
EXAMPLE_HEAD = 600        # 每个示例的首部字符数
EXAMPLE_TAIL = 300        # 每个示例的尾部字符数（含违约/争议/成果归属）

SYSTEM_PROMPT_RAG = f"""你是一个合同分类专家。先阅读下面给出的几个「已分类合同范本」作为参考示例，再判断待分类合同的类型。

法理分类（{len(ENABLED_TYPES)} 类，选择其一）：{', '.join(ENABLED_TYPES)}

判断标准（按正文条款的实质内容判断，不要仅凭标题或首行文字；示例只是参照，最终以正文实质内容为准）：
- 买卖合同：货物买卖/购销/采购/销售，含交付、价款、质量、验收、售后（采购方、销售方、中性买卖都归此类，不区分买卖方向）
- 租赁合同：租赁物使用，含租期、租金、押金、维修、返还
- 承揽合同：定作加工/定制开发，含定作要求、材料、交付验收、报酬
- 建设工程合同：工程总承包/施工/勘察设计/监理，含工期、价款、竣工验收、质量保修
- 技术合同：技术开发/转让/许可/服务/咨询，含技术成果归属、验收、后续改进
- 委托合同：委托代理/委托事务，含委托权限、费用、报告义务
- 物业服务合同：物业管理服务（清洁/绿化/安保/维修/秩序维护），含物业服务范围、物业费、服务标准
- 中介合同：居间/中介服务，含居间报酬、促成交易
- 保密协议：以保密义务为核心，约定保密范围、期限、违约责任，不涉及交易标的本身
- 无名合同：不属于上述任何有名合同的其它合同（培训、养老、电商平台、医疗美容、能源托管等）
- 劳动合同：以建立劳动关系为核心，含岗位、薪酬、社保、竞业限制、离职；劳务派遣合同（三方：派遣单位+用工单位+劳动者）也归此类

若同时具备多类特征，按「合同的核心标的」判断：
- 标的为现成货物→买卖合同；标的为定制加工物/定作物→承揽合同；
- 委托开发软件/技术成果→技术合同；
- 委托他人办理事务→委托合同；只为促成交易收居间费→中介合同；
- 培训/养老/电商/医疗美容等其它服务→无名合同。

is_outsourcing 单独判断（业务标签，不影响法理分类）：一方把软件开发/运维/业务流程整体外包给另一方完成（两方：发包方+承包方，承包方自行管理员工）则 true；劳务派遣（三方：派遣单位+用工单位+劳动者）不是服务外包，false。

只输出 JSON（不要加任何前缀或后缀）：
{{"contract_type": "合同类型", "is_outsourcing": true/false, "confidence": 0.0-1.0, "reason": "一句话判断依据"}}"""


def classify_by_rag(full_text: str, top_k: int = 3, exclude_self: str = "") -> dict:
    """
    RAG 少样本分类。

    Args:
        full_text: 查询合同全文
        top_k: 检索的示例范本数量
        exclude_self: 评测时传原文本，剔除自身（防泄漏）

    Returns:
        dict: {contract_type, is_outsourcing, confidence, method, reason, top_matches, fallback}
    """
    if not full_text or not full_text.strip():
        return {"contract_type": "其他合同", "is_outsourcing": False, "confidence": 0.0,
                "method": "rag", "reason": "合同文本为空", "top_matches": [], "fallback": True}

    # 懒加载 RAG 向量库（避免启动链拖入 chromadb/sentence_transformers）
    from ai.rag.vector_store import search_similar_templates, _excerpt
    matches = search_similar_templates(full_text[:MAX_QUERY_CHARS], top_k + 1)
    if exclude_self:
        matches = [m for m in matches if m.get("text") != exclude_self]
    matches = matches[:top_k]

    if not matches:
        # 检索无结果：降级为纯 LLM 零样本
        from ai.classifier.classifier import classify_contract
        r = classify_contract(full_text)
        r["method"] = "rag-fallback-llm"
        r["top_matches"] = []
        return r

    examples = []
    for i, m in enumerate(matches):
        ex = _excerpt(m.get("text", ""), head=EXAMPLE_HEAD, tail=EXAMPLE_TAIL)
        examples.append(f"[示例{i + 1}] 类型：{m['type']}\n{ex}")
    example_block = "\n\n".join(examples)

    prompt = (
        f"{SYSTEM_PROMPT_RAG}\n\n"
        f"参考示例：\n{example_block}\n\n"
        f"待分类合同：\n{full_text[:MAX_QUERY_CHARS]}"
    )

    try:
        response = llm_client.chat(prompt=prompt, temperature=0.1)
        result = extract_json_dict(response)
        if result and result.get("contract_type") in ENABLED_TYPES:
            return {
                "contract_type": result["contract_type"],
                "is_outsourcing": bool(result.get("is_outsourcing", False)),
                "confidence": float(result.get("confidence", 0.5)),
                "method": "rag",
                "reason": result.get("reason", ""),
                "top_matches": [{"type": m["type"], "score": m["score"]} for m in matches],
                "fallback": False,
            }
    except Exception as e:
        logger.warning("RAG 分类失败: %s", e)

    return {
        "contract_type": "其他合同",
        "is_outsourcing": False,
        "confidence": 0.0,
        "method": "rag",
        "reason": "分类失败",
        "top_matches": [],
        "fallback": True,
    }
