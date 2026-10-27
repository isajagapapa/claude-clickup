# claude-clickup

**English** · [Bahasa Indonesia](README.id.md)

[Claude Code](https://claude.com/claude-code) skills that read ClickUp tasks from their links. Paste one or many card links and Claude fetches the details through the ClickUp API, summarizes them, plans the work per card type (bug, feature, enhancement, ...) in whatever project you are working on, lists your open tasks, and drafts the branch name, commit message and PR description for a card.

ClickUp links need a login, so Claude cannot open them by fetching the page. This skill uses your personal API token instead.

## Features

- Reads many links at once, fetched in parallel
- Title, status, priority, assignees, start/due dates, tags, folder and list
- Description (Markdown), checklists, subtasks (optionally with their description and checklists), comments with their thread replies
- Custom fields (story points, severity, sprint, ...), points, time estimate and time spent
- Relations: parent task, waiting on, blocking, linked tasks
- Downloads screenshots, mockups and PDFs (attachments and images in the description) so Claude can look at them
- Normal task IDs, custom task IDs (`PROJ-123`) and `CU-86abc123` as used in branch names
- Detects the card type from its tags or title prefix (security, bug, feature, enhancement, refactor, ui, research, docs, test, chore)
- `plan` skill: a work plan per card shaped by its type; in plan mode it only plans, otherwise it plans and executes
- `mine` skill: your open tasks, or the tasks of a list, sorted by due date with a suggestion for today
- `pr` skill: branch name, commit message and PR description from the card and your changes
- Read-only: never changes anything in ClickUp, and never commits or pushes anything in your repository
- Python standard library only, no packages to install

## Requirements

- Claude Code
- Python 3.9 or newer (`python3 --version`)
- A ClickUp account with access to the tasks you want to read

## 1. Get a ClickUp API token

1. In ClickUp, click your avatar → **Settings** → **Apps**.
2. Under **API Token**, click **Generate** (or **Copy** if you already have one). It starts with `pk_`.

The token can do everything your account can do. Keep it secret.

## 2. Save the token as an environment variable

Do not paste the token into the chat or commit it to a repository.

**macOS / Linux** (zsh is the macOS default; use `~/.bashrc` for bash):

```bash
echo 'export CLICKUP_API_TOKEN="pk_your_token"' >> ~/.zshrc
source ~/.zshrc
```

**Windows** (PowerShell or Command Prompt):

```powershell
setx CLICKUP_API_TOKEN "pk_your_token"
```

Then fully restart your terminal, editor (VS Code, JetBrains) and Claude Code so they pick up the new variable.

Check it without printing the token:

```bash
[ -n "$CLICKUP_API_TOKEN" ] && echo "token set" || echo "token missing"
```

## 3. Install

### Option A: plugin marketplace (recommended)

Inside Claude Code:

```
/plugin marketplace add isajagapapa/claude-clickup
/plugin install clickup@claude-clickup
```

Update later with `/plugin marketplace update claude-clickup`.

### Option B: manual copy

```bash
git clone https://github.com/isajagapapa/claude-clickup.git
mkdir -p ~/.claude/skills
cp -R claude-clickup/skills/clickup ~/.claude/skills/clickup
for skill in plan mine pr; do
  cp -R "claude-clickup/skills/$skill" ~/.claude/skills/clickup-$skill
  sed -i.bak "s/^name: $skill\$/name: clickup-$skill/" ~/.claude/skills/clickup-$skill/SKILL.md && rm ~/.claude/skills/clickup-$skill/SKILL.md.bak
done
```

The `plan`, `mine` and `pr` skills are copied with a `clickup-` prefix because Claude Code already has a built-in `/plan` command. Keep all folders side by side: they run the scripts inside `clickup`.

To use them in a single project only, copy them to `<project>/.claude/skills/` instead.

## Skills

| Skill | Plugin command | Manual install | What it does |
|---|---|---|---|
| `clickup` | `/clickup:clickup <links>` | `/clickup <links>` | Reads cards and summarizes them |
| `plan` | `/clickup:plan <links>` | `/clickup-plan <links>` | Plans the work per card type, then executes it outside plan mode |
| `mine` | `/clickup:mine [<list links>]` | `/clickup-mine` | Lists your open tasks (or a list's tasks) and suggests what to do today |
| `pr` | `/clickup:pr [<links>]` | `/clickup-pr` | Suggests a branch name, commit message and PR description for a card |

None of the skills ever runs `git commit`, `git push` or opens a pull request: every git write is left to you.

### `clickup`: read cards

Invoke the skill and pass the links:

```
/clickup:clickup https://app.clickup.com/t/86abc123
```

(With the manual install the command is `/clickup`.)

Many links at once, separated by spaces, commas or new lines:

```
/clickup:clickup https://app.clickup.com/t/86abc123 https://app.clickup.com/t/86def456
https://app.clickup.com/t/9012345678/PROJ-42
```

You can also just paste links in a normal message, and Claude picks the skill up on its own:

```
What do these cards ask for?
https://app.clickup.com/t/86abc123
https://app.clickup.com/t/86def456
```

Claude replies with a summary per card (goal, acceptance criteria, decisions from comments, open questions), plus an overview table (card, type, status, assignee, due date) when there are several cards. Screenshots and mockups on the card are downloaded to a temporary folder so Claude can look at them too.

### `plan`: plan the work

```
/clickup:plan https://app.clickup.com/t/86abc123 https://app.clickup.com/t/86def456
```

Asking in a normal message ("plan these cards", "fix this card") also triggers it. For each card Claude:

1. reads the card and picks its type from the tags (falling back to a title prefix such as `[Bug]` or `fix:`, then to its own judgement, which it states),
2. explores your project so the plan names real files and tests,
3. writes a plan from the template for that type, plus open questions for the card owner,
4. maps every acceptance criterion to the test that proves it,
5. shows it in the chat, and also saves it where your project's CLAUDE.md says plans go (if it does not say, Claude offers to save it under `.claude/plans/`).

What happens next depends on the Claude Code mode:

| Mode | Behaviour |
|---|---|
| Plan mode (Shift+Tab) | Only plans. Claude stops at plan approval and waits for your instruction. |
| Any other mode | Plans, warns you with "⚡ Not in plan mode: executing this plan now", then executes right away. Press Esc to stop. Changes are left uncommitted for you to review. |

Working rules for every card:

- **Senior developer review**: architecture, edge cases, error handling, security, performance and maintainability are checked before the plan is final. A weak solution in the card gets a better proposal.
- **Best practice for your stack**: the language, framework and versions are read from your project (`composer.json`, `package.json`, `pyproject.toml`, ...), and their idioms, linters and test patterns are followed. Your project's own conventions win.
- **Clear scope**: every plan says what is in scope and what is out of scope, and execution stays inside it.
- **Recommendations**: findings outside the scope that affect it (other bugs, tech debt, security risks) are listed with their impact and a suggestion, never changed without your approval.
- **Self-fixing**: while executing, tests and linters run after each step. Failures and bugs inside the scope are fixed until green (never by weakening tests); after 3 failed attempts Claude stops and reports.

Card types and the tags that map to them (matched per word, case-insensitive; with several matching tags the first row wins):

| Type | Tags | Plan focuses on |
|---|---|---|
| `security` | security, vulnerability, vuln, cve, keamanan | threat, attack surface, exploit test first, root-cause fix, exposure |
| `bug` | bug, bugfix, fix, hotfix, defect, error, regression, incident, perbaikan | reproduce, root cause, failing test first, minimal fix, rollback |
| `feature` | feature, feat, story, fitur | scope and acceptance criteria, design, increments, rollout |
| `enhancement` | enhancement, enhance, improvement, improve, optimization, performance, tweak, peningkatan | current vs desired, compatibility, how to measure |
| `refactor` | refactor, cleanup, tech-debt | unchanged behaviour, safety-net tests, small green steps |
| `ui` | ui, ux, design, layout, styling, desain, tampilan | current vs mockup, reused components, states, responsive and accessible |
| `research` | research, spike, investigation, poc, riset | question, options, timebox, recommendation |
| `docs` | docs, documentation, dokumentasi | reader, files, accuracy of samples |
| `test` | test, testing, qa, e2e, coverage, pengujian | cases, approach, flaky causes, tests that really fail |
| `chore` | chore, maintenance, infra, devops, config, deps, upgrade | change, impact, rollback |

The templates live in [`skills/plan/references/`](skills/plan/references/).

### `mine`: your open tasks

```
/clickup:mine
/clickup:mine https://app.clickup.com/9012345678/v/li/901234567890
```

Asking "what are my ClickUp tasks?" or "what should I work on today?" also triggers it. Without links Claude lists the open tasks assigned to you; with list or board links it lists the open tasks there. You get them grouped as overdue, due soon and the rest, plus up to three suggestions for today. With several workspaces, set `CLICKUP_TEAM_ID` (or Claude asks which one).

### `pr`: branch, commit and PR text

```
/clickup:pr https://app.clickup.com/t/86abc123
```

Without a link Claude looks for `CU-<id>` or a custom ID in the current branch name. It reads the card and your changes with read-only git commands (`git status`, `git diff`, `git log`) and suggests:

- a branch name like `feat/CU-86abc123-donation-email`, which ClickUp's GitHub, GitLab and Bitbucket integrations link to the card,
- a commit message in your project's style (Conventional Commits by default),
- a PR title and description with the card's acceptance criteria ticked only where your diff covers them.

You copy and run the git commands yourself; the skill never commits, pushes or opens the PR.

### Supported links

| Input | Example |
|---|---|
| Task link | `https://app.clickup.com/t/86abc123` |
| Custom task ID link | `https://app.clickup.com/t/9012345678/PROJ-42` |
| Bare task ID | `86abc123` |
| Task ID as in branch names | `CU-86abc123` |
| Bare custom ID | `PROJ-42` (needs `CLICKUP_TEAM_ID` set to your workspace ID) |

List and board links (`/v/li/...`, `/v/b/...`) are not tasks: use them with `mine`. Doc links (`/v/dc/...`) are not supported. For a single task, open it and use **Copy link**.

### Run the script directly

The skills call two small scripts that you can also run yourself:

```bash
python3 skills/clickup/scripts/fetch_tasks.py https://app.clickup.com/t/86abc123 --tz Asia/Jakarta
python3 skills/clickup/scripts/list_tasks.py
```

`fetch_tasks.py` reads cards:

| Option | Meaning |
|---|---|
| `--tz <zone>` | IANA time zone for dates, e.g. `Europe/Berlin` (default: local time) |
| `--max-comments <n>` | latest comments per task, `-1` for all (default: 20) |
| `--with-subtasks` | also load the description and checklists of up to 25 subtasks |
| `--attachments-dir <dir>` | download image and PDF attachments (also images in the description) into that folder, up to 10 MB each |
| `--team-id <id>` | workspace ID for bare custom IDs (default: `$CLICKUP_TEAM_ID`) |
| `--json` | structured JSON instead of Markdown |

`list_tasks.py` lists the open tasks assigned to you, or of the list and view links you pass:

| Option | Meaning |
|---|---|
| `--team-id <id>` | workspace for your tasks (default: `$CLICKUP_TEAM_ID`, or your only workspace) |
| `--include-closed` | also list closed tasks |
| `--limit <n>` | maximum tasks per source (default: 100) |
| `--tz <zone>`, `--json` | as above |

Exit codes: `0` all loaded, `1` some failed, `2` bad usage, `3` token missing.

## Troubleshooting

| Problem | Fix |
|---|---|
| `CLICKUP_API_TOKEN is not set` | Set the variable (step 2), then restart Claude Code and your editor completely. |
| `HTTP 401: token is invalid or expired` | Generate a new token in ClickUp and update the variable. |
| `HTTP 403` / `HTTP 404` | The token owner cannot see that task, the task was deleted, or a custom ID belongs to a different workspace. |
| `unsupported ClickUp link` | That is a list/board/doc link. Copy the task's own link, or use `mine` for lists and boards. |
| `pick a workspace with --team-id` | Your token sees several workspaces. Set `CLICKUP_TEAM_ID` to the one you want. |
| `python3: command not found` on Windows | Install Python from python.org, or use `python` / `py`. Claude falls back to these automatically. |
| `unknown time zone` on Windows | Run `pip install tzdata`, or leave out `--tz`. |

## Security

- The token is read only from the `CLICKUP_API_TOKEN` environment variable and is never printed.
- The skills only send `GET` requests to `api.clickup.com`, plus `GET` requests to ClickUp's attachment hosts when downloading attachments. They cannot create, edit or delete anything in ClickUp.
- The token goes to an attachment host only after it answers 401/403, and it is never forwarded on a redirect.
- The skills never run `git commit`, `git push`, `git tag`, `git merge` or `git rebase`, and never create branches or pull requests.
- Card content is treated as data, not as instructions for Claude.
- Anyone with your token has your ClickUp access. If it leaks, regenerate it in ClickUp right away.

## Uninstall

- Plugin: `/plugin uninstall clickup@claude-clickup`, then `/plugin marketplace remove claude-clickup`
- Manual: `rm -rf ~/.claude/skills/clickup ~/.claude/skills/clickup-plan ~/.claude/skills/clickup-mine ~/.claude/skills/clickup-pr`

## Contributing

```bash
python3 -m unittest discover tests -v
```

The tests run offline and do not need a token. Please add a test with every change.

## License

[MIT](LICENSE)
