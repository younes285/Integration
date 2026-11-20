"""Async client for the Jira Cloud REST API v3.

Two auth modes (JIRA_AUTH_MODE=user|service, default: user when Atlassian OAuth is configured):
  user    - each request carries the signed-in user's own Atlassian OAuth token, so Jira
            applies that user's real permissions and attributes changes to them.
  service - one shared API-token account (legacy fallback).
"""
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx
from fastmcp.exceptions import ToolError

import atlassian_auth

NOT_CONNECTED = "JIRA_NOT_CONNECTED"  # stable prefix the UI keys off


def _adf(text: str) -> dict:
    """Wrap plain text in Atlassian Document Format."""
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def _flatten(node) -> str:
    if not node or not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    return " ".join(_flatten(c) for c in node.get("content", [])).strip()


def auth_mode() -> str:
    mode = os.getenv("JIRA_AUTH_MODE", "").strip().lower()
    if mode in ("user", "service"):
        return mode
    return "user" if atlassian_auth.configured() else "service"


@dataclass
class Conn:
    api: str                  # base URL for REST calls
    site: str                 # human-facing site URL (for /browse links)
    headers: dict = field(default_factory=dict)
    auth: tuple | None = None


class Jira:
    def __init__(self, email: Callable[[], Awaitable[str]] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._email = email          # async callable -> signed-in user's email
        self._transport = transport  # for tests

    # -- connection -----------------------------------------------------------------
    async def _conn(self) -> Conn:
        if auth_mode() == "service":
            try:
                base = os.environ["JIRA_BASE_URL"].rstrip("/")
                auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
            except KeyError as e:
                raise ToolError(f"Service-account mode needs {e.args[0]} in .env.") from None
            return Conn(api=base, site=base, auth=auth)
        if self._email is None:
            raise ToolError("No signed-in user.")
        try:
            rec = await atlassian_auth.access(await self._email())
        except atlassian_auth.NotConnected:
            raise ToolError(f"{NOT_CONNECTED}: connect your Jira account first "
                            "(connect_jira tool, or the Connect Jira button).") from None
        except atlassian_auth.AuthError as e:
            raise ToolError(f"Atlassian authorization failed: {e}") from None
        return Conn(api=f"{atlassian_auth.API_BASE}/{rec['cloud_id']}", site=rec["site_url"],
                    headers={"Authorization": f"Bearer {rec['access']}"})

    async def connection(self) -> dict:
        """Connection status for the signed-in user (used by whoami / the UI)."""
        if auth_mode() == "service":
            ok = all(os.getenv(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
            return {"mode": "service", "connected": ok, "site": os.getenv("JIRA_BASE_URL", "")}
        return {"mode": "user", **atlassian_auth.status(await self._email())}

    async def _req(self, method: str, path: str, **kw):
        conn = await self._conn()
        async with httpx.AsyncClient(base_url=conn.api, auth=conn.auth, timeout=20,
                                     transport=self._transport,
                                     headers={"Accept": "application/json", **conn.headers}) as c:
            r = await c.request(method, f"/rest/api/3{path}", **kw)
        if r.status_code == 401 and auth_mode() == "user":
            raise ToolError(f"{NOT_CONNECTED}: Jira rejected your token, reconnect your account.")
        if r.status_code == 403 and auth_mode() == "user":
            raise ToolError("Jira denied this: your own Jira account lacks permission.")
        if r.status_code >= 400:
            raise ToolError(f"Jira error {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def _site(self) -> str:
        return (await self._conn()).site

    # -- operations -----------------------------------------------------------------
    async def projects(self):
        data = await self._req("GET", "/project/search", params={"maxResults": 50})
        return [{"key": p["key"], "name": p["name"], "type": p.get("projectTypeKey")}
                for p in data.get("values", [])]

    async def search(self, jql: str, limit: int):
        data = await self._req("GET", "/search/jql", params={
            "jql": jql, "maxResults": limit,
            "fields": "summary,status,assignee,issuetype,priority"})
        return [self._brief(i) for i in data.get("issues", [])]

    async def issue(self, key: str):
        i = await self._req("GET", f"/issue/{key}")
        f = i["fields"]
        out = self._brief(i)
        out["description"] = _flatten(f.get("description"))
        out["reporter"] = (f.get("reporter") or {}).get("displayName")
        out["url"] = f"{await self._site()}/browse/{key}"
        return out

    async def create(self, project: str, summary: str, description: str, issue_type: str):
        fields = {"project": {"key": project}, "summary": summary,
                  "issuetype": {"name": issue_type}}
        if description:
            fields["description"] = _adf(description)
        r = await self._req("POST", "/issue", json={"fields": fields})
        return {"key": r["key"], "url": f"{await self._site()}/browse/{r['key']}"}

    async def update(self, key: str, summary: str, description: str):
        fields = {}
        if summary:
            fields["summary"] = summary
        if description:
            fields["description"] = _adf(description)
        if fields:
            await self._req("PUT", f"/issue/{key}", json={"fields": fields})
        return {"key": key, "updated": list(fields)}

    async def transitions(self, key: str):
        ts = (await self._req("GET", f"/issue/{key}/transitions"))["transitions"]
        return [{"id": t["id"], "name": t["name"], "to": (t.get("to") or {}).get("name")}
                for t in ts]

    async def transition(self, key: str, status: str):
        want = status.lower()
        match = next((t for t in await self.transitions(key)
                      if want in (t["name"].lower(), (t["to"] or "").lower())), None)
        if not match:
            names = [t["name"] for t in await self.transitions(key)]
            raise ToolError(f"No transition '{status}'. Available: {names}")
        await self._req("POST", f"/issue/{key}/transitions",
                        json={"transition": {"id": match["id"]}})
        return {"key": key, "status": match["to"] or match["name"]}

    @staticmethod
    def _brief(i):
        f = i["fields"]
        return {"key": i["key"], "summary": f.get("summary"),
                "status": (f.get("status") or {}).get("name"),
                "type": (f.get("issuetype") or {}).get("name"),
                "priority": (f.get("priority") or {}).get("name"),
                "assignee": (f.get("assignee") or {}).get("displayName")}
