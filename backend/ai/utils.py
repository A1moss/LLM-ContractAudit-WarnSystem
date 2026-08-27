"""
ai.utils — 通用工具函数

extract_json：从 LLM 返回文本中稳健提取 JSON（dict 或 list）。
此前该逻辑被复制在 classifier / llm_auditor / corex.orchestrator /
reviser / matcher / extractor 六个文件里，现统一收口到此。
"""
import json


def extract_json(text: str):
    """
    从 LLM 返回文本中稳健提取 JSON 对象（dict 或 list）。

    依次尝试三种策略：
    1. 直接 json.loads 整个文本；
    2. 剥离 ```json / ``` 代码块后再解析；
    3. 截取首个 '[' 到最后一个 ']'（或 '{' 到 '}'）之间的内容解析。

    Args:
        text: LLM 原始返回文本

    Returns:
        dict | list | None：解析成功返回对应对象，失败返回 None。
    """
    if not text:
        return None
    t = text.strip()

    # 1) 直接解析
    try:
        return json.loads(t)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2) 代码块包裹
    for marker in ("```json", "```"):
        if marker in t:
            try:
                inner = t.split(marker, 1)[1].split("```", 1)[0]
                return json.loads(inner.strip())
            except (json.JSONDecodeError, ValueError, IndexError):
                continue

    # 3) 首尾括号截取（先数组后对象）
    for open_c, close_c in (("[", "]"), ("{", "}")):
        try:
            s = t.index(open_c)
            e = t.rindex(close_c) + 1
            if e > s:
                return json.loads(t[s:e])
        except (ValueError, json.JSONDecodeError):
            continue

    return None


def extract_json_list(text: str) -> list | None:
    """提取 JSON 数组；若返回的是带 "risks" 键的 dict，则取 risks 列表。"""
    result = extract_json(text)
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and isinstance(result.get("risks"), list):
        return result["risks"]
    return None


def extract_json_dict(text: str) -> dict | None:
    """提取 JSON 对象；仅当结果为 dict 时返回。"""
    result = extract_json(text)
    return result if isinstance(result, dict) else None
