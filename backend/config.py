import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/contract_audit")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-to-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-your-key-here")
DIFY_BASE_URL = os.getenv("DIFY_BASE_URL", "http://localhost:5001")
