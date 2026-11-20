"""Jira MCP server: Google SSO -> role from Google group -> per-user Jira (Atlassian OAuth).

Run:  python server.py   (serves MCP over HTTP at http://localhost:8000/mcp)
"""
import html
import os
import re

from dotenv import load_dotenv

load_dotenv()

from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from fastmcp.server.auth.providers.google import GoogleProvider  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import HTMLResponse  # noqa: E402

import atlassian_auth  # noqa: E402
import audit  # noqa: E402
from jira_client import Jira, auth_mode  # noqa: E402
from rbac import GROUP_SCOPE, current_identity, require  # noqa: E402

SCOPES = ["openid",
          "https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/userinfo.profile"]
if os.getenv("ADMIN_GROUP"):
    SCOPES.append(GROUP_SCOPE)  # only ask for group access when roles come from a group

auth = GoogleProvider(
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    base_url=os.getenv("SERVER_BASE_URL", "http://localhost:8000"),
    required_scopes=SCOPES,
    **({"jwt_signing_key": os.environ["JWT_SIGNING_KEY"]} if os.getenv("JWT_SIGNING_KEY") else {}),
)

mcp = FastMCP("Jira SSO Server", auth=auth)


async def _email() -> str:
    return (await current_identity())["email"]


jira = Jira(email=_email)

PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def _key(value: str, issue: bool = False) -> str:
    """Validate/normalise keys so user input can't inject into JQL or URLs."""
    value = value.strip().upper()
    if not (ISSUE_RE if issue else PROJECT_RE).match(value):
        raise ToolError(f"Invalid {'issue' if issue else 'project'} key: {value!r}")
    return value


# ---- identity & Jira connection -------------------------------------------------------

@mcp.tool
async def whoami() -> dict:
    """Show the signed-in Google identity, its role and its Jira connection."""
    who = await current_identity()
    return {**who, "jira": await jira.connection()}


@mcp.tool
@require("jira:connect")
async def connect_jira() -> dict:
    """Start connecting your own Jira account. Returns a URL to open in a browser."""
    who = await current_identity()
    if auth_mode() == "service":
        return {"mode": "service", "connected": True,
                "message": "This server uses a shared Jira service account; nothing to connect."}
    state = atlassian_auth.status(who["email"])
    if state["connected"]:
        return {"mode": "user", **state}
    try:
        return {"mode": "user", "connected": False,
                "url": atlassian_auth.authorize_url(who["email"])}
    except atlassian_auth.AuthError as e:
        raise ToolError(str(e)) from None


@mcp.tool
@require("jira:connect")
async def disconnect_jira() -> dict:
    """Forget your stored Jira tokens on this server."""
    who = await current_identity()
    removed = atlassian_auth.disconnect(who["email"])
    audit.record("jira_disconnected", user=who["email"], had_connection=removed)
    return {"disconnected": removed}


@mcp.custom_route("/jira/callback", methods=["GET"])
async def jira_callback(request: Request) -> HTMLResponse:
    """Atlassian redirects the user's browser here after consent."""
    q = request.query_params
    try:
        if q.get("error"):
            raise atlassian_auth.AuthError(q.get("error_description") or q["error"])
        if not q.get("code") or not q.get("state"):
            raise atlassian_auth.AuthError("Missing code or state.")
        email = await atlassian_auth.handle_callback(q["code"], q["state"])
    except atlassian_auth.AuthError as e:
        audit.record("jira_connect_failed", error=str(e)[:200])
        return HTMLResponse(_callback_page(False, str(e)), status_code=400)
    audit.record("jira_connected", user=email)
    return HTMLResponse(_callback_page(True, email))


def _callback_page(ok: bool, detail: str) -> str:
    back = html.escape(os.getenv("WEBAPP_URL", "http://localhost:3000"))
    title = "Jira connected" if ok else "Could not connect Jira"
    body = (f"Signed in as <b>{html.escape(detail)}</b>. Returning to the app..."
            if ok else html.escape(detail))
    redirect = f'<meta http-equiv="refresh" content="2;url={back}">' if ok else ""
    return f"""<!doctype html><meta charset="utf-8"><title>{title}</title>{redirect}
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:16px system-ui;display:grid;place-items:center;min-height:100vh;margin:0;
background:#f6f7f9;color:#1b1f24}}@media(prefers-color-scheme:dark){{body{{background:#0f1115;color:#e8eaee}}}}
.c{{max-width:420px;padding:32px;text-align:center}}h1{{font-size:20px}}a{{color:#2563eb}}</style>
<div class="c"><h1>{'&#10003; ' if ok else '&#9888; '}{title}</h1><p>{body}</p>
<p><a href="{back}">Back to the app</a></p></div>"""


# ---- Jira tools ---------------------------------------------------------------------------

@mcp.tool
@require("projects:read")
async def list_projects() -> list[dict]:
    """List Jira projects. (user, admin)"""
    return await jira.projects()


@mcp.tool
@require("issues:read")
async def list_issues(project: str, status: str = "", limit: int = 20) -> list[dict]:
    """List issues in a project, optionally filtered by status name. (user, admin)"""
    jql = f'project = "{_key(project)}"'
    if status:
        jql += ' AND status = "' + status.replace('"', "").replace("\\", "") + '"'
    return await jira.search(jql + " ORDER BY created DESC", max(1, min(limit, 50)))


@mcp.tool
@require("issues:read")
async def get_issue(issue_key: str) -> dict:
    """Get full details of one issue, e.g. PROJ-12. (user, admin)"""
    return await jira.issue(_key(issue_key, issue=True))


@mcp.tool
@require("issues:read")
async def get_transitions(issue_key: str) -> list[dict]:
    """List the status transitions currently available for an issue. (user, admin)"""
    return await jira.transitions(_key(issue_key, issue=True))


@mcp.tool
@require("issues:create")
async def create_task(project: str, summary: str, description: str = "",
                      issue_type: str = "Task") -> dict:
    """Create a Jira issue. (admin only)"""
    return await jira.create(_key(project), summary, description, issue_type)


@mcp.tool
@require("issues:update")
async def update_issue(issue_key: str, summary: str = "", description: str = "",
                       status: str = "") -> dict:
    """Update summary/description and/or transition status of an issue. (admin only)"""
    key = _key(issue_key, issue=True)
    result = await jira.update(key, summary, description)
    if status:
        result["status"] = (await jira.transition(key, status))["status"]
    return result


if __name__ == "__main__":
    mcp.run(transport="http", host="localhost", port=8000)
