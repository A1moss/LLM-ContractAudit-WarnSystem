"""services.user_secret — 用户个人 DeepSeek Key 的**加密落库**存取（任何接口都不得回显明文）。

加密方案：`cryptography` 的 **Fernet**（AES-128-CBC + HMAC-SHA256，AEAD 完整性校验）。
密钥来源：由 `.env` 的 `SECRET_KEY` 经 SHA-256 派生 —— 复用项目既有的部署密钥，
         不额外引入新的密钥管理设施、不新增必须配置项。

方案取舍（为什么是「加密」而不是明文、也不是 KMS）
--------------------------------------------------
* 采用加密的理由：数据库文件/备份里不是明文 Key。即使库被拷走（学生项目常见：db 文件随仓库或
  演示机传播），也无法直接读出可用的 Key；`SECRET_KEY` 不在库里。
* 局限（如实说明）：安全性等价于 `SECRET_KEY` 的保管强度 —— 拿到 `SECRET_KEY` 即可解密，
  与 JWT 签名的信任级别相同。轮换 `SECRET_KEY` 会导致已存 Key 解不开。
* 未采用 KMS/HSM/按用户独立数据密钥：本项目没有相应基础设施，属于过度设计；
  当前方案已避免「明文落库」这一最主要风险。
* 解密失败（如 `SECRET_KEY` 变更）按「未配置个人 Key」处理并告警 → 自动回退系统默认 Key，
  不会让服务或用户请求崩溃。

注意：本模块的函数**只返回明文给后端内部使用**，任何 API 响应只能暴露
`deepseek_key_configured: true/false`，绝不返回 Key 本身或其片段。
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from config import SECRET_KEY
from models.user import User

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    """由 SECRET_KEY 派生 Fernet 密钥（32 字节 → urlsafe base64）。"""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()))


def encrypt_api_key(raw: str) -> str:
    """明文 Key → 密文（存库用）。"""
    return _fernet().encrypt(raw.strip().encode("utf-8")).decode("ascii")


def decrypt_api_key(enc: str | None) -> str | None:
    """密文 → 明文；密文为空或解不开（SECRET_KEY 变过）返回 None 并告警。"""
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as e:
        logger.warning("用户自定义 DeepSeek Key 解密失败（SECRET_KEY 可能已变更），按未配置处理: %s", type(e).__name__)
        return None


def get_user_api_key(db: Session, user_id: int) -> str | None:
    """读取某用户已保存的个人 Key 明文；未配置/不可用返回 None。仅供后端内部使用。"""
    if not user_id:
        return None
    row = db.query(User.deepseek_api_key_enc).filter(User.id == user_id).first()
    if not row:
        return None
    return decrypt_api_key(row[0])


def has_user_api_key(db: Session, user_id: int) -> bool:
    """该用户是否已配置个人 Key（只回布尔值，供 API 脱敏展示）。"""
    if not user_id:
        return False
    row = db.query(User.deepseek_api_key_enc).filter(User.id == user_id).first()
    return bool(row and row[0])


def set_user_api_key(db: Session, user_id: int, raw: str) -> None:
    """保存/覆盖某用户的个人 Key（加密后落库）。"""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return
    user.deepseek_api_key_enc = encrypt_api_key(raw)
    db.commit()


def clear_user_api_key(db: Session, user_id: int) -> None:
    """删除某用户的个人 Key（之后自动回退 .env 系统默认 Key）。"""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return
    user.deepseek_api_key_enc = None
    db.commit()
