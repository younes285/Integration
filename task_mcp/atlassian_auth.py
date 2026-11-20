"""Per-user Atlassian OAuth 2.0 (3LO), so Jira sees the real signed-in user.

Flow:  connect_jira tool -> authorize_url(email) -> user consents at Atlassian ->
       /jira/callback -> handle_callback() stores that user's tokens (encrypted) ->
       access(email) returns a fresh access token for every Jira call, refreshing as needed.

The `state` parameter is a Fernet token (authenticated + timestamped) that binds the
Atlassian consent to the Google identity that asked for it.
"""
import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
API_BASE = "https://api.atlassian.com/ex/jira"
SCOPES = "read:jira-work write:jira-work read:jira-user offline_access"
STATE_TTL = 600  # seconds the user has to finish the Atlassian consent screen

_transport: httpx.AsyncBaseTransport | None = None  # overridden in tests
_locks: dict[str, asyncio.Lock] = {}
_used_nonces: set[str] = set()


class AuthError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotConnected(Exception):
    """The user has not connected (or must reconnect) their Atlassian account."""


# ---- configuration ----------------------------------------------------------------

def configured() -> bool:
    return bool(os.getenv("ATLASSIAN_CLIENT_ID") and os.getenv("ATLASSIAN_CLIENT_SECRET"))


def redirect_uri() -> str:
    return os.getenv("SERVER_BASE_URL", "http://localhost:8000").rstrip("/") + "/jira/callback"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, transport=_transport)


def _fernet() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if not key:
        path = Path(os.getenv("TOKEN_KEY_FILE", ".token_key"))
        if not path.exists():
            path.write_text(Fernet.generate_key().decode())
            try:
                path.chmod(0o600)
            except OSError:
                pass
        key = path.read_text().strip()
    return Fernet(key.encode())


# ---- encrypted per-user token store -----------------------------------------------

def _store_path() -> Path:
    return Path(os.getenv("JIRA_TOKEN_FILE", ".jira_tokens.enc"))


def _load() -> dict:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(path.read_bytes()))
    except (InvalidToken, ValueError):
        return {}


def _save_all(data: dict) -> None:
    path = _store_path()
    path.write_bytes(_fernet().encrypt(json.dumps(data).encode()))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _put(email: str, record: dict | None) -> None:
    data = _load()
    if record is None:
        data.pop(email, None)
    else:
        data[email] = record
    _save_all(data)


# ---- authorize / state --------------------------------------------------------------

def authorize_url(email: str) -> str:
    if not configured():
        raise AuthError("Atlassian OAuth is not configured (ATLASSIAN_CLIENT_ID/SECRET).")
    state = _fernet().encrypt(json.dumps(
        {"email": email.lower(), "nonce": secrets.token_urlsafe(12)}).encode()).decode()
    return AUTH_URL + "?" + urlencode({
        "audience": "api.atlassian.com",
        "client_id": os.environ["ATLASSIAN_CLIENT_ID"],
        "scope": SCOPES,
        "redirect_uri": redirect_uri(),
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    })


def _read_state(state: str) -> dict:
    try:
        payload = json.loads(_fernet().decrypt(state.encode(), ttl=STATE_TTL))
    except (InvalidToken, ValueError):
        raise AuthError("Invalid or expired sign-in link. Start the connection again.") from None
    if payload["nonce"] in _used_nonces:
        raise AuthError("This sign-in link was already used. Start the connection again.")
    _used_nonces.add(payload["nonce"])
    return payload


# ---- token endpoint -------------------------------------------------------------------

async def _token_request(body: dict) -> dict:
    body = {**body, "client_id": os.environ["ATLASSIAN_CLIENT_ID"],
            "client_secret": os.environ["ATLASSIAN_CLIENT_SECRET"]}
    try:
        async with _client() as c:
            r = await c.post(TOKEN_URL, json=body)
    except httpx.HTTPError as e:
        raise AuthError(f"Could not reach Atlassian: {type(e).__name__}") from e
    if r.status_code != 200:
        raise AuthError(f"Atlassian token error {r.status_code}: {r.text[:200]}", r.status_code)
    return r.json()


async def _pick_site(access_token: str) -> dict:
    async with _client() as c:
        r = await c.get(RESOURCES_URL, headers={"Authorization": f"Bearer {access_token}"})
    if r.status_code != 200 or not r.json():
        raise AuthError("This Atlassian account has no accessible Jira sites.")
    sites = r.json()
    want = os.getenv("JIRA_BASE_URL", "").rstrip("/").lower()
    return next((s for s in sites if s["url"].rstrip("/").lower() == want), sites[0])


async def _account_name(cloud_id: str, access_token: str) -> str | None:
    try:
        async with _client() as c:
            r = await c.get(f"{API_BASE}/{cloud_id}/rest/api/3/myself",
                            headers={"Authorization": f"Bearer {access_token}"})
        return r.json().get("displayName") if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def handle_callback(code: str, state: str) -> str:
    """Finish the consent flow. Returns the Google email the tokens now belong to."""
    email = _read_state(state)["email"]
    tok = await _token_request({"grant_type": "authorization_code", "code": code,
                                "redirect_uri": redirect_uri()})
    site = await _pick_site(tok["access_token"])
    _put(email, {
        "access": tok["access_token"],
        "refresh": tok.get("refresh_token"),
        "expires_at": time.time() + int(tok.get("expires_in", 3600)),
        "cloud_id": site["id"],
        "site_url": site["url"].rstrip("/"),
        "site_name": site.get("name"),
        "account": await _account_name(site["id"], tok["access_token"]),
        "connected_at": int(time.time()),
    })
    return email


# ---- using the tokens -----------------------------------------------------------------

def _lock(email: str) -> asyncio.Lock:
    return _locks.setdefault(email, asyncio.Lock())


async def access(email: str) -> dict:
    """Return the user's connection record with a non-expired access token."""
    email = email.lower()
    async with _lock(email):
        rec = _load().get(email)
        if not rec:
            raise NotConnected("not connected")
        if rec["expires_at"] - 60 > time.time():
            return rec
        if not rec.get("refresh"):
            _put(email, None)
            raise NotConnected("session expired")
        try:
            tok = await _token_request({"grant_type": "refresh_token",
                                        "refresh_token": rec["refresh"]})
        except AuthError as e:
            if e.status in (400, 401, 403):  # refresh token revoked/expired -> reconnect
                _put(email, None)
                raise NotConnected("session expired") from e
            raise
        rec.update(access=tok["access_token"],
                   refresh=tok.get("refresh_token", rec["refresh"]),  # Atlassian rotates it
                   expires_at=time.time() + int(tok.get("expires_in", 3600)))
        _put(email, rec)
        return rec


def status(email: str) -> dict:
    rec = _load().get(email.lower())
    if not rec:
        return {"connected": False}
    return {"connected": True, "site": rec["site_url"], "site_name": rec.get("site_name"),
            "account": rec.get("account"), "connected_at": rec.get("connected_at")}


def disconnect(email: str) -> bool:
    had = email.lower() in _load()
    _put(email.lower(), None)
    return had
