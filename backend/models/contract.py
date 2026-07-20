from datetime import datetime

from sqlalchemy import String, Integer, Float, Text, DateTime, JSON, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(50), nullable=True, default=None)
    type_confidence: Mapped[float] = mapped_column(Float, nullable=True, default=None)
    parsed_text: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    extracted_elements: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    audit_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="fast")
    our_role: Mapped[str] = mapped_column(String(10), nullable=True, default=None)  # party_a / party_b / neutral
    template_version: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
