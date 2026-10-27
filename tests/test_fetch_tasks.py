import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
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

    def test_cu_prefixed_id(self):
        self.assertEqual(fetch_tasks.parse_reference("CU-86abc123").key, ("86abc123", None))
        self.assertEqual(fetch_tasks.parse_reference("cu-86ABC123").task_id, "86abc123")

    def test_plain_words_are_not_ids(self):
        for raw in ("please", "this", "fixes"):
            with self.subTest(raw=raw), self.assertRaises(fetch_tasks.InvalidReference):
                fetch_tasks.parse_reference(raw)

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


class ClassifyWorkTypeTest(unittest.TestCase):
    def test_from_tags(self):
        cases = {
            "Bug": "bug",
            "tech-debt": "refactor",
            "new feature": "feature",
            "Enhancement": "enhancement",
            "spike": "research",
            "documentation": "docs",
            "maintenance": "chore",
            "security": "security",
            "UI/UX": "ui",
            "qa": "test",
        }
        for tag, expected in cases.items():
            with self.subTest(tag=tag):
                self.assertEqual(fetch_tasks.classify_work_type([tag], "Some card"), (expected, f"tag: {tag}"))

    def test_priority_when_many_tags_match(self):
        self.assertEqual(fetch_tasks.classify_work_type(["enhancement", "backend", "bug"], None), ("bug", "tag: bug"))
        self.assertEqual(fetch_tasks.classify_work_type(["bug", "security"], None), ("security", "tag: security"))

    def test_from_title_prefix(self):
        self.assertEqual(fetch_tasks.classify_work_type([], "[Enhancement] Faster search"), ("enhancement", "title prefix"))
        self.assertEqual(fetch_tasks.classify_work_type(["backend"], "fix(auth): login loop"), ("bug", "title prefix"))

    def test_no_match(self):
        self.assertEqual(fetch_tasks.classify_work_type(["backend"], "Donation email: send after verify"), (None, None))
        self.assertEqual(fetch_tasks.classify_work_type([], None), (None, None))


class PlanReferencesTest(unittest.TestCase):
    def test_every_work_type_has_a_plan_template(self):
        references = SCRIPT.parents[2] / "plan" / "references"
        work_types = [work_type for work_type, _ in fetch_tasks.WORK_TYPE_ALIASES] + ["general"]
        for work_type in work_types:
            with self.subTest(work_type=work_type):
                self.assertTrue((references / f"{work_type}.md").is_file())


