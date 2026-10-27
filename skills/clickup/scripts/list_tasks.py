#!/usr/bin/env python3
"""List open ClickUp tasks: the ones assigned to you, or the ones in a list or view.

Usage:
    list_tasks.py [--team-id <id>] [--json] [--tz Asia/Jakarta]
    list_tasks.py <list-or-view-link> [<list-or-view-link> ...]

Reuses fetch_tasks.py next to this file. Reads the token from CLICKUP_API_TOKEN.
Standard library only, Python 3.9+.

Exit codes:
    0  every source was loaded
    1  at least one source failed (the others are still printed)
    2  invalid command-line usage
    3  CLICKUP_API_TOKEN is not set
"""

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_fetcher() -> Any:
    # Loaded by path because `python3 -I` leaves this folder out of sys.path.
    path = Path(__file__).resolve().with_name("fetch_tasks.py")
    spec = importlib.util.spec_from_file_location("fetch_tasks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


fetch_tasks = load_fetcher()
ApiError = fetch_tasks.ApiError

PAGE_SIZE = 100
VIEW_KINDS = {"l", "b", "t", "c", "g"}
NO_PRIORITY_RANK = 5


class InvalidSource(ValueError):
    """Raised when an input is not a ClickUp list or view link."""


def parse_source(raw: str) -> Tuple[str, str]:
    """Turn a list or view link into ("list" | "view", id).

    Supported:
        https://app.clickup.com/9012345678/v/li/901234567890     (list)
        https://app.clickup.com/9012345678/v/b/li/901234567890   (list as board)
        https://app.clickup.com/9012345678/v/l/6-901234567890-1  (saved view)
    """
    url = raw if "://" in raw else f"https://{raw}"
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in fetch_tasks.CLICKUP_HOSTS:
        raise InvalidSource(f"not a ClickUp link: {raw!r}")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if "li" in segments:
        position = segments.index("li")
        if position + 1 < len(segments) and segments[position + 1].isdigit():
            return "list", segments[position + 1]
    if len(segments) >= 4 and segments[1] == "v" and segments[2] in VIEW_KINDS:
        return "view", segments[3]
    if len(segments) >= 2 and segments[0] == "t":
        raise InvalidSource(f"that is a task link, read it with fetch_tasks.py: {raw!r}")
    raise InvalidSource(f"unsupported ClickUp link (use a list or view link, app.clickup.com/<team>/v/li/<id>): {raw!r}")


def fetch_pages(path: str, token: str, query: Dict[str, str], limit: int) -> List[Dict[str, Any]]:
    """Follow ClickUp's page parameter until the last page or the limit."""
    tasks: List[Dict[str, Any]] = []
    page = 0
    while len(tasks) < limit:
        payload = fetch_tasks.api_get(path, token, dict(query, page=str(page)))
        batch = payload.get("tasks", [])
        tasks += batch
        if payload.get("last_page", True) or len(batch) < PAGE_SIZE:
            break
        page += 1
    return tasks[:limit]


def resolve_team(token: str, team_id: Optional[str]) -> str:
    if team_id:
        return team_id
    teams = fetch_tasks.api_get("/team", token).get("teams", [])
    if len(teams) == 1:
        return str(teams[0]["id"])
    choices = ", ".join(f"{team.get('name')} ({team.get('id')})" for team in teams) or "none"
    raise ApiError(f"pick a workspace with --team-id or {fetch_tasks.TEAM_ENV}: {choices}")


def fetch_my_tasks(token: str, team_id: Optional[str], include_closed: bool, limit: int) -> Tuple[str, List[Dict[str, Any]]]:
    user = fetch_tasks.api_get("/user", token).get("user") or {}
    team = resolve_team(token, team_id)
    query = {
        "assignees[]": str(user.get("id")),
        "subtasks": "true",
        "include_closed": "true" if include_closed else "false",
        "order_by": "due_date",
    }
    tasks = fetch_pages(f"/team/{urllib.parse.quote(team)}/task", token, query, limit)
    return f"assigned to {fetch_tasks.user_name(user)}", tasks


def fetch_source_tasks(kind: str, source_id: str, token: str, include_closed: bool, limit: int) -> List[Dict[str, Any]]:
    query = {"include_closed": "true" if include_closed else "false"}
    if kind == "list":
        query["subtasks"] = "true"
    return fetch_pages(f"/{kind}/{urllib.parse.quote(source_id)}/task", token, query, limit)


def due_millis(task: Dict[str, Any]) -> Optional[int]:
    try:
        return int(task.get("due_date"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def priority_rank(task: Dict[str, Any]) -> int:
    try:
        return int((task.get("priority") or {}).get("orderindex"))
    except (TypeError, ValueError):
        return NO_PRIORITY_RANK


def to_rows(tasks: List[Dict[str, Any]], tz: Any, now_millis: int) -> List[Dict[str, Any]]:
    """Simplified cards, overdue and soonest due first, then by priority."""
    ordered = sorted(
        tasks,
        key=lambda task: (due_millis(task) is None, due_millis(task) or 0, priority_rank(task)),
    )
    rows = []
    for task in ordered:
        card = fetch_tasks.simplify({"task": task, "comments": []}, tz, 0)
        due = due_millis(task)
        closed = (task.get("status") or {}).get("type") in ("closed", "done")
        card["overdue"] = due is not None and due < now_millis and not closed
        rows.append(card)
    return rows


def cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def format_markdown(source: str, rows: List[Dict[str, Any]]) -> str:
    lines = [f"## {len(rows)} task(s) {source}", ""]
    if not rows:
        return "\n".join(lines + ["_No open tasks._"])
    lines += ["| # | Task | Type | Status | Priority | Due | Location |", "|---|---|---|---|---|---|---|"]
    for number, row in enumerate(rows, 1):
        title = f"[{cell(row['name'])}]({row['url']})" if row.get("url") else cell(row["name"])
        due = cell(row.get("due_date"))
        if row["overdue"]:
            due = f"⚠️ {due} (overdue)"
        lines.append(
            f"| {number} | {title} `{row['id']}` | {cell(row.get('work_type'))} | {cell(row.get('status'))} "
            f"| {cell(row.get('priority'))} | {due} | {cell(row.get('location'))} |"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List open ClickUp tasks assigned to you, or in a list or view.")
    parser.add_argument("sources", nargs="*", help="list or view links (default: tasks assigned to you)")
    parser.add_argument("--json", action="store_true", help="print simplified JSON instead of Markdown")
    parser.add_argument("--tz", help="IANA time zone for dates, e.g. Asia/Jakarta (default: local time)")
    parser.add_argument("--team-id", default=os.environ.get(fetch_tasks.TEAM_ENV), help=f"workspace ID for your tasks (default: ${fetch_tasks.TEAM_ENV}, or the only workspace)")
    parser.add_argument("--include-closed", action="store_true", help="also list closed tasks")
    parser.add_argument("--limit", type=int, default=100, help="maximum tasks per source (default: 100)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        build_parser().print_usage(sys.stderr)
        print("error: --limit must be at least 1", file=sys.stderr)
        return 2

    token = os.environ.get(fetch_tasks.TOKEN_ENV, "").strip()
    if not token:
        print(fetch_tasks.TOKEN_SETUP_MESSAGE, file=sys.stderr)
        return fetch_tasks.EXIT_NO_TOKEN

    tz = fetch_tasks.resolve_timezone(args.tz)
    now_millis = int(time.time() * 1000)

    results: List[Dict[str, Any]] = []
    for raw in fetch_tasks.split_inputs(args.sources) or [None]:
        try:
            if raw is None:
                source, tasks = fetch_my_tasks(token, args.team_id, args.include_closed, args.limit)
            else:
                kind, source_id = parse_source(raw)
                source = f"in {kind} {source_id}"
                tasks = fetch_source_tasks(kind, source_id, token, args.include_closed, args.limit)
        except (InvalidSource, ApiError) as error:
            results.append({"input": raw or "my tasks", "error": str(error)})
            continue
        results.append({"input": raw or "my tasks", "source": source, "tasks": to_rows(tasks, tz, now_millis)})

    failures = sum(1 for result in results if "error" in result)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        blocks = ["# ClickUp task list"]
        for result in results:
            if "error" in result:
                blocks.append(fetch_tasks.format_error_markdown(result["input"], result["error"]))
            else:
                blocks.append(format_markdown(result["source"], result["tasks"]))
        print("\n\n---\n\n".join(blocks))

    return fetch_tasks.EXIT_FAILED if failures else fetch_tasks.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
