"""PDF 合同的「可靠原文锚点」建立（`clause_position.original_text`）。

## 为什么需要这个模块（问题背景，已用真实数据实测）

审核阶段原本用 `api/contracts._locate_clause(parsed_text, clause_text)` 取锚点，
它把 **LLM 写的证据文本** `clause_text` 逐字拿去 `parsed_text` 里 `find`。
对 PDF 合同这条链路是坏的——实测真实 PDF 合同（id=95，4 条风险）**仅 1/4 能定位**，
其余 3 条 `clause_position.original_text` 落成 **空字符串**，于是：

    original_clause_text = None → docx_reviser._final_clause_map 直接丢弃该修订
    → 导出一份「看起来完整、实际漏改」的合同

根因（实测证据）：

    parsed_text : '六、合同变更、解除与终止\\n2. 合同解除\\n2.2 双方约定解除合同的条件还包括：…'
    clause_text : '六、合同变更、解除与终止 2. 合同解除 2.2 双方约定解除合同的条件还包括：…'
                                    ↑ 换行被 LLM 改写成了空格，且标题/编号/正文被合并

即 LLM 的 `clause_text` 是**摘要/改写**，不是逐字原文：它会把
目录行与正文行拼在一起、会省略小句（"6.3 双方保证…"）、会改词（"上级主管部门" vs "主管部门"）。

## 本模块的策略（确定性、可解释、无模糊兜底）

只用 **「归一化后的精确子串匹配 + 唯一命中」**：

    Tier 1  连续命中
        在「折叠空白后的 parsed_text」中查找「折叠空白后的 clause_text」的某个连续子串。
        子串由「丢弃开头 token / 丢弃结尾 token」枚举得到，取长度**极大**且唯一命中的那些。

    Tier 2  句级命中共址（覆盖 LLM 把「标题/编号 + 正文」合并的场景）
        按中文句末标点切句，逐句做同样的唯一命中；把彼此靠近
        （相邻间隙 ≤ MAX_GAP 个原文空白字符）的命中聚成一个连续区间。

    末段偏好（`_prefer_body_cluster`）
        当同一段文本在**多个不相邻区域**都能可靠命中时，取位置**最靠后**的那一簇：
        PDF 合同普遍「目录靠前、正文靠后」，目录与正文的同名条款标题会同时命中，
        正文总是更靠后。这是确定性的位置选择，**不是**相似度打分。
        绝不把多个分散区域的文本拼成一个锚点。

**严格禁止**（本项目已确认会静默改错合同，故不实现）：
  - LCS / 最长公共子串等模糊兜底；
  - 逐级缩短到 4 字这类短前缀命中；
  - 多命中时取第一个 / 最长 / 最近 / 随机；
  - 找不到时猜测条款位置或按风险类型推断位置。

**唯一允许的产出**：`original_text` 是 `parsed_text` 的一段**真实连续子串**
（`original_text == parsed_text[start:end]`），这样中间 DOCX 的文本与锚点形成稳定闭环。

刻意不依赖也不会触碰：R01–R13 判定、风险等级/计数、分类、要素抽取、Gold、
测试集、评测脚本、`/revise`、`ClauseRevision`、`/overview/confirm`。
本模块是**纯函数**：不查库、不写库、不调 LLM、不调 RAG。
"""
from __future__ import annotations

import re

__all__ = [
    "MIN_ANCHOR_LEN", "MAX_GAP", "collapse_ws", "norm_with_map",
    "build_anchor", "UNRELIABLE_REASON",
]

# 锚点最低可靠长度（归一化后字符数）。低于该长度一律拒绝，绝不"猜"。
# 实测：真实 PDF 合同的可用锚点长度分别为 82 / 22 / 95 / 35，远高于此阈值。
MIN_ANCHOR_LEN = 8

# 句级命中共址时，相邻命中区间之间允许的最大原文间隙（字符数）。
# 实测：96 行 PDF 的"保密"条款三条句子之间只隔 1 个换行符，故 2 已足够；
# 而 LLM 拼接目录+正文的错配会跨几十行，间隙远大于此值，从而被正确拒绝。
MAX_GAP = 2

# 丢弃 token 的上限：避免在超长被改写文本上退化成近似搜索
MAX_DROP_TOKENS = 60

# 分句标点（中文句末 + 换行已在折叠阶段处理）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。；！？])")

UNRELIABLE_REASON = "无法在合同正文中唯一可靠定位该条款，未建立锚点"


def collapse_ws(text: str) -> str:
    """把任意连续空白折叠为单个空格并去首尾空白（与 docx_reviser._norm 同口径）。"""
    return re.sub(r"\s+", " ", text or "").strip()


