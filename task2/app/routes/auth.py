import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.database import get_db
from app.dependencies import get_current_session
from app.models import Session, User, utcnow
from app.schemas import GoogleCredential, UserOut
from app.security import (CSRF_COOKIE, SESSION_COOKIE, issue_csrf, require_csrf,
                          set_cookie, token_hash, verify_google_token)

router = APIRouter()


@router.get("/api/csrf")
def csrf(request: Request, response: Response):
    return {"csrf_token": issue_csrf(request, response)}


@router.post("/auth/google", response_model=UserOut, dependencies=[Depends(require_csrf)])
def google_login(body: GoogleCredential, request: Request, response: Response,
                 db: DBSession = Depends(get_db)):
    settings = request.app.state.settings
    claims = verify_google_token(body.credential, settings.google_client_id)
    user = db.scalar(select(User).where(User.google_sub == claims["sub"]))
    if user is None:
        user = User(google_sub=claims["sub"], email=claims["email"], name=claims.get("name") or claims["email"])
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            # Two first-time logins may race; the unique subject still creates one account.
            db.rollback()
            user = db.scalar(select(User).where(User.google_sub == claims["sub"]))
            if user is None:
                raise
    user.email = claims["email"]
    user.name = claims.get("name") or claims["email"]
    picture = claims.get("picture")
    user.picture_url = picture if isinstance(picture, str) and picture.startswith("https://") else None
    user.last_login_at = utcnow()
    old_raw = request.cookies.get(SESSION_COOKIE)
    if old_raw:
        old = db.scalar(select(Session).where(Session.session_token_hash == token_hash(old_raw)))
        if old and old.revoked_at is None:
            old.revoked_at = utcnow()
    raw = secrets.token_urlsafe(32)
    db.add(Session(session_token_hash=token_hash(raw), user_id=user.id,
                   expires_at=utcnow() + timedelta(seconds=settings.session_lifetime_seconds)))
    db.commit()
    set_cookie(response, SESSION_COOKIE, raw, settings, settings.session_lifetime_seconds)
    issue_csrf(request, response, binding=token_hash(raw))
    return user


@router.post("/auth/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, response: Response,
           session: Session = Depends(get_current_session), db: DBSession = Depends(get_db)):
    session.revoked_at = utcnow()
    db.commit()
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", httponly=True, samesite="lax",
                               secure=request.app.state.settings.session_cookie_secure)
    return {"message": "Signed out"}
