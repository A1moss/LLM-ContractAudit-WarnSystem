from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    pool_recycle=3600,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow_naive() -> datetime:
    """应用层统一写入 naive UTC（与 DB 方言解耦，BUG-036）。

    SQLite 的 CURRENT_TIMESTAMP 是 UTC，但 MySQL 的 NOW() 是服务器本地时间（东八区）。
    若依赖 server_default=func.now()，切 MySQL 后时间会偏 8 小时。改为在 ORM
    insert/update 时由应用层统一写入 naive UTC，序列化时补 "Z"，两种方言下语义一致。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