def norm_with_map(text: str) -> tuple[str, list[int]]:
    """折叠空白，同时给出「归一化字符 → 原文下标」映射，两者**逐位对齐**。

    折叠产生的那个空格映射到它所属的原始空白字符下标，因此对归一化区间
    ``[pos, pos+n)`` 可以精确还原成原文区间
    ``text[cmap[pos] : cmap[pos + n - 1] + 1]``。
    """
    out: list[str] = []
    cmap: list[int] = []
    for i, ch in enumerate(text or ""):
        if ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                cmap.append(i)
            continue
        out.append(ch)
        cmap.append(i)
    # 与 collapse_ws 保持一致：去掉首尾折叠出的空格
    while out and out[-1] == " ":
        out.pop()
        cmap.pop()
    while out and out[0] == " ":
        out.pop(0)
        cmap.pop(0)
    return "".join(out), cmap


def _unique_pos(norm_text: str, probe: str) -> int:
    """``probe`` 在 ``norm_text`` 中**恰好出现 1 次**时返回其下标，否则 -1。

    0 次（找不到）与 >1 次（多命中）都返回 -1 —— 多命中绝不选一个。
    """
    if len(probe) < MIN_ANCHOR_LEN:
        return -1
    first = norm_text.find(probe)
    if first < 0:
        return -1
    if norm_text.find(probe, first + 1) >= 0:
        return -1
    return first


def _span_from_norm(cmap: list[int], pos: int, norm_len: int) -> tuple[int, int]:
    """把归一化区间映射回原文区间（左闭右开）。"""
    return cmap[pos], cmap[pos + norm_len - 1] + 1


def _norm_digit_space(text: str) -> str:
    """去掉**数字之间**的空白（用于判断"仅数字内部空白不同"这一等价情形）。"""
    return re.sub(r"(?<=\d)\s+(?=\d)", "", text or "")


def _digit_space_variants(norm_text: str, cmap: list[int]):
    """生成「数字内部空白差异」的容错变体，返回 ``[(variant_text, index_map), ...]``。

    背景（真实 OCR 产物）：扫描件识别常把数字与符号拆开，例如原文 ``30%`` 被识别成
    ``30 %``（OCR 在数字与 % 之间插了空格），而 LLM 写的证据文本往往不带这个空格。
    两者在"忽略数字间空白"的口径下本是同一段文字，但逐字子串匹配会失败，
    导致本该可定位的条款建不起锚点。

    只放宽**数字之间／数字与相邻字符之间**的空白（最常见、最无歧义的一类），
    **不引入任何相似度评分**：命中仍要求唯一，且两侧"忽略数字空白后"必须完全一致。
    ``index_map[i]`` = 变体第 i 个字符对应的 ``norm_text`` 下标。
    """
    # 变体 A：删除数字之间的空白（"30 %" → "30%"）
    a_chars: list[str] = []
    a_map: list[int] = []
    for i, ch in enumerate(norm_text):
        if (ch == " " and a_chars and a_chars[-1].isdigit()
                and i + 1 < len(norm_text) and norm_text[i + 1].isdigit()):
            continue                      # 丢弃数字之间的空格
        a_chars.append(ch)
        a_map.append(i)

    # 变体 B：在相邻数字之间插入一个空格（"30%" → "3 0%"，覆盖相反情形）
    b_chars: list[str] = []
    b_map: list[int] = []
    for i, ch in enumerate(norm_text):
        if b_chars and ch.isdigit() and b_chars[-1].isdigit():
            b_chars.append(" ")
            b_map.append(i)
        b_chars.append(ch)
        b_map.append(i)

    return [("".join(a_chars), a_map), ("".join(b_chars), b_map)]


def _unique_span_relaxed(norm_text: str, cmap: list[int], probe: str):
    """唯一命中（允许数字内部空白差异）。返回 ``(start, end)`` 原文区间或 None。"""
    hit = _unique_pos(norm_text, probe)
    if hit >= 0:
        return _span_from_norm(cmap, hit, len(probe))

    target = _norm_digit_space(probe)
    if len(target) < MIN_ANCHOR_LEN or not _norm_digit_space(norm_text):
        return None
    for variant, index_map in _digit_space_variants(norm_text, cmap):
        if len(variant) < len(target):
            continue
        first = variant.find(target)
        if first < 0:
            continue
        if variant.find(target, first + 1) >= 0:
            return None                   # 多命中 → 拒绝（与严格路径同规则）
        start = cmap[index_map[first]]
        end = cmap[index_map[first + len(target) - 1]] + 1
        return start, end
    return None


