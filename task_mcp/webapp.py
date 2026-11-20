"""Web UI for the Jira MCP server. This app is a real multi-user MCP client.

Each browser gets its own session (HttpOnly cookie) holding its own MCP OAuth tokens, obtained
with the standard flow: dynamic client registration -> authorization code + PKCE -> refresh.
Nothing is shared between visitors, and the sign-in happens in the visitor's own browser.

Run the MCP server first (python server.py), then:  python webapp.py
Open http://localhost:3000
"""
import base64
import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
import uvicorn
from dotenv import load_dotenv
from fastmcp import Client
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

load_dotenv()

MCP_URL = os.getenv("MCP_URL", "http://localhost:8000/mcp")
APP_URL = os.getenv("WEBAPP_URL", "http://localhost:3000").rstrip("/")
REDIRECT_URI = APP_URL + "/auth/callback"
_u = urlparse(MCP_URL)
MCP_ORIGIN = f"{_u.scheme}://{_u.netloc}"
STATIC = Path(__file__).parent / "static"
SESSION_TTL = 12 * 3600
TOOLS = {"list_projects", "list_issues", "get_issue", "get_transitions", "create_task",
         "update_issue", "connect_jira", "disconnect_jira"}


# ---- sessions -------------------------------------------------------------------------

@dataclass
class Session:
    sid: str
    created: float
    verifier: str | None = None     # PKCE verifier for the login in progress
    state: str | None = None
    access: str | None = None       # this visitor's MCP access token
    refresh: str | None = None
    expires_at: float = 0

    def clear_tokens(self):
        self.access = self.refresh = self.verifier = self.state = None
        self.expires_at = 0


SESSIONS: dict[str, Session] = {}


def _new_session() -> Session:
    now = time.time()
    for sid in [k for k, s in SESSIONS.items() if now - s.created > SESSION_TTL]:
        del SESSIONS[sid]
    s = Session(secrets.token_urlsafe(32), now)
    SESSIONS[s.sid] = s
    return s


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # CSRF guard: state-changing requests must come from this app's own origin.
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin \
                and origin.rstrip("/") != APP_URL:
            return JSONResponse({"error": "Cross-origin request blocked."}, status_code=403)
        sid = request.cookies.get("sid")
        s = SESSIONS.get(sid) if sid else None
        if s is None or time.time() - s.created > SESSION_TTL:
            s = _new_session()
        request.state.session = s
        resp = await call_next(request)
        if request.cookies.get("sid") != s.sid:
            resp.set_cookie("sid", s.sid, httponly=True, samesite="lax", max_age=SESSION_TTL,
                            secure=APP_URL.startswith("https"))
        resp.headers["Cache-Control"] = "no-store"
        return resp


# ---- MCP OAuth client (discovery, registration, PKCE, refresh) ---------------------------

_meta: dict | None = None
_client_id: str | None = None


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


async def _discover() -> dict:
    global _meta
    if _meta is None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{MCP_ORIGIN}/.well-known/oauth-authorization-server")
                r.raise_for_status()
            _meta = r.json()
        except httpx.HTTPError:
            raise ApiError(f"Cannot reach the MCP server at {MCP_ORIGIN}. Is server.py running?",
                           502, "mcp_unreachable") from None
    return _meta


async def _register(meta: dict) -> str:
    """Register this web app once as a public OAuth client (RFC 7591)."""
    global _client_id
    if _client_id is None:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(meta["registration_endpoint"], json={
                "client_name": "Jira Console", "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none"})
        if r.status_code >= 400:
            raise ApiError(f"MCP server refused client registration: {r.text[:200]}", 502)
        _client_id = r.json()["client_id"]
    return _client_id


async def _token_request(meta: dict, data: dict) -> dict:
    global _client_id, _meta
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(meta["token_endpoint"], data={**data, "client_id": _client_id,
                                                       "resource": MCP_URL})
    if r.status_code != 200:
        if "invalid_client" in r.text:   # server restarted and forgot us: re-register next time
            _client_id = _meta = None
        raise ApiError(f"Token request failed ({r.status_code}).", 401, "unauthenticated")
    return r.json()


def _store_tokens(s: Session, tok: dict):
    s.access = tok["access_token"]
    s.refresh = tok.get("refresh_token", s.refresh)
    s.expires_at = time.time() + int(tok.get("expires_in", 3600))


