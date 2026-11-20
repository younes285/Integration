"""Role-based access control derived from the verified Google identity.

Role resolution (first match wins):
  1. member of the Google Workspace group in ADMIN_GROUP  -> admin   (identity provider decides)
  2. email listed in ADMIN_EMAILS                          -> admin   (static fallback)
  3. anyone else                                           -> user
Every permission decision is written to the audit log.
"""
import os
import time
from functools import wraps

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

import audit

USER, ADMIN = "user", "admin"

PERMISSIONS = {
    USER: {"projects:read", "issues:read", "jira:connect"},
    ADMIN: {"projects:read", "issues:read", "jira:connect", "issues:create", "issues:update"},
}

GROUP_SCOPE = "https://www.googleapis.com/auth/cloud-identity.groups.readonly"
_CI = "https://cloudidentity.googleapis.com/v1"
_transport: httpx.AsyncBaseTransport | None = None  # overridden in tests
_group_cache: dict[tuple[str, str], tuple[float, bool]] = {}


def _admins() -> set[str]:
    return {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}


async def _in_admin_group(email: str, google_token: str) -> bool:
    """Ask Google Cloud Identity whether `email` is (transitively) in ADMIN_GROUP.

    Uses the user's own access token, so the user must have consented to GROUP_SCOPE.
    Fails closed: any error means "not in the group".
    """
    group = os.getenv("ADMIN_GROUP", "").strip().lower()
    if not group or "'" in email:
        return False
    key = (group, email)
    hit = _group_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    member = False
    ttl = int(os.getenv("GROUP_CACHE_SECONDS", "300"))
    try:
        async with httpx.AsyncClient(timeout=10, transport=_transport,
                                     headers={"Authorization": f"Bearer {google_token}"}) as c:
            r = await c.get(f"{_CI}/groups:lookup", params={"groupKey.id": group})
            r.raise_for_status()
            r = await c.get(f"{_CI}/{r.json()['name']}/memberships:checkTransitiveMembership",
                            params={"query": f"member_key_id=='{email}'"})
            r.raise_for_status()
            member = bool(r.json().get("hasMembership"))
    except (httpx.HTTPError, KeyError, ValueError) as e:
        audit.record("group_lookup_failed", user=email, group=group, error=type(e).__name__)
        ttl = 30  # retry soon, but don't hammer Google
    _group_cache[key] = (time.time() + ttl, member)
    return member


async def current_identity() -> dict:
    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required.")
    claims = token.claims or {}
    email = (claims.get("email") or "").lower()
    if not email or claims.get("email_verified") is False:
        raise ToolError("Google account has no verified email.")
    domain = os.getenv("ALLOWED_DOMAIN", "").strip().lower()
    if domain and not email.endswith("@" + domain):
        raise ToolError(f"Accounts from {email.split('@')[-1]} are not allowed.")

    if await _in_admin_group(email, token.token):
        role, source = ADMIN, "google_group"
    elif email in _admins():
        role, source = ADMIN, "admin_emails"
    else:
        role, source = USER, "default"
    return {"email": email, "name": claims.get("name") or email,
            "picture": claims.get("picture"), "role": role, "role_source": source}


def require(permission: str):
    """Decorator: enforce that the caller's role grants `permission`, and audit the decision."""
    def deco(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            who = await current_identity()
            allowed = permission in PERMISSIONS[who["role"]]
            audit.record("tool_call", tool=fn.__name__, user=who["email"], role=who["role"],
                         permission=permission, allowed=allowed, args=audit.clip(kwargs))
            if not allowed:
                raise ToolError(
                    f"Forbidden: role '{who['role']}' ({who['email']}) lacks '{permission}'.")
            return await fn(*args, **kwargs)
        return wrapper
    return deco
