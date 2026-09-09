"""docx_reviser — 修订版 DOCX 生成（打开原 DOCX，按条款文本定位段落并替换/插入，另存新文件）。

不修改原文件；只替换已修改条款对应的段落、插入新增缺失条款（add_clause），其余段落保持原样。
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


# ── 新增条款（add_clause）插入 + 最小可靠编号 ──────────────────────────────

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str) -> int | None:
    """中文数字 → 整数（一~九十九），无法解析返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        parts = s.split("十")
        tens = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
        ones = _CN_DIGITS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(s)


def _int_to_cn(n: int) -> str:
    """整数 → 中文数字（1~99）。"""
    digits = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if n <= 0:
        return str(n)
    if n < 10:
        return digits[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + digits[n - 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        return digits[tens] + "十" + ("" if ones == 0 else digits[ones])
    return str(n)


# 标题样式："五、"（一、二、三…）或 "第五条"（第X条）。group(1)="第"/""，group(2)=数字，group(3)="条"/"、"
_HEADING_RE = re.compile(r'(第\s*)?([一二三四五六七八九十百千\d]+)\s*(条|、)')


def _heading_num(text: str) -> int | None:
    """若文本以「第X条」/「X、」标题开头，返回其编号，否则 None。"""
    m = _HEADING_RE.match((text or "").lstrip())
    if not m:
        return None
    return _cn_to_int(m.group(2))


def _set_heading(text: str, new_num: int) -> str:
    """把文本开头的标题编号改为 new_num，保留前缀/分隔符与标题后正文。"""
    m = _HEADING_RE.match((text or "").lstrip())
    if not m:
        return text
    lead = len(text) - len(text.lstrip())
    cn = _int_to_cn(new_num)
    return text[:lead] + (m.group(1) or "") + cn + m.group(3) + text[lead + m.end():]


def _renumber_text(text: str, greater_than: int) -> str:
    """把文本中所有编号 > greater_than 的标题（第X条 / X、）编号 +1。"""

    def repl(m):
        n = _cn_to_int(m.group(2))
        if n is not None and n > greater_than:
            return (m.group(1) or "") + _int_to_cn(n + 1) + m.group(3)
        return m.group(0)

    return _HEADING_RE.sub(repl, text)


def _pos_key(position) -> str:
    """位置归一化键（用于 add_clause 多轮修改「同位置只留最终版」）。"""
    position = position or {}
    if position.get("append"):
        return "append"
    return "anchor:" + str(position.get("anchor", ""))


def _final_add_clause_map(revisions) -> list:
    """收集新增条款（operation="add_clause"）：同一插入位置只保留最后一条。

    用户对同一条新增条款多轮修改（继续修改）会生成多条 add_clause 修订，
    位置相同 → 只保留最终版，避免重复插入。
    返回 [{"position": {...}, "clause_text": "..."}, ...]（按首次出现顺序）。
    """
    final = {}
    order = []
    for rev in revisions:
        if getattr(rev, "operation", "replace") != "add_clause":
            continue
        if not rev.revised_clause:
            continue
        pos = getattr(rev, "position", None) or {}
        key = _pos_key(pos)
        if key not in final:
            order.append(key)
        final[key] = {"position": pos, "clause_text": rev.revised_clause}
    return [final[k] for k in order]


def _has_paragraph_heading(doc, anchor_num: int) -> bool:
    """是否存在以 anchor_num 编号开头的段落（段落级标题结构）。"""
    for p in doc.paragraphs:
        if _heading_num(p.text) == anchor_num:
            return True
    return False


def _append_clause(doc, clause_text: str) -> tuple[bool, str]:
    """追加新增条款到文档末尾；编号取文档中最大标题编号 + 1（无可识别标题则不编）。"""
    max_num = 0
    has_heading = False
    for p in doc.paragraphs:
        n = _heading_num(p.text)
        if n is not None:
            has_heading = True
            max_num = max(max_num, n)
        else:
            for m in _HEADING_RE.finditer(p.text):
                n2 = _cn_to_int(m.group(2))
                if n2 is not None:
                    has_heading = True
                    max_num = max(max_num, n2)
    if has_heading:
        doc.add_paragraph(f"{_int_to_cn(max_num + 1)}、{clause_text}")
    else:
        # 无法识别编号：不猜编号，直接追加正文（编号问题已在生成前由用户确认位置时解决）
        doc.add_paragraph(clause_text)
    return True, "已追加到合同末尾"


def _insert_paragraph_level(doc, clause_text: str, anchor_num: int) -> tuple[bool, str]:
    """段落级结构：标题独立成段/段首。在 anchor 标题段之后、下一标题段之前插入，并顺延后续编号。"""
    paras = doc.paragraphs
    anchor_idx = -1
    for i, p in enumerate(paras):
        if _heading_num(p.text) == anchor_num:
            anchor_idx = i
            break
    if anchor_idx < 0:
        return False, "未找到插入位置"

    # 1) 顺延编号：所有编号 > anchor_num 的标题 +1
    for p in paras:
        n = _heading_num(p.text)
        if n is not None and n > anchor_num:
            p.text = _set_heading(p.text, n + 1)

    # 2) 找下一标题段（重编号后其编号仍 > anchor_num）
    next_idx = -1
    for i in range(anchor_idx + 1, len(paras)):
        if _heading_num(paras[i].text) is not None and _heading_num(paras[i].text) > anchor_num:
            next_idx = i
            break

    new_text = f"{_int_to_cn(anchor_num + 1)}、{clause_text}"
    if next_idx >= 0:
        paras[next_idx].insert_paragraph_before(new_text)
    else:
        doc.add_paragraph(new_text)
    return True, ""


def _insert_inline(doc, clause_text: str, position, anchor_num: int) -> tuple[bool, str]:
    """单段合同（整份落在一个段落）：在段落内联标题处插入并顺延后续编号。"""
    anchor_cn = (position or {}).get("anchor", "")
    for p in doc.paragraphs:
        text = p.text
        anchor_match = None
        next_match = None
        for m in _HEADING_RE.finditer(text):
            n = _cn_to_int(m.group(2))
            if n == anchor_num and anchor_match is None:
                anchor_match = m
            if n is not None and n > anchor_num and next_match is None:
                next_match = m
                break
        if anchor_match is None:
            continue  # 不在这一段

        new_cn = _int_to_cn(anchor_num + 1)
        if next_match is None:
            # 无后续标题：顺延编号后追加到段落末尾
            new_text = _renumber_text(text, anchor_num)
            p.text = new_text + f" {new_cn}、{clause_text}"
            return True, ""

        # 先顺延编号，再在重编号后的下一标题前插入新条款
        new_text = _renumber_text(text, anchor_num)
        insert_idx = -1
        for m in _HEADING_RE.finditer(new_text):
            n = _cn_to_int(m.group(2))
            if n is not None and n > anchor_num:
                insert_idx = m.start()
                break
        if insert_idx < 0:
            return False, f"插入位置「{anchor_cn}」无法可靠识别，请明确插入位置"
        p.text = new_text[:insert_idx] + f"{new_cn}、{clause_text}" + new_text[insert_idx:]
        return True, ""
    return False, f"未找到插入位置「{anchor_cn}」"


def _insert_one(doc, clause_text: str, position) -> tuple[bool, str]:
    """按 position 插入一条新增条款，返回 (ok, msg)。"""
    clause_text = (clause_text or "").strip()
    if not clause_text:
        return False, "新增条款内容为空"

    if position and position.get("append"):
        return _append_clause(doc, clause_text)

    anchor = (position or {}).get("anchor", "")
    if not anchor:
        return False, "未指定插入位置"
    anchor_num = _cn_to_int(anchor)
    if anchor_num is None:
        return False, f"插入位置「{anchor}」无法识别，请明确条款编号"

    if _has_paragraph_heading(doc, anchor_num):
        return _insert_paragraph_level(doc, clause_text, anchor_num)
    return _insert_inline(doc, clause_text, position, anchor_num)


def build_revised_docx(src_docx_path: str, revisions, dst_docx_path: str):
    """打开原 DOCX，应用条款替换 + 新增条款插入，另存到 dst_docx_path。

    返回 (applied_count, skipped_count)。不修改原文件。
    新增条款插入失败（位置无法定位）时抛 ValueError，调用方据此返回明确错误。
    """
    doc = Document(src_docx_path)
    final_map = _final_clause_map(revisions)

    applied = skipped = 0
    # 1) 替换已有条款（段内子串替换 + 多段兜底）
    for anchor, revised in final_map.items():
        if not anchor:
            skipped += 1
            continue
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
        idxs = _find_paragraph_indices(doc.paragraphs, anchor)
        if idxs:
            doc.paragraphs[idxs[0]].text = revised
            for i in idxs[1:]:
                doc.paragraphs[i].text = ""
            applied += 1
        else:
            skipped += 1

    # 2) 插入新增条款（R09 缺失条款等）
    for ac in _final_add_clause_map(revisions):
        ok, msg = _insert_one(doc, ac["clause_text"], ac["position"])
        if ok:
            applied += 1
        else:
            raise ValueError(msg)

    doc.save(dst_docx_path)
    return applied, skipped
