from datetime import datetime

from sqlalchemy import String, Integer, Float, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class AuditRecord(Base):
    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    audit_batch: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    risk_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)
    clause_text: Mapped[str] = mapped_column(Text, nullable=False)
    clause_position: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    reason: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    suggestion: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    detection_method: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    corex_agent_log: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    # 可溯源证据链：本风险所依据的知识库条目（法条/标准条款），支持答辩"可解释性"
    evidence: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    # v6.5 建议层：风险说明/修改示例/法律依据/接地检查（与裁决解耦）
    recommendation: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    feedback_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # 审核结果有效性：valid / rejected / superseded（BUG-028，描述「这条审核结果当前是否仍具业务有效性」）
    result_status: Mapped[str] = mapped_column(String(20), nullable=False, default="valid")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
