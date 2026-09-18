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


def _is_adopted(rev) -> bool:
    """该修订是否为用户明确确认采用的版本。

    必须用 ``getattr`` 兜底：测试夹具（SimpleNamespace）与历史数据都可能没有该属性，
    直接 ``rev.adopted`` 会 AttributeError。
    """
    return bool(getattr(rev, "adopted", False))


def _pick_final(group: list):
    """从一个**归并组**里选出最终生效的那一条修订。

    采用态优先（V2.2）：
      1. 组内存在 adopted=True 的行 → 在这些行里取"最后一条"；
      2. 组内没有任何 adopted   → 取整组"最后一条"（**与加入 adopted 之前逐字一致**）。

    "最后一条"的判定对测试夹具必须同样成立：现有 fixture 用 ``types.SimpleNamespace``
    构造修订，**连 id 都没有**，旧的 ``_pick_final`` 实现是靠"按遍历顺序覆盖"决定胜负的。
    因此这里用 ``getattr(r, "id", None)``：id 可用时按 id 最大（等价于时间序最后），
    缺失时退回列表末位（等价于遍历顺序最后）。两种情况都不会抛 AttributeError。

    关键：判断必须是「整个组有没有 adopted」，不能写成"逐行 if not rev.adopted: continue"——
    否则历史数据（全部 adopted=False）会让所有归并组变空，导致全库 DOCX 无法导出。
    """
    adopted_rows = [r for r in group if _is_adopted(r)]
    pool = adopted_rows or group
    if all(getattr(r, "id", None) is not None for r in pool):
        return max(pool, key=lambda r: r.id)
    return pool[-1]


def _final_clause_map(revisions) -> dict:
    """按时间顺序链式归并：同一链条的「真实原文锚点 → 最终修订结果」。

    连续改同一条款时（A → A1 → A2），前端第二轮输入的 clause_text = 上一轮
    revised_clause；据此把 A2 归并回原始 A，只保留最终结果，避免导出成 A1。

    锚点优先用 original_clause_text（审核时定位到的逐字原文），缺失时退回
    clause_text（LLM 证据）。返回 {verbatim_anchor: final_revised_clause}。

    归并组 = 同一个 anchor；组内选谁生效见 ``_pick_final``（adopted 优先，否则最后一条）。
    """
    groups = {}       # anchor -> [rev, ...]（按遍历顺序 = id 升序）
    chain_root = {}   # revised_value -> root_clause_text
    root_anchor = {}  # root_clause_text -> verbatim anchor
    for rev in revisions:
        if getattr(rev, "scope", "clause") != "clause":
            continue
        if not rev.clause_text or not rev.revised_clause:
            continue
        root = chain_root.get(rev.clause_text, rev.clause_text)
        # 只用审核阶段保存的真实原文锚点；无锚点的旧修订跳过（需重新审核）
        anchor = (getattr(rev, "original_clause_text", "") or "").strip()
        if anchor:
            root_anchor[root] = anchor
            groups.setdefault(anchor, []).append(rev)
        chain_root[rev.revised_clause] = root
    return {anchor: _pick_final(rows).revised_clause for anchor, rows in groups.items()}


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


# 比对用的编号占位符：只替换标题编号本身，编号之外的文字一字不动
_NUM_TOKEN_PLACEHOLDER = "\u0000"


def _normalize_heading_numbers(text: str) -> str:
    """把文本中所有标题编号（``_HEADING_RE`` 命中的「第X条」/「X、」）替换为统一占位符。

    只供**导出自检**（`api.contracts._verify_revised_docx`）比对使用，不参与任何写入逻辑。

    存在的理由：新增条款（add_clause）插入时，`_insert_paragraph_level` / `_insert_inline`
    会把编号 > anchor_num 的标题**顺延**（编号 +N）。若被替换条款位于插入点之后，
    **修订文本自身的行首编号也会被系统合法改写**（实测：R09 在「第三条」插入，把已替换的
    「第五条 保密…」顺延成「第六条 保密…」），此时逐字比对会把"已写入"误判成"未写入"。

    这里抹掉的正是顺延逻辑唯一会改动的那类 token（同一个 ``_HEADING_RE``），
    编号之外的正文逐字保留 —— 因此"修订正文没写进去"仍然会被自检抓到。
    """
    if not text:
        return text
    return _HEADING_RE.sub(
        lambda m: (m.group(1) or "") + _NUM_TOKEN_PLACEHOLDER + m.group(3), text)


