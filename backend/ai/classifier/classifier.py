"""
ai.classifier — 合同类型分类（法理维度）+ 业务标签（服务外包）

分块采样 + 投票聚合（同要素抽取器），避免只读开头 2000 字导致的漏判/读标题。

输出二维：
  contract_type   法理分类（10 类，单一互斥）：合同在法律上是什么；
  is_outsourcing  业务标签（boolean，可叠加）：是否属「服务外包」业务。
一个合同可以法理上是承揽/技术/委托，业务上同时是服务外包——两者不冲突。
"""
import logging
from collections import Counter

from ai.chunker import split_chunks
from ai.llm_client import llm_client
from ai.taxonomy import ENABLED_TYPES
from ai.utils import extract_json_dict

logger = logging.getLogger(__name__)

CONTRACT_TYPES = ENABLED_TYPES  # 10 类法理分类：由 ai.taxonomy 统一维护

# 单块分类文本上限（字符）。与要素抽取一致，保证单块不超 LLM 上下文预算。
MAX_CLASSIFY_CHARS = 4000
# 长合同最多采样多少块参与投票：首块 + 尾块必含，中间均匀补足，兼顾覆盖与耗时。
MAX_CLASSIFY_CHUNKS = 5

SYSTEM_PROMPT_CLASSIFY = f"""你是一个合同分类专家。请阅读以下合同文本，完成两个独立判断。

【判断一：法理分类】判断它属于以下哪种类型：{', '.join(CONTRACT_TYPES)}。

判断标准（按正文条款的实质内容判断，不要仅凭标题、文件名或首行文字）：
- 买卖合同：货物买卖/购销/采购/销售，含交付、价款、质量、验收、售后（采购方、销售方、中性买卖都归此类，不区分买卖方向）
- 租赁合同：租赁物使用，含租期、租金、押金、维修、返还
- 承揽合同：定作加工/定制开发，含定作要求、材料、交付验收、报酬
- 建设工程合同：工程总承包/施工/勘察设计/监理，含工期、价款、竣工验收、质量保修
- 技术合同：技术开发/转让/许可/服务/咨询，含技术成果归属、验收、后续改进
- 委托合同：委托代理/委托事务，含委托权限、费用、报告义务
- 中介合同：居间/中介服务，含居间报酬、促成交易
- 保密协议：以保密义务为核心，约定保密范围、期限、违约责任，不涉及交易标的本身
- 无名合同：不属于上述任何有名合同的其它合同（培训、养老、电商平台、医疗美容、能源托管等）
- 劳动合同：以建立劳动关系为核心，含岗位、薪酬、社保、竞业限制、离职；劳务派遣合同（三方：派遣单位+用工单位+劳动者）也归此类

若同时具备多类特征，按「合同的核心标的」判断：
- 标的为现成货物→买卖合同；标的为定制加工物/定作物→承揽合同；
- 委托开发软件/技术成果→技术合同；
- 委托他人办理事务→委托合同；只为促成交易收居间费→中介合同；
- 培训/养老/电商/医疗美容等其它服务→无名合同。

【判断二：业务标签】is_outsourcing：该合同是否属「服务外包」业务——即一方把软件开发/运维/业务流程等整体外包给另一方完成（两方：发包方+承包方，承包方自行组织管理员工）。是则 true，否则 false。

注意：
1. 服务外包不是法理分类，判断二独立于判断一，一个合同可以法理上是承揽/技术/委托，同时 is_outsourcing=true；
2. 劳务派遣（三方：派遣单位+用工单位+劳动者，派遣单位缴社保、用工单位直接指挥劳动者）不是服务外包，is_outsourcing=false。

请只输出 JSON（不要加任何前缀或后缀）：
{{"contract_type": "合同类型", "is_outsourcing": true/false, "confidence": 0.0-1.0, "reason": "一句话判断依据"}}"""


def _classify_one_chunk(text: str) -> dict | None:
    """对单个文本块做分类，返回 {contract_type, is_outsourcing, confidence, reason}，失败返回 None。"""
    prompt = f"{SYSTEM_PROMPT_CLASSIFY}\n\n请判断以下合同片段的类型：\n{text}"
    try:
        response = llm_client.chat(prompt=prompt, temperature=0.1)
        result = extract_json_dict(response)
        if result and result.get("contract_type") in CONTRACT_TYPES:
            return {
                "contract_type": result["contract_type"],
                "confidence": float(result.get("confidence", 0.5)),
                "reason": result.get("reason", ""),
                "is_outsourcing": bool(result.get("is_outsourcing", False)),
            }
    except Exception as e:
        logger.warning("LLM 分类失败（单块）: %s", e)
    return None


def _sample_chunks(chunks: list[str], max_n: int = MAX_CLASSIFY_CHUNKS) -> list[str]:
    """均匀采样若干块：首块、尾块必含，中间按下标等距补齐，覆盖全文。"""
    if len(chunks) <= max_n:
        return chunks
    n = len(chunks)
    # 等距取 max_n 个下标（含 0 与 n-1），去重后按序返回
    idxs = sorted({int(round(i * (n - 1) / (max_n - 1))) for i in range(max_n)})
    return [chunks[i] for i in idxs]


def classify_contract(full_text: str) -> dict:
    """
    对完整合同文本做分类（法理分类 + 服务外包业务标签）。

    Args:
        full_text: 完整合同文本

    Returns:
        dict: {contract_type, is_outsourcing, confidence, method, reason, fallback}
    """
    chunks = split_chunks(full_text, MAX_CLASSIFY_CHARS)
    if not chunks:
        return {
            "contract_type": "其他合同",
            "is_outsourcing": False,
            "confidence": 0.0,
            "method": "fallback",
            "reason": "合同文本为空",
            "fallback": True,
        }
    if len(chunks) > 1:
        logger.info("合同分类：全文 %d 字，分为 %d 块，采样 %d 块投票",
                    len(full_text), len(chunks), min(len(chunks), MAX_CLASSIFY_CHUNKS))

    sampled = _sample_chunks(chunks)

    votes = Counter()                      # 各法理类型得票数
    type_conf: dict[str, float] = {}       # 各类型最高置信度
    type_reason: dict[str, str] = {}       # 各类型最近一次判断依据
    outsourcing_votes = Counter()          # 服务外包业务标签得票数

    for chunk in sampled:
        r = _classify_one_chunk(chunk)
        if not r:
            continue
        ct = r["contract_type"]
        votes[ct] += 1
        type_conf[ct] = max(type_conf.get(ct, 0.0), r["confidence"])
        type_reason[ct] = r["reason"]
        outsourcing_votes[bool(r.get("is_outsourcing", False))] += 1

    if votes:
        best_type, _best_count = votes.most_common(1)[0]
        # 服务外包标签：多数块判定为 true 则打标
        is_out = outsourcing_votes.get(True, 0) > outsourcing_votes.get(False, 0)
        return {
            "contract_type": best_type,
            "is_outsourcing": is_out,
            "confidence": round(type_conf.get(best_type, 0.5), 4),
            "method": "llm",
            "reason": type_reason.get(best_type, ""),
            "fallback": False,
        }

    return {
        "contract_type": "其他合同",
        "is_outsourcing": False,
        "confidence": 0.0,
        "method": "fallback",
        "reason": "所有分块分类失败",
        "fallback": True,
    }
