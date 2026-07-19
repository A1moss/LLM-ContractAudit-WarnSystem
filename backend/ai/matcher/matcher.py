"""
ai.matcher.matcher — 标准条款语义比对与缺失检测

双层对齐策略：
1. 关键词归一化：将合同段落与模板条款做标题关键词对齐
2. 向量语义匹配：对不确定的匹配用 text2vec 做精细判断

输出：每条模板条款的覆盖状态 (covered / partial / missing)
"""
import json
import os
import re
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── 关键词归一化同义词组 ──
SYNONYM_GROUPS = [
    {"违约责任", "违约条款", "违约处理", "违约", "违约责任条款"},
    {"付款方式", "付款条件", "支付方式", "支付条款", "价款支付", "价款与支付", "费用与支付方式"},
    {"知识产权", "知识产权归属", "知识产权条款", "IP归属", "软件著作权"},
    {"保密义务", "保密条款", "保密", "保密协议", "保密信息", "商业秘密"},
    {"争议解决", "争议解决方式", "争议处理", "争议", "管辖", "仲裁"},
    {"不可抗力", "免责条款", "免责", "不可抗力条款"},
    {"合同期限", "合同有效期", "履行期限", "交付期限", "开发周期", "租赁期限"},
    {"验收标准", "验收", "验收条件", "交付与验收", "运输交付与验收"},
    {"合同变更", "合同解除", "变更与解除", "合同的变更与解除", "合同终止"},
    {"竞业限制", "竞业", "竞业禁止", "不竞争"},
    {"数据保护", "隐私保护", "数据安全", "个人信息", "数据隐私"},
    {"合同主体", "双方信息", "甲方乙方", "签署方", "合同主体与签署方"},
    {"合同标的", "标的", "标的物", "采购内容", "合作内容", "服务内容"},
    {"签署与生效", "合同生效", "签署", "签章", "生效条件"},
    {"通知与送达", "通知送达", "送达地址", "联系方式"},
    {"质量保证", "质量标准", "质量保证与标准", "质保"},
    {"押金条款", "押金", "保证金", "履约保证金"},
    {"禁止条款", "转租限制", "转租", "转让限制"},
    {"租赁期限", "租赁期", "租期"},
]


def _load_templates(contract_type: str) -> list[dict]:
    """Load standard clause templates for a given contract type."""
    knowledge_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")
    path = os.path.join(knowledge_dir, "standard_clauses.json")
    if not os.path.exists(path):
        logger.warning("standard_clauses.json not found at %s", path)
        return []
    with open(path, "r", encoding="utf-8") as f:
        all_clauses = json.load(f)
    return [c for c in all_clauses if c.get("type") == contract_type]


def _normalize(title: str) -> str:
    """Normalize a clause title by mapping it to a canonical synonym group."""
    title = title.strip()
    # Direct match with synonym groups
    for group in SYNONYM_GROUPS:
        if title in group:
            return min(group, key=len)  # shortest = canonical
    # Fuzzy: check if title contains any synonym keyword
    title_lower = title.lower()
    for group in SYNONYM_GROUPS:
        for kw in group:
            if len(kw) >= 2 and kw in title_lower:
                return min(group, key=len)
    return title


def _split_contract_paragraphs(text: str) -> list[dict]:
    """Split contract text into paragraphs with positional info."""
    paragraphs = []
    lines = text.split("\n")
    heading_pat = re.compile(
        r"(第[一二三四五六七八九十百千\d]+[章节条]|"
        r"[一二三四五六七八九十]+[、．.]|"
        r"\(\d+\)|（\d+）)"
    )
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        m = heading_pat.match(line)
        paragraphs.append({
            "index": i,
            "text": line,
            "is_heading": bool(m),
            "heading": m.group(0) if m else None,
        })
    return paragraphs