def _pos_key(position) -> str:
    """位置归一化键（用于 add_clause 多轮修改「同位置只留最终版」）。

    优先用**精确定位**（``target_text`` + ``paragraph_index``）：同一编号在合同里可能重复
    出现多次（真实合同实测 55% 存在重复顶层编号），只按编号会把「同一编号的不同出现」
    错误地并成一组，导致其中一处被丢弃、或两处互相覆盖。

    键里必须带 ``paragraph_index``：三处标题文本可能**完全相同**，此时只有行号能区分。
    没有精确定位信息时回退到编号 —— 与加入精确定位之前的行为完全一致。
    """
    position = position or {}
    if position.get("append"):
        return "append"
    target = _norm(position.get("target_text") or "")
    if target:
        idx = position.get("paragraph_index")
        idx_part = idx if isinstance(idx, int) and not isinstance(idx, bool) else ""
        return f"target:{target}#{idx_part}"
    return "anchor:" + str(position.get("anchor", ""))


# ── 精确插入位置定位（R-1 修复核心）────────────────────────────────────────
def _heading_text_matches(paragraph_text: str, target_text: str, anchor_num: int | None) -> bool:
    """该段落是否为 ``target_text`` 指代的标题段。

    判定 = 段落文本包含 target_text（空白折叠后）+ 该段落本身是**同一编号**的标题。
    两者同时成立才认，避免把正文里偶然包含该字符串的段落当成标题。
    """
    target = _norm(target_text)
    if not target:
        return False
    if target not in _norm(paragraph_text):
        return False
    if anchor_num is not None and _heading_num(paragraph_text) != anchor_num:
        return False
    return True


def _resolve_insert_paragraph(doc, position):
    """解析「新增条款插到哪一个段落之后」，返回 ``(段落下标, 失败原因)``。

    选择顺序（**精确优先；有精确定位信息时绝不回退到"随便取第一个"**）：

      ① ``position["target_text"]`` 精确定位 —— 用户在界面上实际选中的那一处标题文本。
         唯一命中该标题段 → 用它；命中 0 处或多处 → **明确失败**，不猜测。
         ``position["paragraph_index"]``（若给出且校验通过）用于在**重复标题文本**之间
         精确区分：PDF 中间 DOCX 由 ``parsed_text`` 逐行生成，行号即 paragraph 下标；
         DOCX 原件也满足"行序 = paragraph 序"（生成中间 DOCX 时部分与正文的顺序一致）。
      ② 无 ``target_text`` 时，退回既有「按编号找**第一个**」行为。
         这是加入精确定位之前的语义，为历史数据与既有 API 契约保持零回归。

    返回 ``(idx, "")`` 表示成功；返回 ``(-1, reason)`` 表示无法可靠定位。
    """
    paragraphs = doc.paragraphs
    if not paragraphs:
        return -1, "文档中没有可用段落"

    position = position or {}
    anchor = position.get("anchor", "")
    anchor_num = _cn_to_int(anchor)
    target_text = _norm(position.get("target_text") or "")

    if target_text:
        raw_idx = position.get("paragraph_index")
        hint = raw_idx if isinstance(raw_idx, int) and not isinstance(raw_idx, bool) else None
        # ①-a 行号提示：必须同时通过「位置有效 + 该段确为同一编号标题 + 文本匹配」三重校验
        if hint is not None and 0 <= hint < len(paragraphs):
            if _heading_text_matches(paragraphs[hint].text, target_text, anchor_num):
                return hint, ""
        # ①-b 退化为全文扫描（标题文本通常在文档中唯一）
        hits = [i for i, p in enumerate(paragraphs)
                if _heading_text_matches(p.text, target_text, anchor_num)]
        if len(hits) == 1:
            return hits[0], ""
        if not hits:
            return -1, (f"未找到与所选位置匹配的条款标题「{target_text[:30]}」，"
                        f"为避免插错位置已取消插入")
        return -1, (f"所选位置「{target_text[:30]}」在文档中出现 {len(hits)} 次，"
                    f"无法唯一定位，为避免插错位置已取消插入")

    # 兼容路径：没有精确定位信息时，沿用既有「按编号找第一个」语义
    if anchor_num is None:
        return -1, f"插入位置「{anchor}」无法识别，请明确条款编号"
    for i, p in enumerate(paragraphs):
        if _heading_num(p.text) == anchor_num:
            return i, ""
    return -1, "未找到插入位置"


