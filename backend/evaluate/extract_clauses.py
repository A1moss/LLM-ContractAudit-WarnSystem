"""
extract_clauses.py — 从合同范本提取标准条款，补充 standard_clauses.json

对每个指定的合同类型，采样该类型下若干份范本正文，用 DeepSeek 提取「标准条款清单」
（title/content/priority），按 title 去重后合并进 standard_clauses.json（type 用 11 类口径）。

用法：
    python evaluate/extract_clauses.py --types 技术合同,保密协议,劳动合同      # 提取并写入
    python evaluate/extract_clauses.py --types 技术合同 --dry-run             # 只看不写
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("extract_clauses")

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.llm_client import llm_client              # noqa: E402
from ai.utils import extract_json                 # noqa: E402
from evaluate.build_testset import extract_text, _archive_dir  # noqa: E402

STANDARD_JSON = _BACKEND_DIR / "ai" / "knowledge" / "standard_clauses.json"

# 每类最多采样几份范本 / 单份截断字符（控制 LLM 输入长度）
MAX_SAMPLE_FILES = 3
MAX_SAMPLE_CHARS = 2500

# 类型 → id 前缀（新类型在此登记即可）
_PREFIX = {
    "技术合同": "TEC",
    "保密协议": "NDA",
    "劳动合同": "EMP",
    "承揽合同": "CON",
    "建设工程合同": "CST",
    "委托合同": "AGT",
    "中介合同": "BRK",
    "租赁合同": "LSE",
    "物业服务合同": "PM",
}

SYSTEM_PROMPT = """你是合同条款专家。请从下面给出的「{type}」合同范本中，提取该类型合同的「标准条款清单」——即一份规范、完整的{type}应当包含哪些条款。

对每条条款输出：
- title：条款名（简短，如「验收标准」「保密义务」）
- content：该条款应约定的标准内容要点（一句话概括关键约定要素）
- priority：required（核心必备）或 recommended（建议约定）

要求：
1. 合并不同范本中重复的条款，只保留唯一的一套标准条款；
2. 不要照抄范本原文，概括成「应当约定什么」；
3. 只输出 JSON 数组，不要任何其他文字或解释。

输出格式：
[{{"title":"...","content":"...","priority":"required"}}, ...]"""


def _load() -> list:
    with open(STANDARD_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _save(clauses: list):
    STANDARD_JSON.write_text(json.dumps(clauses, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_id(clauses: list, prefix: str) -> str:
    """取该前缀下已有最大序号 + 1。"""
    mx = 0
    for c in clauses:
        cid = c.get("id", "")
        if cid.startswith(prefix + "-"):
            try:
                mx = max(mx, int(cid.split("-")[1]))
            except (ValueError, IndexError):
                pass
    return f"{prefix}-{mx + 1:03d}"


def extract_for_type(contract_type: str) -> list[dict]:
    """从某类型范本提取标准条款（LLM）。"""
    d = _archive_dir(contract_type)
    if not d.exists():
        logger.warning("类型 %s 无归档目录，跳过", contract_type)
        return []

    files = [f for f in d.rglob("*") if f.suffix.lower() in (".docx", ".doc", ".pdf", ".wps")]
    samples = []
    for f in sorted(files)[:MAX_SAMPLE_FILES]:
        text = extract_text(f)
        if text:
            samples.append(text[:MAX_SAMPLE_CHARS])
    if not samples:
        logger.warning("类型 %s 无可用范本正文，跳过", contract_type)
        return []

    prompt = SYSTEM_PROMPT.format(type=contract_type) + "\n\n" + "\n\n---\n\n".join(samples)
    logger.info("调用 LLM 提取「%s」标准条款（采样 %d 份）…", contract_type, len(samples))
    try:
        response = llm_client.chat(prompt=prompt, temperature=0.1)
    except Exception as e:
        logger.error("LLM 调用失败: %s", e)
        return []

    data = extract_json(response)
    if isinstance(data, dict):
        data = data.get("clauses") or data.get("standard_clauses") or data.get("risks") or []
    if not isinstance(data, list):
        logger.warning("类型 %s LLM 返回解析失败", contract_type)
        return []

    out = []
    for c in data:
        if isinstance(c, dict) and c.get("title"):
            out.append({
                "title": str(c.get("title")).strip(),
                "content": str(c.get("content", "")).strip(),
                "priority": c.get("priority", "recommended") if c.get("priority") in ("required", "recommended") else "recommended",
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="技术合同,保密协议,劳动合同",
                    help="逗号分隔的合同类型，默认技术合同/保密协议/劳动合同")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    args = ap.parse_args()

    existing = _load()
    existing_keys = {(c.get("type"), c.get("title")) for c in existing}

    added_total = 0
    for ct in [t.strip() for t in args.types.split(",") if t.strip()]:
        prefix = _PREFIX.get(ct)
        if not prefix:
            logger.warning("类型 %s 未登记 id 前缀，请在 _PREFIX 添加", ct)
            continue

        new_clauses = extract_for_type(ct)
        added = 0
        for c in new_clauses:
            key = (ct, c["title"])
            if key in existing_keys:
                continue  # 已存在，去重
            existing_keys.add(key)
            c["id"] = _next_id(existing, prefix)
            c["type"] = ct
            c["level"] = 0
            c["depends_on"] = []
            c["conflict_with"] = []
            c["related_law"] = ""
            c["source"] = f"自动提取·{ct}"
            existing.append(c)
            added += 1
            logger.info("  + [%s] %s", c["id"], c["title"])
        added_total += added
        logger.info("类型 %s 新增 %d 条", ct, added)

    if args.dry_run:
        logger.info("（dry-run）共新增 %d 条，未写入", added_total)
    else:
        _save(existing)
        logger.info("已写入 %s，总条数 %d（本次新增 %d）", STANDARD_JSON.name, len(existing), added_total)


if __name__ == "__main__":
    main()
