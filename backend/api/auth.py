from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from api.deps import ROLE_UPLOADER
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
