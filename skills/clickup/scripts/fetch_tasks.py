#!/usr/bin/env python3
"""Fetch one or more ClickUp tasks and print them as Markdown (or JSON).

Usage:
    fetch_tasks.py <url-or-id> [<url-or-id> ...] [--json] [--tz Asia/Jakarta]
    echo "<url> <url>" | fetch_tasks.py

Reads the personal API token from the CLICKUP_API_TOKEN environment variable.
Standard library only, Python 3.9+.

Exit codes:
    0  every task was fetched
    1  at least one task failed (the others are still printed)
    2  invalid command-line usage
    3  CLICKUP_API_TOKEN is not set
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, tzinfo
from typing import Any, Dict, List, Optional, Tuple

API_BASE = "https://api.clickup.com/api/v2"
TOKEN_ENV = "CLICKUP_API_TOKEN"
TEAM_ENV = "CLICKUP_TEAM_ID"
REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRY_WAIT_SECONDS = 60
MAX_WORKERS = 5

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_TOKEN = 3

TOKEN_SETUP_MESSAGE = f"""\
{TOKEN_ENV} is not set.

1. In ClickUp open your avatar > Settings > Apps > API Token > Generate (starts with pk_).
2. Store it as an environment variable, never in a chat or a repository:
     macOS/Linux:  echo 'export {TOKEN_ENV}="pk_..."' >> ~/.zshrc   (or ~/.bashrc)
     Windows:      setx {TOKEN_ENV} "pk_..."
