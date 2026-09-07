import os
import secrets
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/contract_audit")
# 未配置 SECRET_KEY 或仍是弱默认时，生成随机 key，避免线上误用可预测密钥
_SECRET = os.getenv("SECRET_KEY", "").strip()
SECRET_KEY = _SECRET if (_SECRET and _SECRET != "change-me-to-random-string") else secrets.token_urlsafe(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-your-key-here")
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "http://localhost:5001")

# 允许的前端来源（逗号分隔），避免换端口/部署后 CORS 失效
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
