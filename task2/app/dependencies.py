from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.database import get_db
from app.models import Session, User, utcnow
from app.security import SESSION_COOKIE, token_hash


def get_current_session(request: Request, db: DBSession = Depends(get_db)) -> Session:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw or len(raw) > 256:
        raise HTTPException(401, "Sign in required; session missing or expired")
    session = db.scalar(select(Session).where(
        Session.session_token_hash == token_hash(raw),
        Session.revoked_at.is_(None), Session.expires_at > utcnow()))
    if session is None:
        raise HTTPException(401, "Sign in required; session missing or expired")
    return session


def get_current_user(session: Session = Depends(get_current_session),
                     db: DBSession = Depends(get_db)) -> User:
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Account no longer exists")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Administrator role required")
    return user
