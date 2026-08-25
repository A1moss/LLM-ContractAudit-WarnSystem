from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from config import CORS_ORIGINS
from api.auth import router as auth_router
from api.contracts import router as contracts_router
from api.feedback import router as feedback_router
from api.templates import router as templates_router
from api.stats import router as stats_router


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
        except Exception:
            pass  # 表可能尚不存在
        # 旧数据角色 backfill：历史 "user" 统一归为 "uploader"
        try:
            db.execute("UPDATE users SET role='uploader' WHERE role='user'")
            db.commit()
        except Exception:
            pass
        db.close()
    except Exception:
        pass  # non-fatal: table may not exist yet


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
app.include_router(auth_router, prefix="/api")
app.include_router(contracts_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(stats_router, prefix="/api")
