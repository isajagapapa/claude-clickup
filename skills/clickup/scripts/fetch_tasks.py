#!/usr/bin/env python3
"""Fetch one or more ClickUp tasks and print them as Markdown (or JSON).

Usage:
    fetch_tasks.py <url-or-id> [<url-or-id> ...] [--json] [--tz Asia/Jakarta]
                   [--with-subtasks] [--attachments-dir <dir>]
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
COMMENTS_PAGE_SIZE = 25
MAX_COMMENT_PAGES = 40
MAX_SUBTASK_DETAILS = 25
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

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
# Native IDs always contain a digit, so plain words like "please" are not taken for IDs.
RAW_TASK_ID = re.compile(r"^(?=[0-9a-z]*\d)[0-9a-z]{4,20}$")
# "CU-86abc123" is how ClickUp's Git integrations write a native ID in branches and commits.
CU_TASK_ID = re.compile(r"^cu-((?=[0-9a-z]*[a-z])(?=[0-9a-z]*\d)[0-9a-z]{4,20})$", re.IGNORECASE)
RAW_CUSTOM_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")
TEAM_ID = re.compile(r"^\d+$")

# Work types in priority order: when several tags match, the first type wins.
# Each tag is split into lowercase words and matched against these aliases.
WORK_TYPE_ALIASES = (
    ("security", {"security", "vulnerability", "vuln", "cve", "keamanan"}),
    ("bug", {"bug", "bugs", "bugfix", "fix", "hotfix", "defect", "error", "regression", "incident", "perbaikan"}),
    ("feature", {"feature", "features", "feat", "story", "fitur"}),
    ("enhancement", {"enhancement", "enhance", "improvement", "improve", "optimization", "optimize", "performance", "tweak", "peningkatan"}),
    ("refactor", {"refactor", "refactoring", "cleanup", "debt"}),
    ("ui", {"ui", "ux", "design", "layout", "styling", "desain", "tampilan"}),
    ("research", {"research", "spike", "investigation", "investigate", "poc", "riset"}),
    ("docs", {"docs", "doc", "documentation", "dokumentasi"}),
    ("test", {"test", "tests", "testing", "qa", "e2e", "coverage", "pengujian"}),
    ("chore", {"chore", "maintenance", "infra", "devops", "config", "deps", "dependencies", "upgrade"}),
)
INLINE_IMAGE = re.compile(r"!\[[^\]]*\]\((https://[^)\s]+)")
# Only ClickUp's own hosts get attachment requests, and only they may see the token.
ATTACHMENT_HOSTS = ("clickup-attachments.com", "clickup.com")
# File types Claude can open with its Read tool.
VIEWABLE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}
TITLE_LABEL = re.compile(r"^\s*(?:\[([^\]]+)\]|([A-Za-z][\w-]*)(?:\([^)]*\))?!?\s*:)")


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
        CU-86abc123                                      (task ID as written in branch names)
        PROJ-123                                         (bare custom ID, needs a team ID)
    """
    if "://" not in raw and "/" not in raw:
        prefixed = CU_TASK_ID.match(raw)
        if prefixed:
            return TaskRef(raw, prefixed.group(1).lower())
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


def comment_date(comment: Dict[str, Any]) -> int:
    try:
        return int(comment.get("date") or 0)
    except (TypeError, ValueError):
        return 0


def latest_comments(comments: List[Dict[str, Any]], max_comments: int) -> List[Dict[str, Any]]:
    """Oldest first, keeping only the latest max_comments (-1 keeps all)."""
    comments = sorted(comments, key=comment_date)
    if max_comments >= 0:
        return comments[-max_comments:] if max_comments else []
    return comments


