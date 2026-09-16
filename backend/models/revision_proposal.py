from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class RevisionProposal(Base):
    """「总体修改会话」产出的综合修改方案（**方案本身不是合同修改**）。

    定位（务必与 ClauseRevision 区分）：
    - 本表只保存"方案"：AI 从整份合同 + 各专项会话当前结果归纳出的**待用户确认**的修改项；
    - 真正的合同修改**只**由用户确认具体修改项后生成的 ClauseRevision 承载，
      因此本表**永不参与** DOCX 导出（``services/docx_reviser`` 只消费 ClauseRevision）。
      这是"不允许把 overview+replace 直接写入 DOCX"这条硬约束的结构性保证：
      方案表里没有 ``original_clause_text`` 锚点，DOCX 链路也完全不读它。

    items 元素结构（每次确认按 id 逐项处理）：
    {
      "id": "p1",
      "operation": "replace" | "add_clause",
      "target_session_key": "12" | "__cmp__验收标准" | "__overview__"（未指定时为空串）,
      "clause_no": "3" | "",
      "original_quote": "逐字原文（replace 必须；后端会据此定位原文锚点）",
      "revised_clause": "改后的条款全文（不含条款编号）",
      "reason": "为什么这么改",
      "legal_basis": ["民法典第585条"],
      "position": {"anchor": "五", "hint": "第X条之后"} | {"append": true} | None,
      "resolved": true/false,          # 后端是否已能可靠定位（false 时前端必须让用户确认位置）
      "resolved_by": "session" | "quote" | "clause_no" | "" ,
      "anchor_text": "后端定位到的逐字原文锚点（replace 成功时）",
      "blocking_reason": "无法自动定位的原因（resolved=false 时）"
    }
    """

    __tablename__ = "revision_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    # 创建者（多用户协作下区分"谁提的整体方案"）
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    # 触发本方案的整体修改要求（用户自然语言）
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # draft（待确认）| partially_applied（部分已确认落库）| applied（全部已确认落库）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # 方案总述（AI 给出的一段整体说明，纯展示，不写入合同）
    summary: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    # 具体修改项列表（见类 docstring；已确认的项由 confirmed_ids 标记）
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 已完成确认、并已转成 ClauseRevision 的修改项 id
    confirmed_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
