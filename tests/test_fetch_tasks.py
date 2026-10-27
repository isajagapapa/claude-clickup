import contextlib
import importlib.util
import io
import json
import os
import unittest
from datetime import timezone
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "clickup" / "scripts" / "fetch_tasks.py"
spec = importlib.util.spec_from_file_location("fetch_tasks", SCRIPT)
fetch_tasks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_tasks)

TASK = {
    "id": "86abc123",
    "custom_id": None,
    "name": "Donation notification email",
    "url": "https://app.clickup.com/t/86abc123",
    "status": {"status": "in progress"},
    "priority": {"priority": "high"},
    "assignees": [{"username": "faisal"}, {"email": "dev@example.com"}],
    "creator": {"username": "pm"},
    "tags": [{"name": "backend"}],
    "folder": {"name": "Sprint 13", "hidden": False},
    "list": {"name": "Backlog"},
    "due_date": "1761534000000",
    "start_date": None,
    "date_created": "1761447600000",
    "markdown_description": "Send an email after **donation** is verified.",
    "text_content": "Send an email after donation is verified.",
    "checklists": [
        {"name": "Acceptance", "items": [{"name": "Email queued", "resolved": True}, {"name": "Test added", "resolved": False}]}
    ],
    "subtasks": [{"id": "86sub1", "name": "Mail template", "status": {"status": "to do"}}],
    "attachments": [{"title": "mockup.png"}],
}

COMMENTS = [
    {"user": {"username": "pm"}, "date": "1761450000000", "comment_text": "Use the WIB timezone."},
    {"user": {"username": "faisal"}, "date": "1761449000000", "comment_text": "Which template?"},
]


