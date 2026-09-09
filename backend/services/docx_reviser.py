"""docx_reviser — 修订版 DOCX 生成（打开原 DOCX，按条款文本定位段落并替换，另存新文件）。

不修改原文件；只替换已修改条款对应的段落，其余段落保持原样。
不做 Word 修订痕迹（Track Changes）、不做完整版本管理。
"""
import re

from docx import Document


def _norm(s: str) -> str:
    """归一化：把所有连续空白折叠为单空格。"""
    return re.sub(r"\s+", " ", (s or "")).strip()


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度（简单 O(n*m)，条款文本与段落都不长，够用）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _find_paragraph_indices(paragraphs, clause_text: str):
    """定位 clause_text 对应的段落下标列表；未定位返回 []。

    三级匹配：①段落包含条款片段 → 单段；②条款片段横跨多段 → 多段；
    ③兜底：最长公共子串 ≥ 15（LLM 摘录可能与原文略有出入）。
    """
    target = _norm(clause_text)
    if not target:
        return []

    norms = [_norm(p.text) for p in paragraphs]

    for i, t in enumerate(norms):
        if t and target in t:
            return [i]

    idxs = [i for i, t in enumerate(norms) if t and t in target]
    if idxs:
        return idxs

    best_i, best_len = -1, 0
    for i, t in enumerate(norms):
        if not t:
            continue
        l = _lcs_len(target, t)
        if l > best_len:
            best_i, best_len = i, l
    if best_i >= 0 and best_len >= 15:
        return [best_i]
    return []


def _final_clause_map(revisions) -> dict:
    """按时间顺序链式归并：同一链条的「真实原文锚点 → 最终修订结果」。

    连续改同一条款时（A → A1 → A2），前端第二轮输入的 clause_text = 上一轮
    revised_clause；据此把 A2 归并回原始 A，只保留最终结果，避免导出成 A1。

    锚点优先用 original_clause_text（审核时定位到的逐字原文），缺失时退回
    clause_text（LLM 证据）。返回 {verbatim_anchor: final_revised_clause}。
    """
    chain_root = {}   # revised_value -> root_clause_text
    anchor_of = {}    # root_clause_text -> verbatim anchor
    final = {}        # verbatim anchor -> final_revised
    for rev in revisions:
        if getattr(rev, "scope", "clause") != "clause":
            continue
        if not rev.clause_text or not rev.revised_clause:
            continue
        root = chain_root.get(rev.clause_text, rev.clause_text)
        # 只用审核阶段保存的真实原文锚点；无锚点的旧修订跳过（需重新审核）
        anchor = (getattr(rev, "original_clause_text", "") or "").strip()
        if anchor:
            anchor_of[root] = anchor
            final[anchor] = rev.revised_clause
        chain_root[rev.revised_clause] = root
    return final


def build_revised_docx(src_docx_path: str, revisions, dst_docx_path: str):
    """打开原 DOCX，应用条款替换，另存到 dst_docx_path。

    返回 (applied_count, skipped_count)。不修改原文件。
    """
    doc = Document(src_docx_path)
    final_map = _final_clause_map(revisions)

    applied = skipped = 0
    for anchor, revised in final_map.items():
        if not anchor:
            skipped += 1
            continue
        # 1) 段内子串替换：整份合同落在单一段落时也只替换命中片段，保留其余正文
        replaced = False
        for p in doc.paragraphs:
            idx = p.text.find(anchor)
            if idx >= 0:
                p.text = p.text[:idx] + revised + p.text[idx + len(anchor):]
                replaced = True
                break
        if replaced:
            applied += 1
            continue
        # 2) 兜底：锚点跨多个段落时，首段替换、余段清空
        idxs = _find_paragraph_indices(doc.paragraphs, anchor)
        if idxs:
            doc.paragraphs[idxs[0]].text = revised
            for i in idxs[1:]:
                doc.paragraphs[i].text = ""
            applied += 1
        else:
            skipped += 1

    doc.save(dst_docx_path)
    return applied, skipped
