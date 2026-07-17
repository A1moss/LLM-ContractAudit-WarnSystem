from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from services.auth import hash_password, verify_password, create_access_token

router = APIRouter(prefix='/auth', tags=['auth'])


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserOut(BaseModel):
    id: str
    username: str
    email: str

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
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({'sub': user.id, 'username': user.username})
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

    token = create_access_token({'sub': user.id, 'username': user.username})
    return AuthResponse(
        data={
            'token': token,
            'user': UserOut.model_validate(user).model_dump(),
        }
    )