3. Restart your terminal / editor / Claude Code so the new variable is picked up.
"""

CLICKUP_HOSTS = {"app.clickup.com", "clickup.com", "www.clickup.com"}
RAW_TASK_ID = re.compile(r"^[0-9a-z]{4,20}$")
RAW_CUSTOM_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")
TEAM_ID = re.compile(r"^\d+$")


class InvalidReference(ValueError):
    """Raised when an input cannot be turned into a ClickUp task reference."""


class TaskRef:
    """A task to fetch: either a native task ID or a custom ID scoped to a team."""

    def __init__(self, raw: str, task_id: str, team_id: Optional[str] = None) -> None:
        self.raw = raw
        self.task_id = task_id
        self.team_id = team_id

    @property
    def is_custom(self) -> bool:
        return self.team_id is not None

    @property
    def key(self) -> Tuple[str, Optional[str]]:
        return (self.task_id, self.team_id)

    def query(self) -> Dict[str, str]:
        if self.is_custom:
            return {"custom_task_ids": "true", "team_id": str(self.team_id)}
        return {}

    def __repr__(self) -> str:
        return f"TaskRef({self.task_id!r}, team_id={self.team_id!r})"


def split_inputs(values: List[str]) -> List[str]:
    """Split arguments on whitespace and commas, dropping empties and duplicates."""
    seen = set()
    result = []
    for value in values:
        for part in re.split(r"[\s,]+", value):
            part = part.strip().strip("<>()[]\"'")
            if part and part not in seen:
                seen.add(part)
                result.append(part)
    return result


def parse_reference(raw: str, default_team_id: Optional[str] = None) -> TaskRef:
    """Turn a ClickUp task URL or a bare ID into a TaskRef.

    Supported:
        https://app.clickup.com/t/86abc123
        https://app.clickup.com/t/9012345678/PROJ-123   (custom task ID)
        86abc123                                         (bare task ID)
        PROJ-123                                         (bare custom ID, needs a team ID)
    """
    if "://" not in raw and "/" not in raw:
        if RAW_CUSTOM_ID.match(raw):
            if not default_team_id:
                raise InvalidReference(
                    f"custom ID {raw!r} needs a team ID: pass --team-id or set {TEAM_ENV}"
                )
            return TaskRef(raw, raw, default_team_id)
        if RAW_TASK_ID.match(raw):
            return TaskRef(raw, raw)
        raise InvalidReference(f"not a ClickUp task link or ID: {raw!r}")

    url = raw if "://" in raw else f"https://{raw}"
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in CLICKUP_HOSTS:
        raise InvalidReference(f"not a ClickUp link: {raw!r}")

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) >= 2 and segments[0] == "t":
        if len(segments) >= 3 and TEAM_ID.match(segments[1]):
            return TaskRef(raw, segments[2], segments[1])
        return TaskRef(raw, segments[1])

    raise InvalidReference(
        f"unsupported ClickUp link (open the task and copy its link, app.clickup.com/t/...): {raw!r}"
    )


class ApiError(Exception):
    """A ClickUp API call failed."""


def explain_http_error(status: int, body: str) -> str:
    detail = ""
    try:
        payload = json.loads(body)
        detail = payload.get("err") or payload.get("error") or ""
    except (ValueError, AttributeError):
        pass
    hints = {
        401: "token is invalid or expired",
        403: "no access to this task (check the workspace/permissions of the token owner)",
        404: "task not found (wrong ID, deleted, or custom ID without the right team)",
        429: "rate limited by ClickUp, try again in a minute",
    }
    message = hints.get(status, "request failed")
    return f"HTTP {status}: {message}" + (f" ({detail})" if detail else "")


def api_get(path: str, token: str, query: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url,
        headers={"Authorization": token, "Accept": "application/json", "User-Agent": "claude-clickup"},
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            if error.code == 429 and attempt == 0:
                time.sleep(retry_delay(error.headers))
                continue
            raise ApiError(explain_http_error(error.code, body)) from None
        except urllib.error.URLError as error:
            raise ApiError(f"network error: {error.reason}") from None
        except TimeoutError:
            raise ApiError(f"timed out after {REQUEST_TIMEOUT_SECONDS}s") from None
        except ValueError:
            raise ApiError("ClickUp returned a response that is not JSON") from None

    raise ApiError("request failed")


def retry_delay(headers: Any) -> float:
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), MAX_RETRY_WAIT_SECONDS)
    reset = headers.get("X-RateLimit-Reset") if headers else None
    if reset and reset.isdigit():
        return max(1.0, min(float(reset) - time.time(), MAX_RETRY_WAIT_SECONDS))
    return 10.0


def fetch_task(ref: TaskRef, token: str) -> Dict[str, Any]:
    task_query = dict(ref.query(), include_subtasks="true", include_markdown_description="true")
    task = api_get(f"/task/{urllib.parse.quote(ref.task_id)}", token, task_query)
    comments = api_get(f"/task/{urllib.parse.quote(task['id'])}/comment", token).get("comments", [])
    return {"task": task, "comments": comments}


def resolve_timezone(name: Optional[str]) -> Optional[tzinfo]:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        print(f"warning: unknown time zone {name!r}, using local time", file=sys.stderr)
        return None


def format_timestamp(value: Any, tz: Optional[tzinfo]) -> Optional[str]:
    """ClickUp sends dates as epoch milliseconds, usually as strings."""
    if value in (None, ""):
        return None
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z").strip()


def user_name(user: Optional[Dict[str, Any]]) -> str:
    if not user:
        return "unknown"
    return user.get("username") or user.get("email") or str(user.get("id", "unknown"))


def nested_name(value: Any, key: str = "status") -> Optional[str]:
    if isinstance(value, dict):
        return value.get(key)
    return value


def simplify(result: Dict[str, Any], tz: Optional[tzinfo], max_comments: int) -> Dict[str, Any]:
    """Reduce the raw API payload to the fields that matter when reading a card."""
    task = result["task"]
    folder = task.get("folder") or {}
    location = [
        part
        for part in (
            None if folder.get("hidden") else folder.get("name"),
            (task.get("list") or {}).get("name"),
        )
        if part
    ]

    comments = sorted(result.get("comments", []), key=lambda comment: int(comment.get("date") or 0))
    if max_comments >= 0:
        comments = comments[-max_comments:] if max_comments else []

    return {
        "id": task.get("id"),
        "custom_id": task.get("custom_id"),
        "name": task.get("name"),
        "url": task.get("url"),
        "status": nested_name(task.get("status")),
        "priority": nested_name(task.get("priority"), "priority"),
        "assignees": [user_name(user) for user in task.get("assignees") or []],
        "creator": user_name(task.get("creator")) if task.get("creator") else None,
        "tags": [tag.get("name") for tag in task.get("tags") or [] if tag.get("name")],
        "location": " › ".join(location) or None,
        "start_date": format_timestamp(task.get("start_date"), tz),
        "due_date": format_timestamp(task.get("due_date"), tz),
        "created": format_timestamp(task.get("date_created"), tz),
        "updated": format_timestamp(task.get("date_updated"), tz),
        "description": (task.get("markdown_description") or task.get("text_content") or "").strip(),
        "checklists": [
            {
                "name": checklist.get("name"),
                "items": [
                    {"name": item.get("name"), "done": bool(item.get("resolved"))}
                    for item in checklist.get("items") or []
                ],
            }
            for checklist in task.get("checklists") or []
        ],
        "subtasks": [
            {"id": subtask.get("id"), "name": subtask.get("name"), "status": nested_name(subtask.get("status"))}
            for subtask in task.get("subtasks") or []
        ],
        "attachments": [
            attachment.get("title") or attachment.get("url")
            for attachment in task.get("attachments") or []
        ],
        "comments": [
            {
                "author": user_name(comment.get("user")),
                "date": format_timestamp(comment.get("date"), tz),
                "text": (comment.get("comment_text") or "").strip(),
            }
            for comment in comments
        ],
        "comment_count": len(result.get("comments", [])),
    }


def format_markdown(card: Dict[str, Any]) -> str:
    title = card["name"] or card["id"]
    if card.get("custom_id"):
        title = f"[{card['custom_id']}] {title}"
    lines = [f"## {title}", ""]

    facts = [
        ("Link", card.get("url")),
        ("Status", card.get("status")),
        ("Priority", card.get("priority")),
        ("Assignees", ", ".join(card["assignees"]) or None),
        ("Creator", card.get("creator")),
        ("Location", card.get("location")),
        ("Tags", ", ".join(card["tags"]) or None),
        ("Start", card.get("start_date")),
        ("Due", card.get("due_date")),
        ("Created", card.get("created")),
        ("Updated", card.get("updated")),
    ]
    lines += [f"- **{label}:** {value}" for label, value in facts if value]

    lines += ["", "### Description", "", card["description"] or "_No description._"]

    if card["checklists"]:
        lines += ["", "### Checklists"]
        for checklist in card["checklists"]:
            lines += ["", f"**{checklist['name']}**"]
            lines += [f"- [{'x' if item['done'] else ' '}] {item['name']}" for item in checklist["items"]]

    if card["subtasks"]:
        lines += ["", "### Subtasks", ""]
        lines += [f"- {subtask['name']} ({subtask['status']}) `{subtask['id']}`" for subtask in card["subtasks"]]

    if card["attachments"]:
        lines += ["", "### Attachments", ""]
        lines += [f"- {attachment}" for attachment in card["attachments"]]

    shown = len(card["comments"])
    heading = "### Comments"
    if card["comment_count"] > shown:
        heading += f" (latest {shown} of {card['comment_count']})"
    if card["comment_count"]:
        lines += ["", heading]
        for comment in card["comments"]:
            lines += ["", f"**{comment['author']}** · {comment['date'] or ''}".rstrip(" ·"), comment["text"] or "_(empty)_"]

    return "\n".join(lines)


def format_error_markdown(raw: str, message: str) -> str:
    return f"## ⚠️ {raw}\n\n- **Error:** {message}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch ClickUp tasks as Markdown.")
    parser.add_argument("refs", nargs="*", help="task links or IDs (space, comma or newline separated)")
    parser.add_argument("--json", action="store_true", help="print simplified JSON instead of Markdown")
    parser.add_argument("--tz", help="IANA time zone for dates, e.g. Asia/Jakarta (default: local time)")
    parser.add_argument("--team-id", default=os.environ.get(TEAM_ENV), help=f"team (workspace) ID for bare custom IDs (default: ${TEAM_ENV})")
    parser.add_argument("--max-comments", type=int, default=20, help="latest comments to show per task, -1 for all (default: 20)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    values = list(args.refs)
    if not values and not sys.stdin.isatty():
        values = [sys.stdin.read()]
    inputs = split_inputs(values)
    if not inputs:
        build_parser().print_usage(sys.stderr)
        print("error: give at least one ClickUp task link or ID", file=sys.stderr)
        return 2

    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(TOKEN_SETUP_MESSAGE, file=sys.stderr)
        return EXIT_NO_TOKEN

    tz = resolve_timezone(args.tz)

    refs: List[Tuple[str, Optional[TaskRef], Optional[str]]] = []
    seen_keys = set()
    for raw in inputs:
        try:
            ref = parse_reference(raw, args.team_id)
        except InvalidReference as error:
            refs.append((raw, None, str(error)))
            continue
        if ref.key in seen_keys:
            continue
        seen_keys.add(ref.key)
        refs.append((raw, ref, None))

    def load(entry: Tuple[str, Optional[TaskRef], Optional[str]]) -> Dict[str, Any]:
        raw, ref, error = entry
        if ref is None:
            return {"input": raw, "error": error}
        try:
            return {"input": raw, "card": simplify(fetch_task(ref, token), tz, args.max_comments)}
        except ApiError as api_error:
            return {"input": raw, "error": str(api_error)}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(load, refs))

    failures = sum(1 for result in results if "error" in result)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        loaded = len(results) - failures
        header = f"# ClickUp: {loaded} of {len(results)} task(s) loaded"
        blocks = [header]
        for result in results:
            if "error" in result:
                blocks.append(format_error_markdown(result["input"], result["error"]))
            else:
                blocks.append(format_markdown(result["card"]))
        print("\n\n---\n\n".join(blocks))

    return EXIT_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
