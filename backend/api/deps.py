from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from services.auth import decode_access_token

security = HTTPBearer(auto_error=False)

# 角色常量（多用户协作流转：上传者 → 审核人 → 验收人）
ROLE_UPLOADER = "uploader"
ROLE_REVIEWER = "reviewer"
ROLE_APPROVER = "approver"
ROLE_ADMIN = "admin"


def get_current_user(
    credentials: HTTPBearer = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_llm_key_configured() -> None:
    """LLM 前置检查：本次请求是否有可用的 DeepSeek Key（个人 Key 或 .env 默认 Key）。

    两者都没有时抛 400 并给出明确指引，避免"看似操作成功、实际全链路降级"的静默失败。
    读取的是中间件已绑定到请求上下文的用户 Key（实测依赖可读到中间件 set 的值）。
    """
    from ai.llm_client import resolve_api_key, NO_KEY_MESSAGE

    key, _source = resolve_api_key()
    if not key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=NO_KEY_MESSAGE)


def require_role(*roles: str):
    """角色守卫：要求当前用户属于给定角色之一（admin 恒有权限）。

    用法：`Depends(require_role("reviewer", "admin"))`
    """
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles and current_user.role != ROLE_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return current_user
    return checker
