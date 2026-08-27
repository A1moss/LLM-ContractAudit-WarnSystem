from ai.llm_client import llm_client
from ai.utils import extract_json_dict
import logging

logger = logging.getLogger(__name__)

CONTRACT_TYPES = ["采购合同", "销售合同", "保密协议", "服务外包合同", "劳动合同"]

SYSTEM_PROMPT_CLASSIFY = f"""你是一个合同分类专家。请阅读以下合同文本，判断它属于以下哪种类型：{', '.join(CONTRACT_TYPES)}。

判断标准：
- 采购合同：涉及货物/服务采购、交付验收、付款条件、质保条款
- 销售合同：涉及产品销售、定价、市场推广、渠道管理、代理条款
- 保密协议(NDA)：以保密义务为核心，约定保密范围、期限、违约责任
- 服务外包合同：涉及技术开发/运维外包、SLA服务等级、知识产权归属、源代码托管
- 劳动合同：涉及雇佣关系、岗位职责、薪酬福利、竞业限制、社保条款

请只输出 JSON（不要加任何前缀或后缀）：
{{"contract_type": "合同类型", "confidence": 0.0-1.0}}"""


def classify_contract(full_text: str) -> dict:
    truncated = full_text[:2000]

    try:
        response = llm_client.chat(
            prompt=f"{SYSTEM_PROMPT_CLASSIFY}\n\n请判断以下合同的类型：\n{truncated}",
            temperature=0.1,
        )
        result = extract_json_dict(response)
        if result and "contract_type" in result:
            return {
                "contract_type": result.get("contract_type", "其他合同"),
                "confidence": float(result.get("confidence", 0.5)),
                "method": "llm",
                "reason": result.get("reason", ""),
                "fallback": False,
            }
    except Exception as e:
        logger.warning(f"LLM 分类失败，降级为默认类型: {e}")

    return {
        "contract_type": "其他合同",
        "confidence": 0.0,
        "method": "fallback",
        "reason": "",
        "fallback": True,
    }
