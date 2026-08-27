"""
ai.classifier — 合同类型分类

与要素抽取器一致：长合同用 ai.chunker.split_chunks 分块，再从全文均匀采样若干块
（覆盖首部/中部/尾部），对每块独立分类后投票聚合，避免只读开头 2000 字导致的：
 1. 尾部关键条款（知识产权归属、竞业限制、争议解决等）漏看，判错类型；
 2. 过度依赖标题/首行，退化成"读标题"式分类。

提示词显式要求"以正文条款实质内容为准，不看标题/文件名"，降低标题泄漏。
"""
import logging
from collections import Counter

from ai.chunker import split_chunks
from ai.llm_client import llm_client
from ai.utils import extract_json_dict

logger = logging.getLogger(__name__)

CONTRACT_TYPES = ["采购合同", "销售合同", "保密协议", "服务外包合同", "劳动合同"]

# 单块分类文本上限（字符）。与要素抽取一致，保证单块不超 LLM 上下文预算。
MAX_CLASSIFY_CHARS = 4000
# 长合同最多采样多少块参与投票：首块 + 尾块必含，中间均匀补足，兼顾覆盖与耗时。
MAX_CLASSIFY_CHUNKS = 5

SYSTEM_PROMPT_CLASSIFY = f"""你是一个合同分类专家。请阅读以下合同文本，判断它属于以下哪种类型：{', '.join(CONTRACT_TYPES)}。

判断标准（按正文条款的实质内容判断，不要仅凭标题、文件名或首行文字）：
- 采购合同：以买方向卖方采购货物/服务为核心，含交付验收、付款条件、质保/售后条款
- 销售合同：以卖方向买方销售产品为核心，含定价、市场推广、渠道管理、代理/经销条款
- 保密协议(NDA)：以保密义务为核心，约定保密范围、保密期限、违约责任，不涉及交易标的本身
- 服务外包合同：以委托开发/运维/外包服务为核心，含 SLA 服务等级、知识产权归属、源代码托管
- 劳动合同：以建立劳动关系为核心，含岗位职责、薪酬福利、竞业限制、社保、离职条款

若合同同时具备多类特征，按「合同的核心标的」判断；核心是买卖货物就是采购/销售，核心是保密义务就是保密协议。

请只输出 JSON（不要加任何前缀或后缀）：
{{"contract_type": "合同类型", "confidence": 0.0-1.0, "reason": "一句话判断依据"}}"""


def _classify_one_chunk(text: str) -> dict | None:
    """对单个文本块做分类，返回 {contract_type, confidence, reason}，失败返回 None。"""
    prompt = f"{SYSTEM_PROMPT_CLASSIFY}\n\n请判断以下合同片段的类型：\n{text}"
    try:
        response = llm_client.chat(prompt=prompt, temperature=0.1)
        result = extract_json_dict(response)
        if result and result.get("contract_type") in CONTRACT_TYPES:
            return {
                "contract_type": result["contract_type"],
                "confidence": float(result.get("confidence", 0.5)),
                "reason": result.get("reason", ""),
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
    对完整合同文本做类型分类。

    Args:
        full_text: 完整合同文本

    Returns:
        dict: {contract_type, confidence, method, reason, fallback}
              method 为 "llm"（投票成功）或 "fallback"（全部块失败降级）。
    """
    chunks = split_chunks(full_text, MAX_CLASSIFY_CHARS)
    if not chunks:
        return {
            "contract_type": "其他合同",
            "confidence": 0.0,
            "method": "fallback",
            "reason": "合同文本为空",
            "fallback": True,
        }
    if len(chunks) > 1:
        logger.info("合同分类：全文 %d 字，分为 %d 块，采样 %d 块投票",
                    len(full_text), len(chunks), min(len(chunks), MAX_CLASSIFY_CHUNKS))

    sampled = _sample_chunks(chunks)

    votes = Counter()          # 各类型得票数
    type_conf: dict[str, float] = {}   # 各类型最高置信度
    type_reason: dict[str, str] = {}   # 各类型最近一次判断依据

    for chunk in sampled:
        r = _classify_one_chunk(chunk)
        if not r:
            continue
        ct = r["contract_type"]
        votes[ct] += 1
        type_conf[ct] = max(type_conf.get(ct, 0.0), r["confidence"])
        type_reason[ct] = r["reason"]

    if votes:
        best_type, _best_count = votes.most_common(1)[0]
        return {
            "contract_type": best_type,
            "confidence": round(type_conf.get(best_type, 0.5), 4),
            "method": "llm",
            "reason": type_reason.get(best_type, ""),
            "fallback": False,
        }

    return {
        "contract_type": "其他合同",
        "confidence": 0.0,
        "method": "fallback",
        "reason": "所有分块分类失败",
        "fallback": True,
    }
