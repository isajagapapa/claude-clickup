# claude-clickup

**English** · [Bahasa Indonesia](README.id.md)

A [Claude Code](https://claude.com/claude-code) skill that reads ClickUp tasks from their links. Paste one or many card links and Claude fetches the details through the ClickUp API, summarizes them, and can turn them into a plan or code changes in whatever project you are working on.

ClickUp links need a login, so Claude cannot open them by fetching the page. This skill uses your personal API token instead.

## Features

- Reads many links at once, fetched in parallel
- Title, status, priority, assignees, start/due dates, tags, folder and list
- Description (Markdown), checklists, subtasks, attachment names, latest comments
- Normal task IDs and custom task IDs (`PROJ-123`)
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
```

To use it in a single project only, copy it to `<project>/.claude/skills/clickup` instead.

## Usage

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
Read these cards and make an implementation plan:
https://app.clickup.com/t/86abc123
https://app.clickup.com/t/86def456
```

Claude replies with a summary per card (goal, acceptance criteria, decisions from comments, open questions), plus an overview table when there are several cards.

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
- Manual: `rm -rf ~/.claude/skills/clickup`

## Contributing

```bash
python3 -m unittest discover tests -v
```

The tests run offline and do not need a token. Please add a test with every change.

## License

[MIT](LICENSE)
