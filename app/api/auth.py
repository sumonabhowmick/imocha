from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import _bearer_token, client_ip, get_current_admin
from app.models import User
from app.schemas.auth import AdminOut, LoginRequest, LoginResponse
from app.schemas.common import Message
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user, token, expires_at = auth_service.authenticate_admin(db, body.email, body.password, client_ip(request))
    return LoginResponse(access_token=token, expires_at=expires_at, admin=AdminOut.model_validate(user))


@router.post("/logout", response_model=Message)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    auth_service.logout(db, _bearer_token(authorization), "admin")
    return Message(message="Logged out.")


@router.get("/me", response_model=AdminOut)
def me(admin: User = Depends(get_current_admin)):
    return admin
