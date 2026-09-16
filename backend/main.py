from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine, SessionLocal
from sqlalchemy import text
from config import CORS_ORIGINS
from api.auth import router as auth_router
from api.contracts import router as contracts_router
from api.overview import router as overview_router
from api.feedback import router as feedback_router
from api.templates import router as templates_router
from api.stats import router as stats_router
from ai.taxonomy import to_dict as taxonomy_dict
from services import warmup as warmup_service
from services import role_bootstrap
from services import user_secret
from services.auth import decode_access_token
from ai.llm_context import set_user_api_key, reset_user_api_key

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
        if "is_outsourcing" not in existing:
            db.execute("ALTER TABLE contracts ADD COLUMN is_outsourcing BOOLEAN DEFAULT 0")
            db.commit()
        # audit_records 新增 evidence 列（证据链）
        try:
            existing_ar = {row[1] for row in db.execute("PRAGMA table_info(audit_records)")}
            if existing_ar and "evidence" not in existing_ar:
                db.execute("ALTER TABLE audit_records ADD COLUMN evidence JSON")
                db.commit()
            if existing_ar and "recommendation" not in existing_ar:
                db.execute("ALTER TABLE audit_records ADD COLUMN recommendation JSON")
                db.commit()
            if existing_ar and "result_status" not in existing_ar:
                db.execute("ALTER TABLE audit_records ADD COLUMN result_status VARCHAR(20) DEFAULT 'valid'")
                db.commit()
            # Feedback RAG 留痕：本次审核实际使用了哪些经验/哪版经验库。
            # 独立新列，绝不复用 corex_agent_log（Corex 已归档，不得恢复其语义）。
            if existing_ar and "learning_context" not in existing_ar:
                db.execute("ALTER TABLE audit_records ADD COLUMN learning_context JSON")
                db.commit()
        except Exception as e:
            logger.debug("audit_records 表尚不存在，跳过 evidence 迁移: %s", e)
        # clause_revisions 新增列（DOCX 导出替换锚点 + 新增条款操作/位置 + 采用态 adopted）
        try:
            existing_cr = {row[1] for row in db.execute("PRAGMA table_info(clause_revisions)")}
            if existing_cr and "original_clause_text" not in existing_cr:
                db.execute("ALTER TABLE clause_revisions ADD COLUMN original_clause_text TEXT")
                db.commit()
            if existing_cr and "operation" not in existing_cr:
                db.execute("ALTER TABLE clause_revisions ADD COLUMN operation VARCHAR(20) DEFAULT 'replace'")
                db.commit()
            if existing_cr and "position" not in existing_cr:
                db.execute("ALTER TABLE clause_revisions ADD COLUMN position JSON")
                db.commit()
            # 采用态：历史行由 DEFAULT 0 回填，等价于「未采用」，因此旧数据行为完全不变
            if existing_cr and "adopted" not in existing_cr:
                db.execute("ALTER TABLE clause_revisions ADD COLUMN adopted BOOLEAN NOT NULL DEFAULT 0")
                db.commit()
            # (contract_id, clause_key) 复合索引：采用确认与 DOCX 归并都按这两个字段过滤
            if existing_cr:
                db.execute(
                    "CREATE INDEX IF NOT EXISTS ix_clause_revisions_contract_key "
                    "ON clause_revisions (contract_id, clause_key)"
                )
                db.commit()
        except Exception as e:
            logger.debug("clause_revisions 表尚不存在，跳过迁移: %s", e)
        # revision_proposals 表：总体修改会话的「综合修改方案」（方案≠合同修改，不参与 DOCX 导出）。
        # 新表由 Base.metadata.create_all 建立；这里只对「旧库 + 极端情况下 create_all 未生效」做幂等兜底。
        try:
            existing_rp = {row[1] for row in db.execute("PRAGMA table_info(revision_proposals)")}
            if not existing_rp:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS revision_proposals ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "contract_id INTEGER NOT NULL,"
                    "user_id INTEGER,"
                    "instruction TEXT NOT NULL DEFAULT '',"
                    "status VARCHAR(20) NOT NULL DEFAULT 'draft',"
                    "summary TEXT,"
                    "items JSON NOT NULL DEFAULT '[]',"
                    "confirmed_ids JSON NOT NULL DEFAULT '[]',"
                    "created_at DATETIME)"
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS ix_revision_proposals_contract_id "
                    "ON revision_proposals (contract_id)"
                )
                db.commit()
        except Exception as e:
            logger.debug("revision_proposals 表兜底创建失败（非致命）: %s", e)
        # feedback_logs 扩列（反馈池 → 学习层；只在旧表缺列时补齐，不动已有数据）
        try:
            existing_fb = {row[1] for row in db.execute("PRAGMA table_info(feedback_logs)")}
            _fb_cols = (
                ("contract_id", "INTEGER"),
                ("contract_type", "VARCHAR(50)"),
                ("risk_type", "VARCHAR(10)"),
                ("feedback_reason", "VARCHAR(30)"),
                ("status", "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
                ("target_type", "VARCHAR(20) NOT NULL DEFAULT 'risk'"),
                ("revision_id", "INTEGER"),
                ("target_ref", "JSON"),
                ("reviewed_by", "INTEGER"),
                ("reviewed_at", "DATETIME"),
                ("review_comment", "TEXT"),
                ("approved_by", "INTEGER"),
                ("approved_at", "DATETIME"),
                ("model_evidence", "JSON"),
                ("model_result", "JSON"),
            )
            for _col, _ddl in _fb_cols:
                if existing_fb and _col not in existing_fb:
                    db.execute(f"ALTER TABLE feedback_logs ADD COLUMN {_col} {_ddl}")
                    db.commit()
            # 历史行 status 兜底（旧表新增列时由 DEFAULT 覆盖；此处仅防御性补齐 NULL）
            if existing_fb and "status" not in existing_fb:
                db.execute("UPDATE feedback_logs SET status='pending' WHERE status IS NULL")
                db.commit()
        except Exception as e:
            logger.debug("feedback_logs 表尚不存在，跳过迁移: %s", e)
        # FK 类型对齐：历史遗留 VARCHAR(36) → INTEGER（切 MySQL 前保证一致）
        for table in ("contracts", "feedback_logs"):
            try:
                _migrate_fk_column_type(db, table, "user_id", "INTEGER")
            except Exception as e:
                logger.warning("迁移 %s.user_id 列类型失败: %s", table, e)
        # users 新增列：用户个人 DeepSeek Key 的加密密文（C-008）
        try:
            existing_u = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            if existing_u and "deepseek_api_key_enc" not in existing_u:
                db.execute("ALTER TABLE users ADD COLUMN deepseek_api_key_enc TEXT")
                db.commit()
        except Exception as e:
            logger.debug("users 表尚不存在，跳过个人 Key 列迁移: %s", e)
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
    # 启动时复位遗留的 auditing 状态：BackgroundTasks 不持久化，进程重启后任务蒸发，
    # 若不复位，合同会永久卡在"审核中"且无法再次触发（BUG-011）。
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE contracts SET status='parsed' WHERE status='auditing'"))
    except Exception as e:
        logger.warning("启动复位 auditing 状态失败: %s", e)
    # 一次性部署初始化：仅当库中**完全没有 admin** 时，把 BOOTSTRAP_ADMIN_USERNAME
    # 指定的**已存在账号**提升为 admin（绝不自动建号；已有 admin 则完全不动）。
    # 用于在「公开注册只能得到 uploader」之后，安全地产生第一个管理员（详见服务模块）。
    try:
        with SessionLocal() as db:
            role_bootstrap.bootstrap_admin(db)
    except Exception as e:
        logger.warning("bootstrap admin 失败（非致命，不阻断启动）: %s", e)
    # 静启动：后台线程预热 torch/向量模型/向量库，启动立即就绪（不阻塞、用户无感知），
    # 避免懒加载把冷启动成本推到「第一次上传合同」上（详见 services/warmup.py）。
    warmup_service.start_warmup()
    yield


app = FastAPI(title="A24 合同审核系统", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)


@app.middleware("http")
async def bind_user_llm_key(request: Request, call_next):
    """把当前登录用户的个人 DeepSeek Key 绑定到本次请求上下文（**个人 Key > .env 默认 Key**）。

    * 无 Authorization 头 / 该用户没配个人 Key → 不绑定，LLM 层自动回退 .env 系统默认 Key
    * 绑定必须放在**中间件**：实测「在依赖里 set」无效（依赖运行在线程池线程，改不到端点上下文），
      而中间件里 set 之后，同步端点 / 依赖 / 后台任务（BackgroundTasks）都能读到
    * 代价：每个带 Authorization 的请求多一次按主键的用户查询（可忽略）
    * 任何异常都不得影响请求本身，统一降级为「使用 .env 默认 Key」
    """
    token = None
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        try:
            payload = decode_access_token(auth_header.split(" ", 1)[1].strip())
            uid = int(payload["sub"]) if payload and payload.get("sub") else None
            if uid:
                with SessionLocal() as db:
                    user_key = user_secret.get_user_api_key(db, uid)
                token = set_user_api_key(user_key)
        except Exception as e:
            # 只记异常类型，绝不输出 token / key 内容
            logger.debug("绑定用户 LLM Key 失败（回退 .env 默认 Key）: %s", type(e).__name__)
    try:
        return await call_next(request)
    finally:
        if token is not None:
            reset_user_api_key(token)


@app.get("/")
def root():
    return {"message": "A24 合同智能审核系统 v0.1.0"}


@app.get("/api/health")
def health():
    """健康检查。warmup 为静启动进度（被动可查：warming/ready/failed），不影响接口可用性。"""
    return {"status": "ok", "warmup": warmup_service.status()}


@app.get("/api/contract-types")
def contract_types():
    """返回合同分类体系（框架全集 + 已启用 11 类），供前端下拉/标签读取。"""
    return taxonomy_dict()
app.include_router(auth_router, prefix="/api")
app.include_router(contracts_router, prefix="/api")
app.include_router(overview_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(stats_router, prefix="/api")
