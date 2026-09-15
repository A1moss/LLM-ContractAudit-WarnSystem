from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from api.deps import (
    ROLE_UPLOADER, ROLE_REVIEWER, ROLE_APPROVER, ROLE_ADMIN, require_role,
)
from services.auth import hash_password, verify_password, create_access_token

router = APIRouter(prefix='/auth', tags=['auth'])


class RegisterRequest(BaseModel):
    """注册请求。

    注意：**不接受 role 字段**。角色是权限边界，绝不能由客户端自选——
    否则任何人都能自注册为 admin 从而获得全局合同读取/审核/验收/删除权限。
    自助注册一律固定为 uploader；reviewer/approver/admin 只能由数据库预置或
    管理员在后台分配（本版本尚无用户管理接口，故不在注册路径上放开）。
    客户端即使额外传 role=admin，pydantic 也会忽略该未知字段，最终仍建为 uploader。
    """
    username: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    role: str

    model_config = {'from_attributes': True}


class AuthResponse(BaseModel):
    code: int = 0
    message: str = 'ok'
    data: dict | None = None


@router.post('/register', response_model=AuthResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(
        or_(User.username == body.username, User.email == body.email)
    ).first()
    if existing:
        field = 'username' if existing.username == body.username else 'email'
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{field} already exists",
        )

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        # 硬编码 uploader：不读取任何客户端传入的角色（权限边界，见 RegisterRequest 注释）
        role=ROLE_UPLOADER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({'sub': str(user.id), 'username': user.username, 'role': user.role})
    return AuthResponse(
        data={
            'token': token,
            'user': UserOut.model_validate(user).model_dump(),
        }
    )


@router.post('/login', response_model=AuthResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        or_(User.username == body.username, User.email == body.username)
    ).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='incorrect username/email or password',
        )

    token = create_access_token({'sub': str(user.id), 'username': user.username, 'role': user.role})
    return AuthResponse(
        data={
            'token': token,
            'user': UserOut.model_validate(user).model_dump(),
        }
    )


# ── 用户管理（最小化：仅管理员可「查看用户列表 + 修改角色」）──────────────────
# 权限边界完全在服务端：require_role(ROLE_ADMIN) 按**数据库中的真实角色**判定，
# 与 localStorage / JWT 里客户端可控的 role 无关（get_current_user 每次从库读用户）。
# 角色分配入口只有这里——公开注册永远只能得到 uploader，不存在自助提权路径。

VALID_ROLES = (ROLE_UPLOADER, ROLE_REVIEWER, ROLE_APPROVER, ROLE_ADMIN)


class UserRoleUpdate(BaseModel):
    role: str = Field(..., pattern=r"^(uploader|reviewer|approver|admin)$")


def _user_item(u: User) -> dict:
    return {
        'id': u.id,
        'username': u.username,
        'email': u.email,
        'role': u.role,
        'created_at': u.created_at.isoformat() + 'Z' if u.created_at else None,
    }


@router.get('/users')
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ROLE_ADMIN)),
):
    """用户列表（仅 admin，非 admin 一律 403）。"""
    users = db.query(User).order_by(User.id.asc()).all()
    return {'code': 0, 'message': 'ok',
            'data': {'items': [_user_item(u) for u in users], 'total': len(users)}}


@router.put('/users/{user_id}/role')
def update_user_role(
    user_id: int,
    body: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(ROLE_ADMIN)),
):
    """修改指定用户角色（仅 admin）。

    两条防锁死保护：
      1. 管理员不得修改自己的角色 —— 不能自我降权把自己关在门外；
      2. 任何修改完成后系统必须仍保留 ≥1 个 admin —— 不能降级最后一个管理员。
    允许把他人提升为 admin（可存在多个管理员）。
    """
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='user not found')

    if target.id == current_user.id and body.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='不能修改自己的管理员角色（避免把自己锁在系统外），请由其他管理员操作',
        )

    if target.role == ROLE_ADMIN and body.role != ROLE_ADMIN:
        admin_count = db.query(User).filter(User.role == ROLE_ADMIN).count()
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='系统必须至少保留一名管理员，不能降级最后一个管理员',
            )

    target.role = body.role
    db.commit()
    db.refresh(target)
    return {'code': 0, 'message': 'ok', 'data': _user_item(target)}
