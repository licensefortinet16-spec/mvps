from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, UserRole


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    user = db.get(User, user_id)
    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def get_current_client(user: User = Depends(get_current_user)) -> User:
    if user.role == UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin"})
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/"})
    return user
