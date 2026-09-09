from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class ClauseRevision(Base):
    """对话修改条款的持久化记录（轻量表，不做版本管理）。

    每条 = 一轮修订：用户 instruction + 原始条款 + 修订结果 + AI 输出元信息。
    多轮对话通过 clause_key 分组 + created_at/id 排序；同一链条（连续改同一条款）
    在导出 DOCX 时按 clause_text→revised_clause 链式归并到最终结果。
    """

    __tablename__ = "clause_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    # 会话作用域："clause"（单条款）| "overview"（整个合同总览）
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="clause")
    # 操作类型："replace"（替换已有条款）| "add_clause"（新增缺失条款，R09）
    operation: Mapped[str] = mapped_column(String(20), nullable=False, default="replace")
    # 新增条款的插入位置（operation=add_clause 时用）：{"anchor":"五","hint":"第五条之后"} 或 {"append":true}
    position: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    # 条款会话标识：str(风险记录 id) 或 "__overview__"（用于刷新后重建各会话）
    clause_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # 条款编号（第 X 条，定位可得时记录，供展示/未来定位兜底）
    clause_no: Mapped[str] = mapped_column(String(50), nullable=True, default=None)
    # 本轮输入的原始条款（第一轮为风险条款原文；后续轮次为该链条上轮修订结果）
    clause_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 真实原文锚点：审核阶段从合同正文定位到的逐字原文（供 DOCX 导出精确替换，不依赖 LLM 改写版）
    original_clause_text: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    revised_clause: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    constraints: Mapped[list] = mapped_column(JSON, nullable=True, default=None)
    legal_basis: Mapped[list] = mapped_column(JSON, nullable=True, default=None)
    remaining_risks: Mapped[list] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
