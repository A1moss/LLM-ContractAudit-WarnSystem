import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # 多用户角色：uploader(上传者)/reviewer(审核人)/approver(验收人)/admin(管理员)
    role: Mapped[str] = mapped_column(String(20), default="uploader")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 用户个人 DeepSeek API Key（Fernet 加密后的密文，见 services/user_secret.py）。
    # None = 未配置 → 调用 LLM 时回退 .env 的 DEEPSEEK_API_KEY（系统默认 Key）。
    # 任何接口都不得返回该字段或其明文。
    deepseek_api_key_enc: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
