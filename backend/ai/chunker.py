"""
ai.chunker — 长合同文本分块
把超长合同按条款标题边界切成若干块，保证每块不超过上限且尽量不切断条款，
供规则引擎之外的 LLM 审核 / Corex 多 Agent 审核逐块处理，避免尾部内容被截断漏检。

切分优先级：
1. 在条款标题行（"第X条""第X章""一、""（一）""1."）处断块，保证条款完整性；
2. 单个条款块仍超长时，在换行处硬切；
3. 无明确条款编号的纯段落文本，退化为按行硬切。
"""
import re

# 条款标题行：第X条/第X章/第X款、一、二、...、（一）（二）...、1. 2. ...
_HEADING_RE = re.compile(
    r"\s*("
    r"第[一二三四五六七八九十百千零〇\d]+[条章节款]"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|[（(][一二三四五六七八九十\d]+[)）]"
    r"|\d{1,3}[、.．]"
    r")"
)


def _split_segments(text: str) -> list[str]:
    """按条款标题行边界切段，每段以标题开头。"""
    lines = text.split("\n")
    segments = []
    buf = []
    for line in lines:
        if _HEADING_RE.match(line) and buf:
            segments.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        segments.append("\n".join(buf))
    return segments


def _hard_split(text: str, max_chars: int) -> list[str]:
    """单段超长时在换行处硬切，兜底按字符硬切。"""
    chunks = []
    while len(text) > max_chars:
        cut = text.rfind("\n", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text.strip():
        chunks.append(text)
    return chunks


def split_chunks(text: str, max_chars: int = 12000) -> list[str]:
    """
    把长合同切成若干块，每块不超过 max_chars，尽量在条款边界断。

    Args:
        text: 完整合同文本
        max_chars: 单块字符上限（默认 12000，与历史单次审核上限一致）

    Returns:
        list[str]: 块列表；空文本返回 []；短文本返回 [原文]。
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    segments = _split_segments(text)
    chunks = []
    buf = ""
    for seg in segments:
        # 单段超长：先落当前缓冲，再硬切该段
        if len(seg) > max_chars:
            if buf.strip():
                chunks.append(buf)
                buf = ""
            chunks.extend(_hard_split(seg, max_chars))
            continue
        # 拼入会超限：当前缓冲落块，另起一块
        if buf and len(buf) + 1 + len(seg) > max_chars:
            chunks.append(buf)
            buf = seg
        else:
            buf = (buf + "\n" + seg) if buf else seg
    if buf.strip():
        chunks.append(buf)
    return chunks or [text]