class PlanSkillTest(unittest.TestCase):
    def test_working_rules_present(self):
        skill = (SCRIPT.parents[2] / "plan" / "SKILL.md").read_text(encoding="utf-8")
        for rule in ("Working rules", "Senior developer review", "Best practice", "Scope", "Out of scope", "Recommendations", "Self-fixing", "Acceptance criteria → tests"):
            with self.subTest(rule=rule):
                self.assertIn(rule, skill)

    def test_never_commits_while_executing(self):
        skill = (SCRIPT.parents[2] / "plan" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Never commit or push", skill)
        self.assertNotIn("rules for tests and commits", skill)


class SkillRulesTest(unittest.TestCase):
    GIT_RULE = "Never run `git commit`, `git push`, `git tag`, `git merge` or `git rebase`, and never create branches or pull requests."

    def test_every_skill_forbids_git_writes(self):
        skills = sorted(SCRIPT.parents[2].glob("*/SKILL.md"))
        self.assertGreaterEqual(len(skills), 4)
        for skill in skills:
            with self.subTest(skill=skill.parent.name):
                text = skill.read_text(encoding="utf-8")
                self.assertIn(self.GIT_RULE, text)
                self.assertIn("Read-only", text)


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
        self.assertNotIn("**Type:**", markdown)

    def test_work_type(self):
        self.assertIsNone(self.card["work_type"])
        task = dict(TASK, tags=[{"name": "backend"}, {"name": "Bug"}])
        card = fetch_tasks.simplify({"task": task, "comments": []}, None, 20)
        self.assertEqual((card["work_type"], card["work_type_source"]), ("bug", "tag: Bug"))
        self.assertIn("- **Type:** bug (tag: Bug)", fetch_tasks.format_markdown(card))

    def test_empty_description_placeholder(self):
        task = dict(TASK, markdown_description="", text_content="")
        card = fetch_tasks.simplify({"task": task, "comments": []}, None, 20)
        self.assertIn("_No description._", fetch_tasks.format_markdown(card))

    def test_points_estimate_fields_and_relations(self):
        task = dict(
            TASK,
            points=3,
            time_estimate=9000000,
            time_spent="1800000",
            parent="86parent",
            dependencies=[
                {"task_id": "86abc123", "depends_on": "86block1"},
                {"task_id": "86waits1", "depends_on": "86abc123"},
            ],
            linked_tasks=[{"task_id": "86abc123", "link_id": "86link1"}],
            custom_fields=[
                {"name": "Severity", "type": "drop_down", "value": 1, "type_config": {"options": [{"name": "Low", "orderindex": 0}, {"name": "High", "orderindex": 1}]}},
                {"name": "Areas", "type": "labels", "value": ["a1", "a2"], "type_config": {"options": [{"id": "a1", "label": "API"}, {"id": "a2", "label": "Web"}]}},
                {"name": "Release", "type": "date", "value": "1761534000000"},
                {"name": "Reviewed", "type": "checkbox", "value": False},
                {"name": "Reviewer", "type": "users", "value": [{"username": "lead"}]},
                {"name": "Empty", "type": "short_text"},
                {"name": "Env", "type": "short_text", "value": "staging"},
            ],
        )
        card = fetch_tasks.simplify({"task": task, "comments": []}, timezone.utc, 20)
        self.assertEqual(
            card["fields"],
            [
                {"name": "Severity", "value": "High"},
                {"name": "Areas", "value": "API, Web"},
                {"name": "Release", "value": "2025-10-27 03:00 UTC"},
                {"name": "Reviewer", "value": "lead"},
                {"name": "Env", "value": "staging"},
            ],
        )
        markdown = fetch_tasks.format_markdown(card)
        self.assertIn("- **Points:** 3", markdown)
        self.assertIn("- **Estimate:** 2h 30m", markdown)
        self.assertIn("- **Time spent:** 30m", markdown)
        self.assertIn("- **Severity:** High", markdown)
        self.assertIn("- Parent: https://app.clickup.com/t/86parent", markdown)
        self.assertIn("- Waiting on: https://app.clickup.com/t/86block1", markdown)
        self.assertIn("- Blocking: https://app.clickup.com/t/86waits1", markdown)
        self.assertIn("- Linked: https://app.clickup.com/t/86link1", markdown)

    def test_no_new_sections_when_empty(self):
        markdown = fetch_tasks.format_markdown(self.card)
        for heading in ("### Fields", "### Relations", "**Points:**", "**Estimate:**"):
            self.assertNotIn(heading, markdown)

    def test_comment_replies(self):
        comments = [dict(COMMENTS[0], replies=[{"user": {"username": "dev"}, "date": "1761451000000", "comment_text": "Done.\nSee PR."}])]
        card = fetch_tasks.simplify({"task": TASK, "comments": comments}, None, 20)
        markdown = fetch_tasks.format_markdown(card)
        self.assertIn("> **dev**", markdown)
        self.assertIn("> Done.\n> See PR.", markdown)

    def test_incomplete_comments_heading(self):
        card = fetch_tasks.simplify({"task": TASK, "comments": COMMENTS, "comments_complete": False}, None, 20)
        self.assertIn("### Comments (latest 2 of 2+)", fetch_tasks.format_markdown(card))

    def test_subtask_details(self):
        details = {"86sub1": {"markdown_description": "Use MJML.", "checklists": [{"name": "Done when", "items": [{"name": "Preview ok", "resolved": False}]}]}}
        card = fetch_tasks.simplify({"task": TASK, "comments": [], "subtask_details": details}, None, 20)
        markdown = fetch_tasks.format_markdown(card)
        self.assertIn("#### Mail template (to do) `86sub1`", markdown)
        self.assertIn("Use MJML.", markdown)
        self.assertIn("- [ ] Preview ok", markdown)

        card = fetch_tasks.simplify({"task": TASK, "comments": [], "subtask_details": {"86sub1": {"error": "HTTP 403"}}}, None, 20)
        self.assertIn("_Not loaded: HTTP 403_", fetch_tasks.format_markdown(card))

    def test_attachments_include_inline_images(self):
        image = "https://t123.p.clickup-attachments.com/t123/abc/shot.png"
        task = dict(
            TASK,
            markdown_description=f"Broken button ![shot]({image}) and again ![x]({image})",
            attachments=[{"title": "mockup.png", "url": "https://t123.p.clickup-attachments.com/t123/def/mockup.png"}],
        )
        card = fetch_tasks.simplify({"task": task, "comments": []}, None, 20)
        self.assertEqual([attachment["name"] for attachment in card["attachments"]], ["mockup.png", "shot.png"])

    def test_http_error_message(self):
        self.assertEqual(
            fetch_tasks.explain_http_error(401, '{"err":"Token invalid","ECODE":"OAUTH_025"}'),
            "HTTP 401: token is invalid or expired (Token invalid)",
        )


class CommentsFetchTest(unittest.TestCase):
    def make_comments(self, count):
        return [
            {"id": str(number), "user": {"username": "pm"}, "date": str(1761450000000 + number), "comment_text": f"c{number}"}
            for number in range(count)
        ]

    def paged_api(self, comments, replies=None):
        newest_first = sorted(comments, key=fetch_tasks.comment_date, reverse=True)

        def api(path, token, query=None):
            if path.startswith("/comment/"):
                return {"comments": (replies or {}).get(path.split("/")[2], [])}
            if not path.endswith("/comment"):
                return TASK
            start = 0
            if query:
                start = next(i for i, comment in enumerate(newest_first) if comment["id"] == query["start_id"])
            return {"comments": newest_first[start:start + fetch_tasks.COMMENTS_PAGE_SIZE]}

        return mock.Mock(side_effect=api)

    def test_reads_every_page(self):
        api = self.paged_api(self.make_comments(60))
        with mock.patch.object(fetch_tasks, "api_get", api):
            comments, complete = fetch_tasks.fetch_comments("86abc123", "pk_test", -1)
        self.assertTrue(complete)
        self.assertEqual(len(comments), 60)
        self.assertEqual(len({comment["id"] for comment in comments}), 60)

    def test_stops_after_enough_comments(self):
        api = self.paged_api(self.make_comments(60))
        with mock.patch.object(fetch_tasks, "api_get", api):
            comments, complete = fetch_tasks.fetch_comments("86abc123", "pk_test", 20)
        self.assertFalse(complete)
        self.assertEqual(api.call_count, 1)
        card = fetch_tasks.simplify({"task": TASK, "comments": comments, "comments_complete": complete}, None, 20)
        self.assertIn("(latest 20 of 25+)", fetch_tasks.format_markdown(card))

    def test_replies_only_for_shown_comments(self):
        comments = self.make_comments(3)
        comments[0]["reply_count"] = 2
        comments[2]["reply_count"] = "1"
        api = self.paged_api(comments, {"2": [{"user": {"username": "dev"}, "date": "1", "comment_text": "ok"}]})
        with mock.patch.object(fetch_tasks, "api_get", api):
            result = fetch_tasks.fetch_task(fetch_tasks.TaskRef("x", "86abc123"), "pk_test", 1)
        paths = [call.args[0] for call in api.call_args_list]
        self.assertEqual(paths.count("/comment/2/reply"), 1)
        self.assertNotIn("/comment/0/reply", paths)
        newest = max(result["comments"], key=fetch_tasks.comment_date)
        self.assertEqual(newest["replies"][0]["comment_text"], "ok")


class AttachmentsTest(unittest.TestCase):
    def card(self, *attachments):
        return {"id": "86abc123", "attachments": [dict(attachment) for attachment in attachments]}

    def test_saves_images_and_skips_the_rest(self):
        card = self.card(
            {"name": "shot.png", "url": "https://t1.p.clickup-attachments.com/t1/a/shot.png"},
            {"name": "notes.docx", "url": "https://t1.p.clickup-attachments.com/t1/b/notes.docx"},
            {"name": "evil.png", "url": "https://evil.example.com/evil.png"},
            {"name": "plain.png", "url": "http://t1.p.clickup-attachments.com/t1/c/plain.png"},
        )
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(fetch_tasks, "http_download", return_value=b"PNG") as download:
                fetch_tasks.save_attachments(card, "pk_test", directory)
            saved = card["attachments"][0]["path"]
            self.assertEqual(Path(saved).read_bytes(), b"PNG")
            self.assertEqual(Path(saved).name, "86abc123-1-shot.png")
        download.assert_called_once()
        self.assertEqual(card["attachments"][1]["skipped"], "not an image or PDF")
        self.assertEqual(card["attachments"][2]["skipped"], "not hosted by ClickUp")
        self.assertEqual(card["attachments"][3]["skipped"], "not hosted by ClickUp")
        markdown_line = fetch_tasks.format_markdown(dict(fetch_tasks.simplify({"task": TASK, "comments": []}, None, 20), attachments=card["attachments"]))
        self.assertIn("shot.png → saved to", markdown_line)
        self.assertIn("notes.docx (not downloaded: not an image or PDF)", markdown_line)

    def test_download_error_is_reported(self):
        card = self.card({"name": "shot.png", "url": "https://t1.p.clickup-attachments.com/t1/a/shot.png"})
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(fetch_tasks, "http_download", side_effect=fetch_tasks.ApiError("HTTP 404")):
                fetch_tasks.save_attachments(card, "pk_test", directory)
        self.assertEqual(card["attachments"][0]["skipped"], "HTTP 404")

    def test_token_only_after_auth_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"PDF"
        denied = urllib.error.HTTPError("https://x", 403, "Forbidden", {}, io.BytesIO(b""))
        with mock.patch.object(fetch_tasks.ATTACHMENT_OPENER, "open", side_effect=[denied, response]) as opener:
            data = fetch_tasks.http_download("https://t1.p.clickup-attachments.com/t1/a/doc.pdf", "pk_test")
        self.assertEqual(data, b"PDF")
        first, second = (call.args[0] for call in opener.call_args_list)
        self.assertIsNone(first.get_header("Authorization"))
        self.assertEqual(second.get_header("Authorization"), "pk_test")

    def test_too_large(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"x" * (fetch_tasks.MAX_ATTACHMENT_BYTES + 1)
        with mock.patch.object(fetch_tasks.ATTACHMENT_OPENER, "open", return_value=response):
            with self.assertRaisesRegex(fetch_tasks.ApiError, "larger than"):
                fetch_tasks.http_download("https://t1.p.clickup-attachments.com/t1/a/big.png", "pk_test")

    def test_redirect_drops_token(self):
        handler = fetch_tasks._DropAuthOnRedirect()
        request = urllib.request.Request("https://t1.p.clickup-attachments.com/a.png", headers={"Authorization": "pk_test"})
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://bucket.s3.amazonaws.com/a.png")
        self.assertIsNone(redirected.get_header("Authorization"))


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
        self.assertIn("work_type", payload[0]["card"])

    def test_sends_custom_id_query(self):
        api = mock.Mock(side_effect=fake_api)
        run_main(["https://app.clickup.com/t/9012/PROJ-1"], env={"CLICKUP_API_TOKEN": "pk_test"}, api=api)
        path, token, query = api.call_args_list[0].args
        self.assertEqual(path, "/task/PROJ-1")
        self.assertEqual(token, "pk_test")
        self.assertEqual(query["custom_task_ids"], "true")
        self.assertEqual(query["team_id"], "9012")
        self.assertEqual(api.call_args_list[1].args[0], "/task/86abc123/comment")

    def test_with_subtasks_and_attachments_dir(self):
        api = mock.Mock(side_effect=fake_api)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(fetch_tasks, "save_attachments") as save:
                code, _, _ = run_main(["86abc123", "--with-subtasks", "--attachments-dir", directory], env={"CLICKUP_API_TOKEN": "pk_test"}, api=api)
        self.assertEqual(code, fetch_tasks.EXIT_OK)
        self.assertIn("/task/86sub1", [call.args[0] for call in api.call_args_list])
        self.assertEqual(save.call_args.args[1:], ("pk_test", directory))


if __name__ == "__main__":
    unittest.main()
