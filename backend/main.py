from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from config import CORS_ORIGINS
from api.auth import router as auth_router
from api.contracts import router as contracts_router
from api.feedback import router as feedback_router
from api.templates import router as templates_router
from api.stats import router as stats_router
from ai.taxonomy import to_dict as taxonomy_dict

logger = logging.getLogger(__name__)


def _migrate_fk_column_type(db, table: str, column: str, target: str = "INTEGER"):
    """SQLite 列类型迁移：把 FK 列从历史遗留的 VARCHAR 迁移为 INTEGER。

    背景：早期 Contract/FeedbackLog 的 user_id 定义成 String(36)，SQLite 弱类型
    下不报错，但切 MySQL 时 FK 类型不匹配会建表失败。模型层已改为 Integer，
    此处同步迁移 SQLite 里已存在的旧表，保证表结构与模型一致（幂等）。
    SQLite 不支持 ALTER COLUMN TYPE，采用重建表方式。
    """
    try:
        info = db.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception as e:
        logger.debug("迁移 %s 前读取表结构失败（表可能不存在）: %s", table, e)
        return

    col = next((c for c in info if c[1] == column), None)
    if col is None or (col[2] or "").upper() == target:
        return  # 列不存在或已一致

    names = [c[1] for c in info]
    col_defs = []
    selects = []
    for c in info:
        name, typ, notnull, dflt, pk = c[1], c[2], c[3], c[4], c[5]
        if name == column:
            typ = target
            selects.append(f'CAST("{name}" AS {target})')
        else:
            selects.append(f'"{name}"')
        ddl = f'"{name}" {typ}'
        if pk:
            ddl += " PRIMARY KEY"
        elif notnull:
            ddl += " NOT NULL"
        if dflt is not None:
            ddl += f" DEFAULT {dflt}"
        col_defs.append(ddl)

    tmp = f"{table}__migrate_tmp"
    db.execute(f'DROP TABLE IF EXISTS "{tmp}"')
    db.execute(f'ALTER TABLE "{table}" RENAME TO "{tmp}"')
    db.execute(f'CREATE TABLE "{table}" ({", ".join(col_defs)})')
    db.execute(
        f'INSERT INTO "{table}" ({", ".join(chr(34) + n + chr(34) for n in names)}) '
        f'SELECT {", ".join(selects)} FROM "{tmp}"'
    )
    db.execute(f'DROP TABLE "{tmp}"')
    db.commit()
    logger.warning("已迁移 %s.%s 列类型 → %s（FK 类型对齐模型定义）", table, column, target)


def _ensure_columns():
    """Safe migration: add new columns that may be missing from existing tables."""
    import sqlite3
    try:
        url = str(engine.url)
        if "sqlite" not in url:
            return
        path = url.replace("sqlite:///", "").replace("sqlite://", "")
        db = sqlite3.connect(path)
        existing = {row[1] for row in db.execute("PRAGMA table_info(contracts)")}
        if "stored_path" not in existing:
            db.execute("ALTER TABLE contracts ADD COLUMN stored_path VARCHAR(500)")
            db.commit()
        # audit_records 新增 evidence 列（证据链）
        try:
            existing_ar = {row[1] for row in db.execute("PRAGMA table_info(audit_records)")}
            if existing_ar and "evidence" not in existing_ar:
                db.execute("ALTER TABLE audit_records ADD COLUMN evidence JSON")
                db.commit()
        except Exception as e:
            logger.debug("audit_records 表尚不存在，跳过 evidence 迁移: %s", e)
        # FK 类型对齐：历史遗留 VARCHAR(36) → INTEGER（切 MySQL 前保证一致）
        for table in ("contracts", "feedback_logs"):
            try:
                _migrate_fk_column_type(db, table, "user_id", "INTEGER")
            except Exception as e:
                logger.warning("迁移 %s.user_id 列类型失败: %s", table, e)
        # 旧数据角色 backfill：历史 "user" 统一归为 "uploader"
        try:
            db.execute("UPDATE users SET role='uploader' WHERE role='user'")
            db.commit()
        except Exception as e:
            logger.debug("users 角色 backfill 失败: %s", e)
        db.close()
    except Exception as e:
        logger.warning("列迁移 _ensure_columns 失败（非致命）: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    yield


app = FastAPI(title="A24 合同审核系统", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)


@app.get("/")
def root():
    return {"message": "A24 合同智能审核系统 v0.1.0"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/contract-types")
def contract_types():
    """返回合同分类体系（框架全集 + 已启用 11 类），供前端下拉/标签读取。"""
    return taxonomy_dict()
app.include_router(auth_router, prefix="/api")
app.include_router(contracts_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(stats_router, prefix="/api")
