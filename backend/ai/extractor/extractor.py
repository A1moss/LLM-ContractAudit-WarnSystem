from ai.chunker import split_chunks
from ai.llm_client import llm_client
import json
import logging

logger = logging.getLogger(__name__)

# 单块要素抽取文本上限（字符）。超长合同分块抽取后合并，尾部要素不再丢失。
MAX_EXTRACT_CHARS = 4000

SYSTEM_PROMPT_EXTRACT = """你是一个法律信息抽取专家。请从合同文本中抽取以下关键结构化信息。

只输出 JSON（不要加任何前缀或后缀）：
{
  "parties": {"甲方": "公司全称", "乙方": "公司全称"},
  "amount": {"value": 数字, "currency": "CNY/USD/EUR", "text": "大写金额原文"},
  "sign_date": "YYYY-MM-DD",
  "performance_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "dispute_resolution": "争议解决方式",
  "governing_law": "适用法律"
}

规则：
1. 双方信息从合同首部提取，识别公司全称和角色（甲方/乙方）
2. 金额取合同总金额，数字类型，注意区分含税/不含税
3. 日期统一 YYYY-MM-DD，如"自签署之日"需结合签署日期推算
4. 争议解决提取仲裁机构或管辖法院全称
5. 未出现的字段填 null"""

FEWSHOT_EXAMPLE = """
合同片段：
"杭州科技有限公司（以下简称甲方）与上海软件有限公司（以下简称乙方）经友好协商，就甲方向乙方采购企业管理系统软件事宜达成如下协议。合同总金额为人民币伍拾万元整（¥500,000）。本合同自2024年3月15日起生效，履行期限至2025年3月15日。因本合同引起的争议，提交北京仲裁委员会仲裁。"

正确输出：
{
  "parties": {"甲方": "杭州科技有限公司", "乙方": "上海软件有限公司"},
  "amount": {"value": 500000, "currency": "CNY", "text": "人民币伍拾万元整"},
  "sign_date": "2024-03-15",
  "performance_period": {"start": "2024-03-15", "end": "2025-03-15"},
  "dispute_resolution": "北京仲裁委员会",
  "governing_law": null
}"""


def _extract_json(response: str) -> dict:
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass
    for marker in ["```json", "```"]:
        if marker in response:
            try:
                inner = response.split(marker)[1].split("```")[0]
                return json.loads(inner.strip())
            except (IndexError, json.JSONDecodeError):
                continue
    try:
        start = response.index("{")
        end = response.rindex("}") + 1
        return json.loads(response[start:end])
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def _merge_elements(results: list[dict]) -> dict:
    """合并多块抽取结果：字段非 null 优先，parties 内层甲方/乙方逐角色非 null 优先。"""
    keys = ["parties", "amount", "sign_date", "performance_period", "dispute_resolution", "governing_law"]
    merged = {k: None for k in keys}
    for r in results:
        if not isinstance(r, dict):
            continue
        for key in keys:
            val = r.get(key)
            if merged[key] is None and val is not None:
                merged[key] = val
            elif key == "parties" and isinstance(val, dict) and isinstance(merged[key], dict):
                for role in ("甲方", "乙方"):
                    if merged[key].get(role) is None and val.get(role) is not None:
                        merged[key][role] = val[role]
    return merged


def extract_elements(full_text: str, contract_type: str) -> dict:
    chunks = split_chunks(full_text, MAX_EXTRACT_CHARS)
    if len(chunks) > 1:
        logger.info("要素抽取：合同 %d 字超过单块上限，分为 %d 块", len(full_text), len(chunks))

    results = []
    for chunk in chunks:
        prompt = (
            f"{SYSTEM_PROMPT_EXTRACT}\n\n"
            f"这是一个{contract_type}。\n\n"
            f"{FEWSHOT_EXAMPLE}\n\n"
            f"现在请从以下合同中抽取要素：\n{chunk}"
        )
        try:
            response = llm_client.chat(prompt=prompt, temperature=0.1)
            result = _extract_json(response)
            if result:
                results.append({
                    "parties": result.get("parties"),
                    "amount": result.get("amount"),
                    "sign_date": result.get("sign_date"),
                    "performance_period": result.get("performance_period"),
                    "dispute_resolution": result.get("dispute_resolution"),
                    "governing_law": result.get("governing_law"),
                })
        except Exception as e:
            logger.warning(f"LLM 要素抽取失败: {e}")

    if results:
        merged = _merge_elements(results)
        return {**merged, "fallback": False}

    return {
        "parties": {"甲方": "未知", "乙方": "未知"},
        "amount": None,
        "sign_date": None,
        "performance_period": None,
        "dispute_resolution": None,
        "governing_law": None,
        "fallback": True,
    }
