import contextlib
import importlib.util
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "clickup" / "scripts" / "list_tasks.py"
spec = importlib.util.spec_from_file_location("list_tasks", SCRIPT)
list_tasks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(list_tasks)

NOW = 1761534000000
DAY = 24 * 60 * 60 * 1000


def task(task_id, name, due=None, priority=None, status="to do", tags=()):
    return {
        "id": task_id,
        "name": name,
        "url": f"https://app.clickup.com/t/{task_id}",
        "status": {"status": status, "type": "open"},
        "priority": {"priority": priority, "orderindex": {"urgent": "1", "high": "2", "normal": "3", "low": "4"}[priority]} if priority else None,
        "due_date": str(due) if due else None,
        "tags": [{"name": tag} for tag in tags],
        "list": {"name": "Sprint"},
    }


MY_TASKS = [
    task("86later", "Later | soon", due=NOW + 3 * DAY, priority="low"),
    task("86nodue", "No due date", priority="urgent"),
    task("86late", "Overdue fix", due=NOW - DAY, priority="high", tags=["bug"]),
]


def run_main(argv, api, env=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, env if env is not None else {"CLICKUP_API_TOKEN": "pk_test"}, clear=True))
        stack.enter_context(mock.patch.object(list_tasks.fetch_tasks, "api_get", api))
        stack.enter_context(mock.patch.object(list_tasks.time, "time", return_value=NOW / 1000))
        stack.enter_context(contextlib.redirect_stdout(stdout))
        stack.enter_context(contextlib.redirect_stderr(stderr))
        code = list_tasks.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def fake_api(path, token, query=None):
    if path == "/user":
        return {"user": {"id": 42, "username": "faisal"}}
    if path == "/team":
        return {"teams": [{"id": "9012", "name": "Acme"}]}
    if path == "/team/9012/task":
        return {"tasks": MY_TASKS, "last_page": True}
    if path == "/list/901234/task":
        return {"tasks": [task("86inlist", "In the list")], "last_page": True}
    raise list_tasks.ApiError("HTTP 404: not found")


class ParseSourceTest(unittest.TestCase):
    def test_list_links(self):
        for raw in (
            "https://app.clickup.com/9012/v/li/901234",
            "https://app.clickup.com/9012/v/b/li/901234?pr=1",
            "app.clickup.com/9012/v/li/901234/",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(list_tasks.parse_source(raw), ("list", "901234"))

    def test_view_link(self):
        self.assertEqual(list_tasks.parse_source("https://app.clickup.com/9012/v/l/6-901234-1"), ("view", "6-901234-1"))

    def test_rejects_other_links(self):
        for raw in ("https://app.clickup.com/t/86abc123", "https://app.clickup.com/9012/v/dc/abc", "https://example.com/9012/v/li/1"):
            with self.subTest(raw=raw), self.assertRaises(list_tasks.InvalidSource):
                list_tasks.parse_source(raw)


class MainTest(unittest.TestCase):
    def test_my_tasks_sorted_with_overdue(self):
        api = mock.Mock(side_effect=fake_api)
        code, stdout, _ = run_main([], api)
        self.assertEqual(code, 0)
        self.assertIn("## 3 task(s) assigned to faisal", stdout)
        rows = [line for line in stdout.splitlines() if line.startswith("| ") and "`86" in line]
        self.assertEqual([row.split("`")[1] for row in rows], ["86late", "86later", "86nodue"])
        self.assertIn("⚠️", rows[0])
        self.assertIn("(overdue)", rows[0])
        self.assertIn("| bug |", rows[0])
        self.assertNotIn("overdue", rows[1])
        self.assertIn("Later \\| soon", rows[1])
        query = next(call.args[2] for call in api.call_args_list if call.args[0] == "/team/9012/task")
        self.assertEqual(query["assignees[]"], "42")
        self.assertEqual(query["include_closed"], "false")
        self.assertNotIn("pk_test", stdout)

    def test_team_from_env_skips_lookup(self):
        api = mock.Mock(side_effect=fake_api)
        run_main([], api, env={"CLICKUP_API_TOKEN": "pk_test", "CLICKUP_TEAM_ID": "9012"})
        self.assertNotIn("/team", [call.args[0] for call in api.call_args_list])

    def test_several_workspaces_need_a_choice(self):
        def api(path, token, query=None):
            if path == "/team":
                return {"teams": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]}
            return fake_api(path, token, query)

        code, stdout, _ = run_main([], mock.Mock(side_effect=api))
        self.assertEqual(code, 1)
        self.assertIn("A (1), B (2)", stdout)

    def test_list_link_and_failure(self):
        code, stdout, _ = run_main(
            ["https://app.clickup.com/9012/v/li/901234", "https://app.clickup.com/t/86abc123"],
            mock.Mock(side_effect=fake_api),
        )
        self.assertEqual(code, 1)
        self.assertIn("## 1 task(s) in list 901234", stdout)
        self.assertIn("that is a task link", stdout)

    def test_pagination_and_limit(self):
        pages = [{"tasks": [task(f"86p{page}x{n}", "t") for n in range(list_tasks.PAGE_SIZE)], "last_page": False} for page in range(3)]
        api = mock.Mock(side_effect=lambda path, token, query=None: pages[int(query["page"])])
        with mock.patch.dict(os.environ, {"CLICKUP_API_TOKEN": "pk_test"}, clear=True):
            with mock.patch.object(list_tasks.fetch_tasks, "api_get", api):
                tasks = list_tasks.fetch_pages("/list/1/task", "pk_test", {}, 150)
        self.assertEqual(len(tasks), 150)
        self.assertEqual(api.call_count, 2)

    def test_json(self):
        code, stdout, _ = run_main(["--json"], mock.Mock(side_effect=fake_api))
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload[0]["tasks"][0]["id"], "86late")
        self.assertTrue(payload[0]["tasks"][0]["overdue"])

    def test_missing_token(self):
        code, stdout, stderr = run_main([], mock.Mock(side_effect=fake_api), env={})
        self.assertEqual(code, 3)
        self.assertEqual(stdout, "")
        self.assertIn("CLICKUP_API_TOKEN is not set", stderr)


if __name__ == "__main__":
    unittest.main()
