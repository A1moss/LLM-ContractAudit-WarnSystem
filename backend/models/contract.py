from datetime import datetime

from sqlalchemy import String, Integer, Float, Text, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(50), nullable=True, default=None)
    # 分类结果状态（BUG-1）：
    #   "success" —— 分类器给出了 taxonomy 内的类别（含模型判定的「无名合同」）
    #   "manual"  —— 上传时用户手工指定了类型
    #   "failed"  —— 分类未跑成功（超时/限流/非法 JSON，有界重试后仍失败）
    #   None      —— 历史数据（改动前写入）；读取时按 contract_type 是否为空推导
    # 不变式：classification_status == "failed" ⇔ contract_type IS NULL。
    # **"无名合同" 是 success 下的一个正式类别，绝不代表失败。**
    classification_status: Mapped[str] = mapped_column(String(20), nullable=True, default=None)
    type_confidence: Mapped[float] = mapped_column(Float, nullable=True, default=None)
    is_outsourcing: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    parsed_text: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    extracted_elements: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    audit_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="precise")
    template_version: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