def fetch_comments(task_id: str, token: str, max_comments: int) -> Tuple[List[Dict[str, Any]], bool]:
    """ClickUp returns 25 comments per page, newest first; older pages need start and start_id.

    Returns (comments, complete). complete is False when older comments were left unread.
    """
    path = f"/task/{urllib.parse.quote(task_id)}/comment"
    comments: List[Dict[str, Any]] = []
    seen = set()
    query: Optional[Dict[str, str]] = None
    for _ in range(MAX_COMMENT_PAGES):
        page = api_get(path, token, query).get("comments", [])
        fresh = [comment for comment in page if comment.get("id") not in seen]
        seen.update(comment.get("id") for comment in fresh)
        comments += fresh
        if len(page) < COMMENTS_PAGE_SIZE or not fresh:
            return comments, True
        if 0 <= max_comments < len(comments):
            return comments, False
        oldest = min(page, key=comment_date)
        query = {"start": str(oldest.get("date")), "start_id": str(oldest.get("id"))}
    return comments, False


def fetch_replies(comments: List[Dict[str, Any]], token: str) -> None:
    """Attach the thread replies to every comment that has some."""
    for comment in comments:
        try:
            has_replies = int(comment.get("reply_count") or 0) > 0
        except (TypeError, ValueError):
            has_replies = False
        if has_replies and comment.get("id"):
            path = f"/comment/{urllib.parse.quote(str(comment['id']))}/reply"
            try:
                comment["replies"] = api_get(path, token).get("comments", [])
            except ApiError as error:
                comment["replies_error"] = str(error)


def fetch_task(ref: TaskRef, token: str, max_comments: int = 20, with_subtasks: bool = False) -> Dict[str, Any]:
    task_query = dict(ref.query(), include_subtasks="true", include_markdown_description="true")
    task = api_get(f"/task/{urllib.parse.quote(ref.task_id)}", token, task_query)
    comments, complete = fetch_comments(task["id"], token, max_comments)
    fetch_replies(latest_comments(comments, max_comments), token)
    result = {"task": task, "comments": comments, "comments_complete": complete}
    if with_subtasks:
        result["subtask_details"] = fetch_subtask_details(task, token)
    return result


def fetch_subtask_details(task: Dict[str, Any], token: str) -> Dict[str, Dict[str, Any]]:
    """Full payload of the first MAX_SUBTASK_DETAILS subtasks, keyed by ID."""
    details = {}
    for subtask in (task.get("subtasks") or [])[:MAX_SUBTASK_DETAILS]:
        if subtask.get("id"):
            query = {"include_markdown_description": "true"}
            try:
                details[subtask["id"]] = api_get(f"/task/{urllib.parse.quote(subtask['id'])}", token, query)
            except ApiError as error:
                details[subtask["id"]] = {"error": str(error)}
    return details


def is_attachment_host(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == name or host.endswith("." + name) for name in ATTACHMENT_HOSTS)


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the token to wherever an attachment redirects (often a signed S3 URL)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is not None:
            new_request.remove_header("Authorization")
        return new_request


ATTACHMENT_OPENER = urllib.request.build_opener(_DropAuthOnRedirect)


def http_download(url: str, token: str) -> bytes:
    """GET an attachment: first without the token, then with it if ClickUp asks for auth."""
    for send_token in (False, True):
        headers = {"User-Agent": "claude-clickup"}
        if send_token:
            headers["Authorization"] = token
        try:
            with ATTACHMENT_OPENER.open(urllib.request.Request(url, headers=headers), timeout=REQUEST_TIMEOUT_SECONDS) as response:
                data = response.read(MAX_ATTACHMENT_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403) and not send_token:
                continue
            raise ApiError(f"HTTP {error.code}") from None
        except urllib.error.URLError as error:
            raise ApiError(f"network error: {error.reason}") from None
        except TimeoutError:
            raise ApiError(f"timed out after {REQUEST_TIMEOUT_SECONDS}s") from None
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ApiError(f"larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB")
        return data
    raise ApiError("download failed")


def file_extension(*candidates: Optional[str]) -> str:
    for candidate in candidates:
        extension = os.path.splitext(candidate or "")[1].lstrip(".").lower()
        if extension:
            return extension
    return ""


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "file"