def run_main(argv, env=None, api=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    patches = [mock.patch.dict(os.environ, env or {}, clear=True)]
    if api is not None:
        patches.append(mock.patch.object(fetch_tasks, "api_get", side_effect=api))
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        stack.enter_context(contextlib.redirect_stdout(stdout))
        stack.enter_context(contextlib.redirect_stderr(stderr))
        code = fetch_tasks.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def fake_api(path, token, query=None):
    if path.endswith("/comment"):
        return {"comments": COMMENTS}
    if "missing" in path:
        raise fetch_tasks.ApiError("HTTP 404: task not found")
    return TASK


class ParseReferenceTest(unittest.TestCase):
    def test_task_url(self):
        ref = fetch_tasks.parse_reference("https://app.clickup.com/t/86abc123")
        self.assertEqual(ref.key, ("86abc123", None))

    def test_task_url_with_query_and_trailing_slash(self):
        ref = fetch_tasks.parse_reference("https://app.clickup.com/t/86abc123/?comment=99")
        self.assertEqual(ref.task_id, "86abc123")
        self.assertFalse(ref.is_custom)

    def test_custom_id_url(self):
        ref = fetch_tasks.parse_reference("https://app.clickup.com/t/9012345678/PROJ-123")
        self.assertEqual(ref.key, ("PROJ-123", "9012345678"))
        self.assertEqual(ref.query(), {"custom_task_ids": "true", "team_id": "9012345678"})

    def test_url_without_scheme(self):
        self.assertEqual(fetch_tasks.parse_reference("app.clickup.com/t/86abc123").task_id, "86abc123")

    def test_bare_id(self):
        self.assertEqual(fetch_tasks.parse_reference("86abc123").task_id, "86abc123")

    def test_bare_custom_id_uses_team(self):
        self.assertEqual(fetch_tasks.parse_reference("PROJ-7", "123").key, ("PROJ-7", "123"))

    def test_bare_custom_id_without_team_fails(self):
        with self.assertRaisesRegex(fetch_tasks.InvalidReference, "team ID"):
            fetch_tasks.parse_reference("PROJ-7")

    def test_rejects_other_links(self):
        for raw in ("https://github.com/t/86abc123", "https://app.clickup.com/9012/v/li/901", "not a link!"):
            with self.subTest(raw=raw), self.assertRaises(fetch_tasks.InvalidReference):
                fetch_tasks.parse_reference(raw)


class SplitInputsTest(unittest.TestCase):
    def test_splits_and_dedupes(self):
        values = ["https://app.clickup.com/t/a1b2c3, https://app.clickup.com/t/d4e5f6", "<https://app.clickup.com/t/a1b2c3>\n86xyz99"]
        self.assertEqual(
            fetch_tasks.split_inputs(values),
            ["https://app.clickup.com/t/a1b2c3", "https://app.clickup.com/t/d4e5f6", "86xyz99"],
        )


class FormatTest(unittest.TestCase):
    def setUp(self):
        self.card = fetch_tasks.simplify({"task": TASK, "comments": COMMENTS}, timezone.utc, 20)

    def test_simplify(self):
        self.assertEqual(self.card["status"], "in progress")
        self.assertEqual(self.card["assignees"], ["faisal", "dev@example.com"])
        self.assertEqual(self.card["location"], "Sprint 13 › Backlog")
        self.assertEqual(self.card["due_date"], "2025-10-27 03:00 UTC")
        self.assertIsNone(self.card["start_date"])
        self.assertEqual([comment["author"] for comment in self.card["comments"]], ["faisal", "pm"])

    def test_hidden_folder_is_skipped(self):
        task = dict(TASK, folder={"name": "hidden", "hidden": True})
        self.assertEqual(fetch_tasks.simplify({"task": task, "comments": []}, None, 20)["location"], "Backlog")

    def test_max_comments(self):
        card = fetch_tasks.simplify({"task": TASK, "comments": COMMENTS}, None, 1)
        self.assertEqual([comment["author"] for comment in card["comments"]], ["pm"])
        self.assertIn("latest 1 of 2", fetch_tasks.format_markdown(card))
        self.assertEqual(fetch_tasks.simplify({"task": TASK, "comments": COMMENTS}, None, 0)["comments"], [])

    def test_markdown(self):
        markdown = fetch_tasks.format_markdown(self.card)
        self.assertIn("## Donation notification email", markdown)
        self.assertIn("- **Status:** in progress", markdown)
        self.assertIn("- [x] Email queued", markdown)
        self.assertIn("- [ ] Test added", markdown)
        self.assertIn("- Mail template (to do) `86sub1`", markdown)
        self.assertIn("mockup.png", markdown)
        self.assertIn("Use the WIB timezone.", markdown)

    def test_empty_description_placeholder(self):
        task = dict(TASK, markdown_description="", text_content="")
        card = fetch_tasks.simplify({"task": task, "comments": []}, None, 20)
        self.assertIn("_No description._", fetch_tasks.format_markdown(card))

    def test_http_error_message(self):
        self.assertEqual(
            fetch_tasks.explain_http_error(401, '{"err":"Token invalid","ECODE":"OAUTH_025"}'),
            "HTTP 401: token is invalid or expired (Token invalid)",
        )


class MainTest(unittest.TestCase):
    def test_missing_token(self):
        code, stdout, stderr = run_main(["86abc123"])
        self.assertEqual(code, fetch_tasks.EXIT_NO_TOKEN)
        self.assertEqual(stdout, "")
        self.assertIn("CLICKUP_API_TOKEN is not set", stderr)

    def test_no_input(self):
        with mock.patch.object(fetch_tasks.sys.stdin, "isatty", return_value=True):
            code, _, stderr = run_main([], env={"CLICKUP_API_TOKEN": "pk_test"})
        self.assertEqual(code, 2)
        self.assertIn("at least one", stderr)

    def test_many_links_with_partial_failure(self):
        code, stdout, _ = run_main(
            ["https://app.clickup.com/t/86abc123", "86abc123", "https://app.clickup.com/t/missing1", "https://example.com/x"],
            env={"CLICKUP_API_TOKEN": "pk_test"},
            api=fake_api,
        )
        self.assertEqual(code, fetch_tasks.EXIT_FAILED)
        self.assertIn("# ClickUp: 1 of 3 task(s) loaded", stdout)
        self.assertIn("HTTP 404: task not found", stdout)
        self.assertIn("not a ClickUp link", stdout)
        self.assertNotIn("pk_test", stdout)

    def test_json_output(self):
        code, stdout, _ = run_main(["86abc123", "--json"], env={"CLICKUP_API_TOKEN": "pk_test"}, api=fake_api)
        self.assertEqual(code, fetch_tasks.EXIT_OK)
        payload = json.loads(stdout)
        self.assertEqual(payload[0]["card"]["name"], "Donation notification email")

    def test_sends_custom_id_query(self):
        api = mock.Mock(side_effect=fake_api)
        run_main(["https://app.clickup.com/t/9012/PROJ-1"], env={"CLICKUP_API_TOKEN": "pk_test"}, api=api)
        path, token, query = api.call_args_list[0].args
        self.assertEqual(path, "/task/PROJ-1")
        self.assertEqual(token, "pk_test")
        self.assertEqual(query["custom_task_ids"], "true")
        self.assertEqual(query["team_id"], "9012")
        self.assertEqual(api.call_args_list[1].args[0], "/task/86abc123/comment")


if __name__ == "__main__":
    unittest.main()
