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


def require_role(*roles: str):
    """角色守卫：要求当前用户属于给定角色之一（admin 恒有权限）。

    用法：`Depends(require_role("reviewer", "admin"))`
    """
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles and current_user.role != ROLE_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return current_user
    return checker