def save_attachments(card: Dict[str, Any], token: str, directory: str) -> None:
    """Download the images and PDFs of a card so Claude can open them; mark the rest as skipped."""
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass  # open() below reports the problem per attachment
    for index, attachment in enumerate(card["attachments"], 1):
        url = attachment.get("url") or ""
        extension = file_extension(attachment.get("name"), urllib.parse.urlparse(url).path)
        if extension not in VIEWABLE_EXTENSIONS:
            attachment["skipped"] = "not an image or PDF"
            continue
        if not is_attachment_host(url):
            attachment["skipped"] = "not hosted by ClickUp"
            continue
        name = safe_filename(os.path.splitext(attachment.get("name") or "")[0])
        path = os.path.join(directory, f"{safe_filename(card['id'] or 'task')}-{index}-{name}.{extension}")
        try:
            data = http_download(url, token)
            with open(path, "wb") as handle:
                handle.write(data)
        except ApiError as error:
            attachment["skipped"] = str(error)
            continue
        except OSError as error:
            attachment["skipped"] = f"could not save: {error.strerror or error}"
            continue
        attachment["path"] = os.path.abspath(path)


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


def match_work_type(label: str) -> Optional[str]:
    words = set(re.split(r"[^0-9a-z]+", label.lower()))
    for work_type, aliases in WORK_TYPE_ALIASES:
        if words & aliases:
            return work_type
    return None


