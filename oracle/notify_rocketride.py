"""POST a course-loaded event to the RocketRide webhook (pipeline P6). Degraded on any failure.

Env: ROCKETRIDE_WEBHOOK_URL (https), ROCKETRIDE_WEBHOOK_KEY, and optionally
ROCKETRIDE_WEBHOOK_AUTH_HEADER (default "Authorization", sent as "Bearer <key>").
The header convention is [TEST] — confirm it against the webhook node in RocketRide.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from .config import USER_AGENT, check_course, course_path, emit, read_report


def add_args(p):
    p.add_argument("--course", required=True)
    p.add_argument("--event", default="course-loaded")


def _degraded(course: str, warning: str) -> int:
    emit({"ok": True, "available": False, "course": course, "warning": warning})
    return 0


def run(args) -> int:
    course = check_course(args.course)
    url = os.environ.get("ROCKETRIDE_WEBHOOK_URL", "").strip()
    if not url:
        return _degraded(course, "ROCKETRIDE_WEBHOOK_URL not set; notification skipped")
    if urlparse(url).scheme != "https":
        return _degraded(course, "ROCKETRIDE_WEBHOOK_URL must be https; notification skipped")
    summary_path = course_path("work", course) / "summary.json"
    payload = {"event": args.event, "course": course,
               "summary": json.loads(summary_path.read_text()) if summary_path.exists() else None,
               "validate": read_report(course, "validate")}
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    key = os.environ.get("ROCKETRIDE_WEBHOOK_KEY", "").strip()
    if key:
        header = os.environ.get("ROCKETRIDE_WEBHOOK_AUTH_HEADER", "Authorization").strip()
        headers[header] = f"Bearer {key}" if header.lower() == "authorization" else key
    try:
        import requests

        resp = requests.post(url, data=json.dumps(payload), headers=headers, timeout=(10, 30))
    except Exception as err:  # network trouble must not fail a load
        return _degraded(course, f"webhook POST failed: {type(err).__name__}")
    if resp.status_code >= 300:
        return _degraded(course, f"webhook returned HTTP {resp.status_code}")
    emit({"ok": True, "available": True, "course": course, "status": resp.status_code})
    return 0