def _final_add_clause_map(revisions) -> list:
    """收集新增条款（operation="add_clause"）：同一插入位置只保留最终一条。

    用户对同一条新增条款多轮修改（继续修改）会生成多条 add_clause 修订，
    位置相同 → 只保留最终版，避免重复插入。

    归并组 = 同一个 ``_pos_key(position)``；组内选谁生效见 ``_pick_final``
    （adopted 优先，否则最后一条 —— 与加入 adopted 之前的行为一致）。
    返回 [{"position": {...}, "clause_text": "..."}, ...]（按首次出现顺序）。
    """
    groups = {}
    order = []
    for rev in revisions:
        if getattr(rev, "operation", "replace") != "add_clause":
            continue
        if not rev.revised_clause:
            continue
        pos = getattr(rev, "position", None) or {}
        key = _pos_key(pos)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(rev)
    out = []
    for k in order:
        chosen = _pick_final(groups[k])
        out.append({
            "position": (getattr(chosen, "position", None) or {}),
            "clause_text": chosen.revised_clause,
        })
    return out


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


def _insert_paragraph_level(doc, clause_text: str, position, anchor_num: int,
                            anchor_idx: int | None = None) -> tuple[bool, str]:
    """段落级结构：标题独立成段/段首。

    在**用户实际选中的那个**标题段之后、下一标题段之前插入，并顺延后续编号。

    `anchor_idx` 由 `_insert_one` 解析后传入（**必须在顺延编号之前解析**：
    顺延会把后续标题的编号 +1，事后再解析就可能对不上原锚点了）。
    未传时按 `position` 现场解析（保持该函数的独立可用性）。
    """
    paras = doc.paragraphs
    if anchor_idx is None:
        anchor_idx, why = _resolve_insert_paragraph(doc, position)
        if anchor_idx < 0:
            return False, why

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
    """按 position 插入一条新增条款，返回 (ok, msg)。

    位置解析顺序（与 `_resolve_insert_paragraph` 一致）：
      - 有 `target_text` 精确定位 → 必须唯一命中，否则**明确失败**（绝不插错位置）；
      - 无精确定位信息 → 沿用既有「按编号找第一个」语义（历史数据零回归）。
    """
    clause_text = (clause_text or "").strip()
    if not clause_text:
        return False, "新增条款内容为空"

    if position and position.get("append"):
        return _append_clause(doc, clause_text)

    position = position or {}
    anchor = position.get("anchor", "")
    anchor_num = _cn_to_int(anchor) if anchor else None
    has_target = bool(_norm(position.get("target_text") or ""))

    if anchor_num is None and not has_target:
        if not anchor:
            return False, "未指定插入位置"
        return False, f"插入位置「{anchor}」无法识别，请明确条款编号"
    if anchor_num is None:
        return False, "缺少可解析的条款编号，无法确定新增条款编号"

    # ① 精确定位：只认「段落级标题」命中；命中数 ≠ 1 一律失败
    if has_target:
        idx, why = _resolve_insert_paragraph(doc, position)
        if idx >= 0:
            # 注意：把**解析好的下标**传下去。`_insert_paragraph_level` 会先顺延后续标题编号，
            # 若让它事后再解析，就可能因为编号已被改动而对不上原锚点。
            return _insert_paragraph_level(doc, clause_text, position, anchor_num, anchor_idx=idx)
        # ①-b 唯一允许的宽松回退：`paragraph_index` **本身指向一个编号正确的标题段**，
        #      只是该段文本与 target_text 的措辞有细微差异（空白/标点）。
        #      这既确定（下标是用户选的那一处）又无歧义（编号自洽）。
        #      除此之外一律不回退：
        #        · target_text 命中多处（真歧义）→ 失败
        #        · target_text 命中的段编号与 anchor 矛盾（位置信息自相矛盾）→ 失败
        #        · 只有编号独一、但下标既缺失又不自洽 → 失败（避免"按编号猜一个"）
        raw_idx = position.get("paragraph_index")
        if isinstance(raw_idx, int) and not isinstance(raw_idx, bool) \
                and 0 <= raw_idx < len(doc.paragraphs) \
                and _heading_num(doc.paragraphs[raw_idx].text) == anchor_num:
            return _insert_paragraph_level(doc, clause_text, position, anchor_num,
                                           anchor_idx=raw_idx)
        return False, why

    # ② 兼容路径：沿用既有判据（有段落级标题 → 段落级；否则 → 段内联插入）
    if _has_paragraph_heading(doc, anchor_num):
        return _insert_paragraph_level(doc, clause_text, position, anchor_num)
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
