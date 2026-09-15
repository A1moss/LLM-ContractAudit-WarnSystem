from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class FeedbackLog(Base):
    """人工反馈的**唯一录入与留痕表**（不做学习判定，不参与 RAG）。

    角色分工（本创新点的核心边界）：
    - 本表 = 原始反馈留痕：谁、何时、对哪条记录、点了什么、写了什么理由；
    - 学习语料 = `feedback_experiences`（仅保存**经人工批准**的经验），
      本表通过 `status` 驱动那条链路，绝不自己变成知识库。

    生命周期（status）：
        pending → reviewed / rejected → approved_for_learning → active → revoked
    - pending：刚提交，未审核
    - reviewed：审核通过，等待批准
    - rejected：审核不通过，永不进入学习
    - approved_for_learning：**已批准**，但尚未完成经验入库（此时还不能被检索）
    - active：已被批准且经验已入库，可被 Feedback RAG 检索
    - revoked：经验已撤销（检索已排除），本行保留作审计轨迹

    注意：`status` 以本表为**反馈池状态**；"能否被检索"的权威来源是
    `feedback_experiences.status == 'active'`（见 ai/rag/feedback_store.py）。
    """

    __tablename__ = "feedback_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("audit_records.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # 提交动作（沿用既有四值，不重定义语义）：confirmed / corrected / false_positive / supplemented
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    original_risk: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    corrected_risk: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    comment: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    # ── 学习链路新增：归属与过滤键（冗余存，避免 AuditRecord 被 superseded 后失去上下文）──
    contract_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None, index=True)
    contract_type: Mapped[str] = mapped_column(String(50), nullable=True, default=None)
    risk_type: Mapped[str] = mapped_column(String(10), nullable=True, default=None, index=True)
    # 归因（比自由文本更适合统计与检索）：
    # clause_exists / evidence_gap / semantic_error / contract_type_error
    # / adjudicator_rule_suspect（**只进规则反馈池，禁止自动改规则**）/ other
    feedback_reason: Mapped[str] = mapped_column(String(30), nullable=True, default=None, index=True)
    # ── 学习链路新增：生命周期 ──
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )
    # ── 预留：第一阶段只启用 risk；clause_revision / comparison_clause 待第二阶段 ──
    target_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="risk", server_default="risk"
    )
    revision_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    target_ref: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    # ── 学习链路新增：审核 / 批准留痕（职责分离用）──
    reviewed_by: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)
    review_comment: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    approved_by: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)
    # ── 学习链路新增：模型侧快照（审核当时的 evidence / 完整风险对象）──
    model_evidence: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    model_result: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
