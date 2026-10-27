# claude-clickup

**English** · [Bahasa Indonesia](README.id.md)

[Claude Code](https://claude.com/claude-code) skills that read ClickUp tasks from their links. Paste one or many card links and Claude fetches the details through the ClickUp API, summarizes them, and plans the work per card type (bug, feature, enhancement, ...) in whatever project you are working on.

ClickUp links need a login, so Claude cannot open them by fetching the page. This skill uses your personal API token instead.

## Features

- Reads many links at once, fetched in parallel
- Title, status, priority, assignees, start/due dates, tags, folder and list
- Description (Markdown), checklists, subtasks, attachment names, latest comments
- Normal task IDs and custom task IDs (`PROJ-123`)
- Detects the card type from its tags or title prefix (bug, feature, enhancement, refactor, research, docs, chore)
- `plan` skill: a work plan per card shaped by its type; in plan mode it only plans, otherwise it plans and executes
- Read-only: never changes anything in ClickUp
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
cp -R claude-clickup/skills/plan ~/.claude/skills/clickup-plan
sed -i.bak 's/^name: plan$/name: clickup-plan/' ~/.claude/skills/clickup-plan/SKILL.md && rm ~/.claude/skills/clickup-plan/SKILL.md.bak
```

The `plan` skill is copied as `clickup-plan` because Claude Code already has a built-in `/plan` command. Keep both folders side by side: `clickup-plan` runs the fetcher inside `clickup`.

To use them in a single project only, copy them to `<project>/.claude/skills/` instead.

## Skills

| Skill | Plugin command | Manual install | What it does |
|---|---|---|---|
| `clickup` | `/clickup:clickup <links>` | `/clickup <links>` | Reads cards and summarizes them |
| `plan` | `/clickup:plan <links>` | `/clickup-plan <links>` | Plans the work per card type, then executes it outside plan mode |

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

Claude replies with a summary per card (goal, acceptance criteria, decisions from comments, open questions), plus an overview table (card, type, status, assignee, due date) when there are several cards.

### `plan`: plan the work

```
/clickup:plan https://app.clickup.com/t/86abc123 https://app.clickup.com/t/86def456
```

Asking in a normal message ("plan these cards", "fix this card") also triggers it. For each card Claude:

1. reads the card and picks its type from the tags (falling back to a title prefix such as `[Bug]` or `fix:`, then to its own judgement, which it states),
2. explores your project so the plan names real files and tests,
3. writes a plan from the template for that type, plus open questions for the card owner,
4. shows it in the chat, and also saves it where your project's CLAUDE.md says plans go (if it says so).

What happens next depends on the Claude Code mode:

| Mode | Behaviour |
|---|---|
| Plan mode (Shift+Tab) | Only plans. Claude stops at plan approval and waits for your instruction. |
| Any other mode | Plans, warns you with "⚡ Not in plan mode: executing this plan now", then executes right away. Press Esc to stop. |

Working rules for every card:

- **Senior developer review**: architecture, edge cases, error handling, security, performance and maintainability are checked before the plan is final. A weak solution in the card gets a better proposal.
- **Best practice for your stack**: the language, framework and versions are read from your project (`composer.json`, `package.json`, `pyproject.toml`, ...), and their idioms, linters and test patterns are followed. Your project's own conventions win.
- **Clear scope**: every plan says what is in scope and what is out of scope, and execution stays inside it.
- **Recommendations**: findings outside the scope that affect it (other bugs, tech debt, security risks) are listed with their impact and a suggestion, never changed without your approval.
- **Self-fixing**: while executing, tests and linters run after each step. Failures and bugs inside the scope are fixed until green (never by weakening tests); after 3 failed attempts Claude stops and reports.

Card types and the tags that map to them (matched per word, case-insensitive; with several matching tags the first row wins):

| Type | Tags | Plan focuses on |
|---|---|---|
| `bug` | bug, bugfix, fix, hotfix, defect, error, regression, incident, perbaikan | reproduce, root cause, failing test first, minimal fix, rollback |
| `feature` | feature, feat, story, fitur | scope and acceptance criteria, design, increments, rollout |
| `enhancement` | enhancement, enhance, improvement, improve, optimization, performance, tweak, peningkatan | current vs desired, compatibility, how to measure |
| `refactor` | refactor, cleanup, tech-debt | unchanged behaviour, safety-net tests, small green steps |
| `research` | research, spike, investigation, poc, riset | question, options, timebox, recommendation |
| `docs` | docs, documentation, dokumentasi | reader, files, accuracy of samples |
| `chore` | chore, maintenance, infra, devops, config, deps, upgrade | change, impact, rollback |

The templates live in [`skills/plan/references/`](skills/plan/references/).

### Supported links

| Input | Example |
|---|---|
| Task link | `https://app.clickup.com/t/86abc123` |
| Custom task ID link | `https://app.clickup.com/t/9012345678/PROJ-42` |
| Bare task ID | `86abc123` |
| Bare custom ID | `PROJ-42` (needs `CLICKUP_TEAM_ID` set to your workspace ID) |

List, board and doc links (`/v/li/...`, `/v/b/...`, `/v/dc/...`) are not tasks. Open the task and use **Copy link**.

### Run the script directly

The skill calls a small script that you can also run yourself:

```bash
python3 skills/clickup/scripts/fetch_tasks.py https://app.clickup.com/t/86abc123 --tz Asia/Jakarta
```

| Option | Meaning |
|---|---|
| `--tz <zone>` | IANA time zone for dates, e.g. `Europe/Berlin` (default: local time) |
| `--max-comments <n>` | latest comments per task, `-1` for all (default: 20) |
| `--team-id <id>` | workspace ID for bare custom IDs (default: `$CLICKUP_TEAM_ID`) |
| `--json` | structured JSON instead of Markdown |

Exit codes: `0` all loaded, `1` some failed, `2` bad usage, `3` token missing.

## Troubleshooting

| Problem | Fix |
|---|---|
| `CLICKUP_API_TOKEN is not set` | Set the variable (step 2), then restart Claude Code and your editor completely. |
| `HTTP 401: token is invalid or expired` | Generate a new token in ClickUp and update the variable. |
| `HTTP 403` / `HTTP 404` | The token owner cannot see that task, the task was deleted, or a custom ID belongs to a different workspace. |
| `unsupported ClickUp link` | That is a list/board/doc link. Copy the task's own link. |
| `python3: command not found` on Windows | Install Python from python.org, or use `python` / `py`. Claude falls back to these automatically. |
| `unknown time zone` on Windows | Run `pip install tzdata`, or leave out `--tz`. |

## Security

- The token is read only from the `CLICKUP_API_TOKEN` environment variable and is never printed.
- The skill only sends `GET` requests to `api.clickup.com`. It cannot create, edit or delete anything.
- Card content is treated as data, not as instructions for Claude.
- Anyone with your token has your ClickUp access. If it leaks, regenerate it in ClickUp right away.

## Uninstall

- Plugin: `/plugin uninstall clickup@claude-clickup`, then `/plugin marketplace remove claude-clickup`
- Manual: `rm -rf ~/.claude/skills/clickup ~/.claude/skills/clickup-plan`

## Contributing

```bash
python3 -m unittest discover tests -v
```

The tests run offline and do not need a token. Please add a test with every change.

## License

[MIT](LICENSE)
