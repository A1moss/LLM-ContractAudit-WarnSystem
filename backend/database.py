from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    pool_recycle=3600,
    pool_pre_ping=True,
    # 仅 SQLite：busy_timeout=30s，让短暂写竞争等待而非默认 5s 后即 database is locked（BUG-009）
    connect_args={"timeout": 30} if _is_sqlite else {},
)

if _is_sqlite:
    # WAL：读写并发改善、减少部分写锁冲突（SQLite 仍是单写者，不根治并发写）（BUG-009）
    @event.listens_for(engine, "connect")
    def _sqlite_wal(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

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
