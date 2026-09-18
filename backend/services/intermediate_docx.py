"""PDF/非 DOCX 合同的「规范化中间 DOCX」生成器。

定位（**重要**）：中间 DOCX 不是「转换下载版」，而是**修订引擎使用的内部规范化载体**。
最终用户只会拿到 `build_revised_docx` 产出的修订版 DOCX。

设计约束（与 P0 调查结论一致）：
- 只用已安装的 `python-docx`，**不新增依赖**；
- **不重新解析原始 PDF**，直接消费已经落库的 `contracts.parsed_text`；
- `parsed_text.split("\\n")` → 每一行按原顺序写成一个 paragraph；
- **不合并文本、不改文本内容**（每个段落文本 = 该行折叠空白后的内容，见下）。

为什么段落文本要折叠空白：
  `services/docx_reviser.py` 的定位单位是「单个 paragraph」——
  ① 先做段内子串替换 `p.text.find(anchor)`，② 再做跨段兜底 `_find_paragraph_indices`。
  若段落文本保留原始换行，跨行锚点（PDF 里非常常见）就无法在**单段内**命中，
  只能走跨段兜底，而该兜底一旦遇到更长的无关段落会命中错位置。
  折叠空白后：`norm(中间DOCX全文) == norm(parsed_text)`（已实测），
  跨行锚点变成单段内子串 → 走 ① 原地替换，**准确且可预测**。

与 `parsed_text` 的对应关系（两条都必须成立，已由测试锁定）：
  A. `"\\n".join(p.text for p in doc.paragraphs) == parsed_text`   （逐字无损，含空行）
  B. `norm(中间DOCX全文) == norm(parsed_text)`                     （修订引擎定位前提）
"""
from __future__ import annotations

import re

from docx import Document

__all__ = ["collapse_ws", "build_from_parsed_text"]


def collapse_ws(text: str) -> str:
    """把任意连续空白折叠为单个空格并去掉首尾空白。

    与 `services/docx_reviser._norm`、`api/contracts._locate_clause` 的口径一致，
    保证「锚点」与「段落文本」在同一归一化口径下比较。
    """
    return re.sub(r"\s+", " ", text or "").strip()


def build_from_parsed_text(parsed_text: str, out_path: str) -> int:
    """把已落库的 ``parsed_text`` 写成一份规范化 DOCX，返回写入的段落数。

    - 逐行处理，**空行也保留为空段落**，因此
      ``"\\n".join(p.text for p in Document(out_path).paragraphs) == parsed_text`` 恒成立；
    - 段落文本 = 该行 ``collapse_ws`` 的结果（见模块 docstring 的原因）；
    - 只读入参、只写 ``out_path``，无任何其它副作用。
    """
    doc = Document()
    lines = (parsed_text or "").split("\n")
    for line in lines:
        doc.add_paragraph(collapse_ws(line))
    doc.save(out_path)
    return len(lines)
