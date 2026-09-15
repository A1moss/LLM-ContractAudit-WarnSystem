import os
from pathlib import Path
from dotenv import load_dotenv

# 从 __file__ 定位项目根 .env（config.py → backend/ → 项目根/），避免依赖 cwd
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/contract_audit")
# 未配置 SECRET_KEY 或仍是弱默认时启动失败，避免随机 key 导致 --reload/多 worker 下 JWT 失效（BUG-054）
_SECRET = os.getenv("SECRET_KEY", "").strip()
if not _SECRET or _SECRET == "change-me-to-random-string":
    raise RuntimeError('必须配置 SECRET_KEY：请在 .env 中设置（生成：python -c "import secrets;print(secrets.token_urlsafe(32))"）')
SECRET_KEY = _SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# 允许的前端来源（逗号分隔），避免换端口/部署后 CORS 失效
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    """环境变量布尔解析（1/true/yes/on 视为真；未设置或无法识别取默认值）。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ── Feedback RAG（人工反馈驱动的持续优化）───────────────────────────────
# **默认关闭**。开启后，生产审核链路的 evidence 抽取 prompt 会注入"已人工批准的历史经验"参考块；
# 官方评测路径（evaluate/run_evidence.py → extract_evidence）不读取本开关，prompt 保持逐字节不变，
# 因此官方 F1 / evidence.json / Gold / classification_test / realtest 全部冻结不受影响。
FEEDBACK_RAG_ENABLED = _env_bool("FEEDBACK_RAG_ENABLED", False)
