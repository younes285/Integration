"""Append-only JSON-lines audit log: who did what, and whether it was allowed."""
import json
import os
import time


def clip(args: dict, limit: int = 200) -> dict:
    """Shorten long values so the log stays readable and free of large payloads."""
    out = {}
    for k, v in (args or {}).items():
        out[k] = v if not isinstance(v, str) or len(v) <= limit else v[:limit] + "..."
    return out


def record(event: str, **fields) -> None:
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    try:
        with open(os.getenv("AUDIT_LOG", "audit.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
    except OSError:
        pass  # auditing must never break a tool call