def classify_work_type(tags: List[str], name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Guess the kind of work (bug, feature, ...) from the tags, then from a title prefix.

    Returns (work_type, source), or (None, None) when nothing matches.
    """
    matches = {}
    for tag in tags:
        work_type = match_work_type(tag)
        if work_type and work_type not in matches:
            matches[work_type] = tag
    for work_type, _ in WORK_TYPE_ALIASES:
        if work_type in matches:
            return work_type, f"tag: {matches[work_type]}"

    label = TITLE_LABEL.match(name or "")
    if label:
        work_type = match_work_type(label.group(1) or label.group(2))
        if work_type:
            return work_type, "title prefix"
    return None, None


def format_duration(milliseconds: Any) -> Optional[str]:
    try:
        minutes = int(milliseconds) // 60000
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    hours, minutes = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours}h" if hours else "", f"{minutes}m" if minutes else "") if part)


def custom_field_value(field: Dict[str, Any], tz: Optional[tzinfo]) -> Optional[str]:
    """Render a custom field value as text, or None when it is empty."""
    value = field.get("value")
    if value in (None, "", [], {}):
        return None
    kind = field.get("type")
    options = (field.get("type_config") or {}).get("options") or []
    if kind == "drop_down":
        for index, option in enumerate(options):
            if str(value) in (str(option.get("orderindex", index)), str(option.get("id"))):
                return option.get("name") or str(value)
        return str(value)
    if kind == "labels" and isinstance(value, list):
        labels = {option.get("id"): option.get("label") or option.get("name") for option in options}
        return ", ".join(str(labels.get(item) or item) for item in value)
    if kind == "date":
        return format_timestamp(value, tz)
    if kind == "checkbox":
        return "yes" if value in (True, "true") else None
    if kind == "users" and isinstance(value, list):
        return ", ".join(user_name(user) for user in value) or None
    if kind == "tasks" and isinstance(value, list):
        return ", ".join(str(item.get("name") or item.get("id")) for item in value if isinstance(item, dict)) or None
    if isinstance(value, dict):
        if "formatted_address" in value:
            return value.get("formatted_address")
        if "percent_completed" in value:
            return f"{value.get('percent_completed')}%"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def task_relations(task: Dict[str, Any]) -> List[Dict[str, str]]:
    """Parent, dependencies and linked tasks as (relation, task ID) pairs."""
    own_id = task.get("id")
    relations = []
    if task.get("parent"):
        relations.append({"relation": "Parent", "id": task["parent"]})
    for dependency in task.get("dependencies") or []:
        if dependency.get("task_id") == own_id and dependency.get("depends_on"):
            relations.append({"relation": "Waiting on", "id": dependency["depends_on"]})
        elif dependency.get("depends_on") == own_id and dependency.get("task_id"):
            relations.append({"relation": "Blocking", "id": dependency["task_id"]})
    for link in task.get("linked_tasks") or []:
        other = link.get("link_id") if link.get("task_id") == own_id else link.get("task_id")
        if other:
            relations.append({"relation": "Linked", "id": other})
    return relations


def simplify_checklists(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "name": checklist.get("name"),
            "items": [
                {"name": item.get("name"), "done": bool(item.get("resolved"))}
                for item in checklist.get("items") or []
            ],
        }
        for checklist in task.get("checklists") or []
    ]


def task_description(task: Dict[str, Any]) -> str:
    return (task.get("markdown_description") or task.get("text_content") or "").strip()


def simplify_attachments(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Attachments of the task plus images embedded in its description, without duplicates."""
    attachments = []
    seen = set()
    for attachment in task.get("attachments") or []:
        url = attachment.get("url")
        name = attachment.get("title") or (os.path.basename(urllib.parse.urlparse(url).path) if url else None)
        if not name and not url:
            continue
        attachments.append({"name": name or url, "url": url})
        seen.add(url)
    for url in INLINE_IMAGE.findall(task_description(task)):
        if url not in seen:
            seen.add(url)
            attachments.append({"name": os.path.basename(urllib.parse.urlparse(url).path) or url, "url": url})
    return attachments


def simplify_comment(comment: Dict[str, Any], tz: Optional[tzinfo]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "author": user_name(comment.get("user")),
        "date": format_timestamp(comment.get("date"), tz),
        "text": (comment.get("comment_text") or "").strip(),
    }
    if comment.get("replies"):
        entry["replies"] = [simplify_comment(reply, tz) for reply in sorted(comment["replies"], key=comment_date)]
    if comment.get("replies_error"):
        entry["replies_error"] = comment["replies_error"]
    return entry


def simplify_subtask(subtask: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"id": subtask.get("id"), "name": subtask.get("name"), "status": nested_name(subtask.get("status"))}
    if detail is not None:
        if "error" in detail:
            entry["error"] = detail["error"]
        else:
            entry["description"] = task_description(detail)
            entry["checklists"] = simplify_checklists(detail)
    return entry


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

    comments = latest_comments(result.get("comments", []), max_comments)

    tags = [tag.get("name") for tag in task.get("tags") or [] if tag.get("name")]
    work_type, work_type_source = classify_work_type(tags, task.get("name"))

    fields = []
    for field in task.get("custom_fields") or []:
        value = custom_field_value(field, tz)
        if value and field.get("name"):
            fields.append({"name": field["name"], "value": value})

    subtask_details = result.get("subtask_details")

    return {
        "id": task.get("id"),
        "custom_id": task.get("custom_id"),
        "name": task.get("name"),
        "url": task.get("url"),
        "status": nested_name(task.get("status")),
        "priority": nested_name(task.get("priority"), "priority"),
        "assignees": [user_name(user) for user in task.get("assignees") or []],
        "creator": user_name(task.get("creator")) if task.get("creator") else None,
        "tags": tags,
        "work_type": work_type,
        "work_type_source": work_type_source,
        "location": " › ".join(location) or None,
        "start_date": format_timestamp(task.get("start_date"), tz),
        "due_date": format_timestamp(task.get("due_date"), tz),
        "created": format_timestamp(task.get("date_created"), tz),
        "updated": format_timestamp(task.get("date_updated"), tz),
        "points": task.get("points"),
        "time_estimate": format_duration(task.get("time_estimate")),
        "time_spent": format_duration(task.get("time_spent")),
        "fields": fields,
        "relations": task_relations(task),
        "description": task_description(task),
        "checklists": simplify_checklists(task),
        "subtasks": [
            simplify_subtask(subtask, None if subtask_details is None else subtask_details.get(subtask.get("id"), None))
            for subtask in task.get("subtasks") or []
        ],
        "attachments": simplify_attachments(task),
        "comments": [simplify_comment(comment, tz) for comment in comments],
        "comment_count": len(result.get("comments", [])),
        "comments_complete": result.get("comments_complete", True),
    }


def format_checklists(checklists: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for checklist in checklists:
        lines += ["", f"**{checklist['name']}**"]
        lines += [f"- [{'x' if item['done'] else ' '}] {item['name']}" for item in checklist["items"]]
    return lines


def format_comment(comment: Dict[str, Any], prefix: str = "") -> List[str]:
    header = f"**{comment['author']}** · {comment['date'] or ''}".rstrip(" ·")
    lines = [header] + (comment["text"] or "_(empty)_").splitlines()
    lines = [f"{prefix}{line}".rstrip() for line in lines]
    for reply in comment.get("replies", []):
        lines += [f"{prefix}>"] + format_comment(reply, prefix + "> ")
    if comment.get("replies_error"):
        lines.append(f"{prefix}> _Replies not loaded: {comment['replies_error']}_")
    return lines


def task_link(task_id: str) -> str:
    return f"https://app.clickup.com/t/{task_id}"


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
        ("Type", f"{card['work_type']} ({card['work_type_source']})" if card.get("work_type") else None),
        ("Start", card.get("start_date")),
        ("Due", card.get("due_date")),
        ("Created", card.get("created")),
        ("Updated", card.get("updated")),
        ("Points", card.get("points")),
        ("Estimate", card.get("time_estimate")),
        ("Time spent", card.get("time_spent")),
    ]
    lines += [f"- **{label}:** {value}" for label, value in facts if value not in (None, "")]

    if card.get("fields"):
        lines += ["", "### Fields", ""]
        lines += [f"- **{field['name']}:** {field['value']}" for field in card["fields"]]

    if card.get("relations"):
        lines += ["", "### Relations", ""]
        lines += [f"- {relation['relation']}: {task_link(relation['id'])}" for relation in card["relations"]]

    lines += ["", "### Description", "", card["description"] or "_No description._"]

    if card["checklists"]:
        lines += ["", "### Checklists"]
        lines += format_checklists(card["checklists"])

    if card["subtasks"]:
        lines += ["", "### Subtasks"]
        brief = []
        for subtask in card["subtasks"]:
            label = f"{subtask['name']} ({subtask['status']}) `{subtask['id']}`"
            if "error" in subtask:
                lines += ["", f"#### {label}", "", f"_Not loaded: {subtask['error']}_"]
            elif "description" in subtask:
                lines += ["", f"#### {label}", "", subtask["description"] or "_No description._"]
                lines += format_checklists(subtask["checklists"])
            else:
                brief.append(f"- {label}")
        if brief:
            lines += [""] + brief

    if card["attachments"]:
        lines += ["", "### Attachments", ""]
        for attachment in card["attachments"]:
            line = f"- {attachment['name']}"
            if attachment.get("path"):
                line += f" → saved to `{attachment['path']}`"
            elif attachment.get("skipped"):
                line += f" (not downloaded: {attachment['skipped']})"
            lines.append(line)

    shown = len(card["comments"])
    total = f"{card['comment_count']}" + ("" if card.get("comments_complete", True) else "+")
    heading = "### Comments"
    if card["comment_count"] > shown or not card.get("comments_complete", True):
        heading += f" (latest {shown} of {total})"
    if card["comment_count"]:
        lines += ["", heading]
        for comment in card["comments"]:
            lines += [""] + format_comment(comment)

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
    parser.add_argument("--with-subtasks", action="store_true", help=f"also load the description and checklists of up to {MAX_SUBTASK_DETAILS} subtasks per task")
    parser.add_argument("--attachments-dir", help="download image and PDF attachments (also images in the description) into this folder")
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
            card = simplify(fetch_task(ref, token, args.max_comments, args.with_subtasks), tz, args.max_comments)
        except ApiError as api_error:
            return {"input": raw, "error": str(api_error)}
        if args.attachments_dir:
            save_attachments(card, token, args.attachments_dir)
        return {"input": raw, "card": card}

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