def _tier1_candidates(norm_text: str, cmap: list[int], norm_clause: str) -> list[tuple[int, int]]:
    """连续命中：枚举「丢开头 / 丢结尾」得到的连续子串，取**所有**唯一命中且长度为极大值的候选。

    只保留长度等于最大命中长度的候选（同一段文本在目录与正文各出现一次时，
    两个候选长度相同，都会被保留下来交给 `_prefer_body_cluster` 判定）。
    """
    if not norm_text or not norm_clause:
        return []
    tokens = norm_clause.split(" ")
    total = len(tokens)

    candidates: list[tuple[bool, str]] = []
    for drop in range(0, min(MAX_DROP_TOKENS, total) + 1):
        candidates.append((drop == 0, " ".join(tokens[drop:])))
    for keep in range(total, max(0, total - MAX_DROP_TOKENS) - 1, -1):
        candidates.append((False, " ".join(tokens[:keep])))

    spans: list[tuple[int, int]] = []
    best_len = 0
    for exact, probe in candidates:
        probe = probe.strip()
        span = _unique_span_relaxed(norm_text, cmap, probe)
        if span is None:
            continue
        cand_len = span[1] - span[0]
        if cand_len > best_len:
            best_len = cand_len
            spans = [span]
        elif cand_len == best_len and span not in spans:
            spans.append(span)
        if exact and spans:
            break          # 完整 clause_text 即唯一命中 → 已是最优，直接收敛
    return spans


def _tier2_candidates(norm_text: str, cmap: list[int], norm_clause: str) -> list[tuple[int, int]]:
    """句级命中共址候选：返回可能存在的**多个**连续区间（目录区 / 正文区各一个）。"""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(norm_clause) if s.strip()]
    hits: list[tuple[int, int]] = []
    for sent in sentences:
        if len(sent) < MIN_ANCHOR_LEN:
            continue
        span = _unique_span_relaxed(norm_text, cmap, sent)
        if span is None:
            # 该句被改写 → 逐级丢弃开头 token 后重试（仍是精确/等效匹配）
            tokens = sent.split(" ")
            for drop in range(1, min(MAX_DROP_TOKENS, len(tokens))):
                probe = " ".join(tokens[drop:]).strip()
                span = _unique_span_relaxed(norm_text, cmap, probe)
                if span is not None:
                    break
        if span is not None:
            hits.append(span)

    if not hits:
        return []
    hits.sort()

    # 把命中聚成「彼此靠近」的簇：相邻间隙 <= MAX_GAP 视为同一簇
    clusters: list[list[tuple[int, int]]] = [[hits[0]]]
    for span in hits[1:]:
        if span[0] - clusters[-1][-1][1] <= MAX_GAP:
            clusters[-1].append(span)
        else:
            clusters.append([span])

    out = []
    for cluster in clusters:
        start, end = cluster[0][0], cluster[-1][1]
        if end - start >= MIN_ANCHOR_LEN:
            out.append((start, end))
    return out


def _prefer_body_cluster(spans: list[tuple[int, int]]) -> tuple[int, int] | None:
    """当同一段文本在**多个不相邻区域**都能可靠命中时，取位置**最靠后**的那一簇。

    理由（基于真实合同结构，非启发式猜测）：PDF 合同普遍「目录靠前、正文靠后」，
    目录区的条款标题与正文区的同名标题会同时命中。正文总是更靠后，
    因此"取最后一簇"是确定性的，且不引入任何相似度评分。

    多个簇时只在其中选一个；若最后一簇与其它簇重叠/相邻（会被并成同一簇，不会走到这里），
    则保持唯一性。**绝不**把多个分散区域的文本拼成一个锚点。
    """
    if not spans:
        return None
    distinct = sorted(set(spans))
    if len(distinct) == 1:
        return distinct[0]
    return distinct[-1]


def build_anchor(parsed_text: str, clause_text: str):
    """为一处风险/条款建立**可靠原文锚点**。

    :returns: ``(original_text, start, end)`` 或 ``None``。
              ``original_text`` 恒等于 ``parsed_text[start:end]``（真实连续子串）。
              返回 ``None`` 表示「无法唯一可靠定位」——调用方据此**明确提示用户**，
              **不得**自行猜测位置，也不得生成静默漏改的修订版。
    """
    norm_text, cmap = norm_with_map(parsed_text or "")
    norm_clause = collapse_ws(clause_text or "")
    if not norm_text or not norm_clause:
        return None

    # 两层都算，再挑**更长**的那个作为锚点：
    # 目录区常能让 Tier1 命中一个很短的候选（例如「八、其他\n4. 保密」10 字），
    # 而 Tier2 能在正文区命中覆盖整个条款的长候选（95 字）。锚点越长，
    # docx_reviser 的「段内子串替换」越精确，因此长度是本模块唯一使用的取舍依据
    # （确定性，不涉及任何相似度评分）。
    t1 = _tier1_candidates(norm_text, cmap, norm_clause)
    best = _prefer_body_cluster(t1)
    t2 = _tier2_candidates(norm_text, cmap, norm_clause)
    t2_best = _prefer_body_cluster(t2)
    if t2_best is not None and (best is None or (t2_best[1] - t2_best[0]) > (best[1] - best[0])):
        best = t2_best
    span = best
    if span is None:
        return None

    start, end = span
    if start < 0 or end > len(parsed_text) or end - start < MIN_ANCHOR_LEN:
        return None
    original_text = parsed_text[start:end]
    # 自检：锚点必须是 parsed_text 的真实子串，且与解析文本口径一致
    if parsed_text.find(original_text) != start:
        return None
    return original_text, start, end
