import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, Response
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

SESSION_COOKIE = "app_session"
CSRF_COOKIE = "app_csrf"
CSRF_LIFETIME = 3600


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_google_token(credential: str, client_id: str) -> dict:
    try:
        claims = id_token.verify_oauth2_token(credential, GoogleRequest(), client_id)
        # The library verifies signature, audience, time and issuer. Explicit claim
        # checks document our application's identity requirements as well.
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("issuer")
        if claims.get("aud") != client_id or claims.get("exp", 0) <= time.time():
            raise ValueError("audience or expiry")
        sub, email = claims.get("sub"), claims.get("email")
        if not isinstance(sub, str) or not sub.strip() or len(sub) > 255:
            raise ValueError("subject")
        if claims.get("email_verified") is not True or not isinstance(email, str) or not email:
            raise ValueError("verified email")
        return claims
    except (ValueError, TypeError, GoogleAuthError):
        # Never return credential contents or Google's detailed errors to the client.
        raise HTTPException(401, "Invalid or expired Google credential") from None


def set_cookie(response: Response, name: str, value: str, settings, max_age: int):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=settings.session_cookie_secure, samesite="lax", path="/")


def csrf_signature(payload: str, binding: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{payload}|{binding}".encode(), hashlib.sha256).hexdigest()


def csrf_binding(request: Request) -> str:
    raw = request.cookies.get(SESSION_COOKIE)
    return token_hash(raw) if raw else "anonymous"


def issue_csrf(request: Request, response: Response, binding: str | None = None) -> str:
    settings = request.app.state.settings
    payload = f"{int(time.time())}.{secrets.token_urlsafe(32)}"
    signature = csrf_signature(payload, binding or csrf_binding(request), settings.csrf_secret_key)
    token = f"{payload}.{signature}"
    set_cookie(response, CSRF_COOKIE, token, settings, CSRF_LIFETIME)
    return token


def require_csrf(request: Request):
    settings = request.app.state.settings
    if request.headers.get("origin") != settings.app_origin:
        raise HTTPException(403, "Untrusted or missing Origin")
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if (not header or len(header) > 256 or not header.isascii() or not cookie.isascii()
            or not hmac.compare_digest(header, cookie)):
        raise HTTPException(403, "Missing or invalid CSRF token")
    try:
        issued, nonce, signature = header.split(".")
        age = time.time() - int(issued)
        expected = csrf_signature(f"{issued}.{nonce}", csrf_binding(request), settings.csrf_secret_key)
        if not nonce or not 0 <= age <= CSRF_LIFETIME or not hmac.compare_digest(signature, expected):
            raise ValueError()
    except ValueError:
        raise HTTPException(403, "Missing or invalid CSRF token") from None
