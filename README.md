# Review Ops

A local dashboard for PR reviews that **Claude Code** sessions run for you.

It shows every PR you review or own, starts and resumes review sessions in iTerm2 tabs, and
lets you approve each planned review comment before anything is posted.

**Nothing posts to GitHub from the dashboard.** A review session posts only after you confirm
in its own tab.

## What it does

- **One list of PRs**, in sections: Pending, In progress, To post, Reviewed, Next review,
  Approved, Done.
- **New review:** fills a brief from a template and starts `claude` in a new iTerm2 tab.
- **Re-review:** resumes the same session with a recheck prompt, so it keeps its context.
- **Drafts:** the session writes its planned comments to `drafts.json`. You approve, reject
  or edit each one in the dashboard. The session posts only the approved ones.
- **PR page:** next step, findings and their status, new replies and commits, the timeline,
  and the local report.
- **My PRs:** review threads waiting for you, CI checks, and sessions that work on your PRs.
- **Linked PRs:** group PRs that make one feature across repos.

## Requirements

- macOS with **iTerm2** (sessions open in iTerm2 tabs). Turn on the Python API:
  iTerm2 → Settings → General → Magic → **Enable Python API**.
- **Python 3.11+** (standard library only; no pip packages for the app itself).
- **GitHub CLI** (`gh`), logged in: `gh auth login`.
- **Claude Code** (`claude`) on your PATH.
- Local checkouts of the repos you review, all in one folder (the "workspace").

## Install

```bash
git clone <this repo> ~/tools/review-ops
cd ~/tools/review-ops

# 1. Settings
cp config.example.toml config.toml
$EDITOR config.toml            # workspace, github_org, [repos]

# 2. A Python with the iterm2 package, for opening and focusing tabs
python3 -m venv ~/.local/share/it2venv
~/.local/share/it2venv/bin/pip install iterm2

# 3. The command
ln -s "$PWD/bin/review-ops" ~/.local/bin/review-ops   # any folder on your PATH

# 4. Run it
review-ops            # starts the server and opens http://127.0.0.1:8765
```

## Configuration

All settings are in `config.toml`. See `config.example.toml` for every key.

| Key | Meaning |
|---|---|
| `workspace` | Folder that holds your repo checkouts. |
| `pr_reviews` | Where review data goes. Default: `<workspace>/pr-reviews`. |
| `github_org` | GitHub org (or user) that owns the repos. |
| `github_user` | Your GitHub login. Empty = read it from `gh api user`. |
| `[repos]` | `github-repo-name = "local-folder-name"`, one line per repo. |
| `exclude_authors` | PR authors to leave out of the lists. |
| `port` | Dashboard port. Default 8765. |
| `iterm_python`, `iterm_tools` | The Python with `iterm2`, and the helper scripts folder. |
| `quality_remote`, `aws_profile` | Optional remote quality gates. Leave empty to turn them off. |

The config is read from `$REVIEW_OPS_CONFIG`, then `config.toml` in the app folder, then
`~/.config/review-ops/config.toml`.

## Review data

Everything lives under `pr_reviews` (default `<workspace>/pr-reviews`):

```
pr-reviews/
  registry.json            # the source of truth (PRs, rounds, sessions, links)
  STATUS.md                # generated from registry.json
  brief-template.md        # optional: your own brief template
  <repo>/<PR>/
    brief.md               # what the review session was asked to do
    <date>-review.md       # the session's report
    <date>-post/drafts.json, decisions.json
```

**Brief template.** New reviews use `pr-reviews/brief-template.md` when it exists, otherwise
`templates/brief-template.md`. Copy the starter one and change it for your team. Keep these
headings, because the app builds the passes from them: `## Passes`, the steps `1.`, `2.`,
`3.`, `3b.`, and `## Output`. Placeholders: `<org>`, `<repo>`, `<PR>`, `<title>`, `<author>`,
`<head>`, `<base>`, `<size>`, `<crates>`, `<today>`, `<PR-specific focus>`.

## Commands

```bash
review-ops                 # start if needed, then open the dashboard
review-ops start|stop|restart|status|log
review-ops list            # tracked PRs
review-ops help            # every registry command (add, update, round, posted, link, ...)
```

Review sessions use these commands to record their work, for example:

```bash
review-ops round <repo> <N> --head <sha> --verdict request_changes --kind review
review-ops posted <repo> <N>
```

## Security

- The server listens on **127.0.0.1 only** and checks the Host header.
- Every POST needs an `X-Review-Ops: 1` header and a local Origin.
- The app never stores a GitHub token. It uses your `gh` login.
- Local file links are limited to the workspace's review folders.

## Files

| File | Role |
|---|---|
| `server.py` | HTTP server and API. |
| `registry.py` | `registry.json` and `STATUS.md`. |
| `collect.py` | Read-only data: GitHub (cached), sessions, iTerm2 tabs, gate files. |
| `model.py` | Builds the list, sections, timeline and findings. |
| `actions.py` | New review, re-review, focus, link, drafts hand-off. |
| `drafts.py` | Planned comments and your decisions. |
| `address.py` | Prompts to fix review comments on your own PRs. |
| `checklist.py` | Options for new reviews and re-reviews. |
| `cli.py` | The `review-ops` registry commands. |
| `config.py` | Reads `config.toml`. |
| `static/` | The web page (plain HTML, CSS and JS; no build step). |
| `tools/` | iTerm2 helpers: `newtab.py`, `focus.py`. |
| `HANDOFF.md` | Design notes for Claude sessions that work on this code. |
