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
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-your-key-here")
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "http://localhost:5001")

# 允许的前端来源（逗号分隔），避免换端口/部署后 CORS 失效
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