async def _fresh_token(s: Session) -> str:
    if not s.access:
        raise ApiError("Not signed in.", 401, "unauthenticated")
    if s.expires_at - 30 < time.time():
        if not s.refresh:
            s.clear_tokens()
            raise ApiError("Session expired. Please sign in again.", 401, "unauthenticated")
        try:
            _store_tokens(s, await _token_request(await _discover(),
                                                  {"grant_type": "refresh_token",
                                                   "refresh_token": s.refresh}))
        except ApiError:
            s.clear_tokens()
            raise
    return s.access


_PREFIX = re.compile(r"^Error calling tool '[^']*':\s*")


async def call(s: Session, tool: str, args: dict | None = None):
    """Call an MCP tool as this session's user and translate failures into ApiError."""
    token = await _fresh_token(s)
    try:
        async with Client(MCP_URL, auth=token) as c:
            return (await c.call_tool(tool, args or {})).data
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001 - classify everything for the UI
        msg = _PREFIX.sub("", str(e)) or type(e).__name__
        if isinstance(e, (httpx.ConnectError, ConnectionError)):
            raise ApiError(f"Cannot reach the MCP server at {MCP_ORIGIN}.", 502,
                           "mcp_unreachable") from None
        if "401" in msg or "Unauthorized" in msg:
            s.clear_tokens()
            raise ApiError("Session expired. Please sign in again.", 401, "unauthenticated") \
                from None
        if "JIRA_NOT_CONNECTED" in msg:
            detail = msg.split(":", 1)[-1].strip()
            raise ApiError(detail[:1].upper() + detail[1:], 409, "jira_not_connected") from None
        if "Forbidden" in msg:
            raise ApiError(msg, 403, "forbidden") from None
        raise ApiError(msg[:500], 400) from None


# ---- routes -----------------------------------------------------------------------------------

def _json_error(e: ApiError):
    return JSONResponse({"error": e.message, "code": e.code}, status_code=e.status)


async def api_login(request: Request):
    s: Session = request.state.session
    try:
        meta = await _discover()
        client_id = await _register(meta)
    except ApiError as e:
        return _json_error(e)
    s.verifier = secrets.token_urlsafe(48)
    s.state = secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(s.verifier.encode()).digest()).rstrip(b"=").decode()
    return JSONResponse({"url": meta["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
        "state": s.state, "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": MCP_URL})})


async def auth_callback(request: Request):
    """The MCP server sends the visitor's browser back here with an authorization code."""
    s: Session = request.state.session
    q = request.query_params

    def fail(msg: str):
        return RedirectResponse("/?" + urlencode({"error": msg[:200]}))

    if q.get("error"):
        return fail(q.get("error_description") or q["error"])
    if not s.state or not secrets.compare_digest(q.get("state", ""), s.state):
        return fail("Sign-in expired or was started in another browser. Try again.")
    try:
        _store_tokens(s, await _token_request(await _discover(), {
            "grant_type": "authorization_code", "code": q.get("code", ""),
            "redirect_uri": REDIRECT_URI, "code_verifier": s.verifier}))
        s.state = s.verifier = None
        await call(s, "whoami")  # fail fast if the account is not allowed
    except ApiError as e:
        s.clear_tokens()
        return fail(e.message)
    return RedirectResponse("/")


async def api_me(request: Request):
    s: Session = request.state.session
    if not s.access:
        return JSONResponse({"signed_in": False})
    try:
        return JSONResponse({"signed_in": True, "me": await call(s, "whoami")})
    except ApiError as e:
        if e.status == 401:
            return JSONResponse({"signed_in": False})
        return _json_error(e)


async def api_logout(request: Request):
    request.state.session.clear_tokens()
    return JSONResponse({"ok": True})


async def api_tool(request: Request):
    tool = request.path_params["tool"]
    if tool not in TOOLS:
        return JSONResponse({"error": "Unknown tool."}, status_code=404)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        return JSONResponse({"result": await call(request.state.session, tool, body)})
    except ApiError as e:
        return _json_error(e)


async def index(request: Request):
    return FileResponse(STATIC / "index.html")


app = Starlette(
    routes=[
        Route("/", index),
        Route("/auth/callback", auth_callback),
        Route("/api/login", api_login, methods=["POST"]),
        Route("/api/logout", api_logout, methods=["POST"]),
        Route("/api/me", api_me),
        Route("/api/tool/{tool}", api_tool, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ],
    middleware=[Middleware(SessionMiddleware)],
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(urlparse(APP_URL).port or 3000))
