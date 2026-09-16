from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class ClauseRevision(Base):
    """对话修改条款的持久化记录（轻量表，不做版本管理）。

    每条 = 一轮修订：用户 instruction + 原始条款 + 修订结果 + AI 输出元信息。
    多轮对话通过 clause_key 分组 + created_at/id 排序；同一链条（连续改同一条款）
    在导出 DOCX 时按 clause_text→revised_clause 链式归并到最终结果。

    ``adopted``（采用态，V2.2）：
      「生成一轮」与「用户确认采用这一轮」是两件事。``/revise`` 每成功一轮就落一行
      （adopted=False），只有用户点「确认采用此版」/「确认新增」或从总体方案
      ``/overview/confirm`` 落库时才置 True。DOCX 归并时**归并组内优先取 adopted**，
      组内没有 adopted 才回退到今天的行为（取 id 最大的一条）——保证历史数据零回归。

    唯一范围：``(contract_id, clause_key)`` —— 一个合同的一个会话至多一个 adopted。
    注意 DOCX 归并单位是锚点/位置（不是 clause_key），不同会话命中同一锚点时
    仍按后写覆盖（已知边界，本轮不处理）。
    """

    __tablename__ = "clause_revisions"
    __table_args__ = (
        # 采用确认与 DOCX 归并都按 (contract_id, clause_key) 过滤，加复合索引
        Index("ix_clause_revisions_contract_key", "contract_id", "clause_key"),
    )

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
    # 是否用户明确确认采用的版本（同一 contract_id + clause_key 下至多一条为 True）
    adopted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