def _keyword_match(template: dict, paragraphs: list[dict]) -> tuple[Optional[dict], float]:
    """
    Keyword-level match: find the best matching paragraph for a template clause.
    Returns (best_paragraph, confidence).
    confidence: 1.0 = exact keyword match, 0.0 = no match
    """
    norm_title = _normalize(template["title"])
    # Search for the normalized title in paragraph text
    best = None
    best_score = 0.0
    for para in paragraphs:
        text = para["text"]
        score = 0.0
        # Method 1: exact title match
        if template["title"] in text:
            score = max(score, 0.7)
        # Method 2: normalized keyword in paragraph
        if norm_title in text or norm_title in _normalize(text[:30]):
            score = max(score, 0.6)
        # Method 3: heading match
        if para.get("heading") and norm_title in _normalize(para.get("heading", "")):
            score = max(score, 0.5)
        # Method 4: content keywords overlap
        content_words = set(template.get("content", ""))
        text_words = set(text)
        if content_words and text_words:
            overlap = len(content_words & text_words) / max(len(content_words), 1)
            score = max(score, overlap * 0.3)
        if score > best_score:
            best_score = score
            best = para
    if best_score >= 0.3:
        return best, best_score
    return None, 0.0


def _get_embedder():
    """Lazy-load sentence transformer embedder (reuse from rag module if available)."""
    try:
        from ai.rag.vector_store import _get_embedder as rag_embedder
        return rag_embedder()
    except Exception:
        pass
    # Fallback: load directly
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer("shibing624/text2vec-base-chinese")
    except Exception:
        logger.warning("sentence-transformers not available, semantic matching disabled")
        return None


def _cosine_similarity(a, b) -> float:
    """Cosine similarity between two vectors."""
    a, b = np.asarray(a), np.asarray(b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def compare_clauses(contract_text: str, contract_type: str) -> dict:
    """
    Compare contract text against standard clause templates.

    Args:
        contract_text: Full text of the uploaded contract
        contract_type: Contract type classification result (e.g., "采购合同")

    Returns:
        {
            "clauses": [{template_title, priority, status, matched_text, similarity, deviation, completion, risk}],
            "summary": {total, covered, partial, missing, coverage_rate},
            "missing_critical": ["强制必选但缺失的条款标题"]
        }
    """
    templates = _load_templates(contract_type)
    if not templates:
        logger.info("No templates found for contract type '%s', trying fallback", contract_type)
        # Fallback: load all templates as generic reference
        templates = _load_templates("采购合同")  # generic fallback

    paragraphs = _split_contract_paragraphs(contract_text)
    embedder = _get_embedder()
    clauses = []
    total = len(templates)
    covered = partial = missing_count = 0
    missing_critical = []

    for tmpl in templates:
        title = tmpl["title"]
        priority = tmpl.get("priority", "recommended")
        content = tmpl.get("content", "")
        law = tmpl.get("related_law", "")

        # Step 1: keyword match
        best_para, kw_score = _keyword_match(tmpl, paragraphs)

        similarity = kw_score
        matched_text = None
        deviation = None
        status = "missing"

        if best_para:
            matched_text = best_para["text"][:300]
            # Step 2: semantic match (if embedder available and keyword not decisive)
            if embedder and 0.3 <= kw_score <= 0.7:
                try:
                    tmpl_vec = embedder.encode(content, normalize_embeddings=True)
                    para_vec = embedder.encode(best_para["text"], normalize_embeddings=True)
                    sem_score = _cosine_similarity(tmpl_vec, para_vec)
                    similarity = max(kw_score, sem_score * 0.8)
                except Exception as e:
                    logger.debug("Semantic matching failed: %s", e)

        # Step 3: classify status
        if similarity > 0.75:
            status = "covered"
            covered += 1
        elif similarity >= 0.5:
            status = "partial"
            partial += 1
            deviation = f"条款'{title}'与标准模板存在差异（相似度 {similarity:.0%}）"
        else:
            status = "missing"
            missing_count += 1
            if priority == "required":
                missing_critical.append(title)

        # Step 4: generate completion suggestion for missing/partial
        completion = None
        risk = None
        if status == "missing":
            completion = f"建议补充'{title}'条款。参考模板：{content[:200]}"
            risk = f"缺失{priority}级条款'{title}'，可能影响合同完整性" if priority == "required" else None
        elif status == "partial":
            completion = f"建议完善'{title}'条款内容以完全覆盖标准模板要求"

        clauses.append({
            "template_title": title,
            "priority": priority,
            "status": status,
            "matched_text": matched_text,
            "similarity": round(similarity, 4),
            "deviation": deviation,
            "completion": completion,
            "risk": risk or None,
        })

    coverage_rate = covered / total if total > 0 else 0.0

    return {
        "clauses": clauses,
        "summary": {
            "total": total,
            "covered": covered,
            "partial": partial,
            "missing": missing_count,
            "coverage_rate": round(coverage_rate, 4),
        },
        "missing_critical": missing_critical,
    }
