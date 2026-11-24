# Review brief: <repo> PR #<PR>

**#<PR>** "<title>" by <author>, `<head>` → `<base>`, <size>, crates <crates>.

The user wants an **in-depth, multi-pass review** of this PR, and **every claimed problem proven**. Write a report. **Do not post anything on GitHub.**

## Rules
- Read with `gh pr view <PR> -R <org>/<repo>`, `gh pr diff`, review comments
  (`gh api repos/<org>/<repo>/pulls/<PR>/comments`), `gh pr checks`. Fetch refs only:
  `git fetch origin pull/<PR>/head:refs/review/pr-<PR>` and `git fetch origin <base>`; read with
  `git show refs/review/pr-<PR>:<path>`.
- **Never check out, switch branches, stash or create a worktree in this repo.** It may hold
  someone's uncommitted work.
- Follow the repo's own rules (`CLAUDE.md`, `CLAUDE.local.md`) for where builds and tests may run.
- No commits, pushes, GitHub posts or edits to tracked files. No AI attribution.
- Keep the main context small: agents read the bulk and send back short results.

## Review depth (pick one before starting)
- **Light** — small or low-risk PRs (CI, docs, UI copy, config, refactors with no logic change):
  `/code-review` (high) + the security pass (3b). Probes only for a blocker. One round.
- **Standard** — normal feature or fix PRs: Light + an in-depth `pr-reviewer` review. Probes for
  majors and blockers.
- **Full** — money, auth, data migrations or concurrency: every pass below, probes for every
  major and wrong-result minor.
Do only the passes the chosen depth lists; skip the others and say so in the report.

## Passes (all of them)
1. **Built-in review:** run the `/code-review` skill at **high** effort on PR <PR>.
2. **In-depth review** with the `pr-reviewer` agent and the repo skills that fit.
   Order: correctness, then concurrency and failure modes, then security, then tests,
   then maintainability.
   <PR-specific focus>
3. **Self review:** run the user's `self-review` skill on this PR at **high** effort, after
   `/code-review`. Read `~/.claude/skills/self-review/SKILL.md` and follow it as written, with
   target PR <PR> and no `--comment` or `--fix`.
3b. **Security pass** (its own agent, read-only, on the PR diff and the code it calls):
   authn and authz on every new or changed endpoint (who can call it, per-user isolation, IDOR);
   input validation and limits; query injection (bound params only); secrets (never logged,
   never in URLs or errors, none in fixtures); SSRF and outbound URLs; deserialization
   strictness; replay and idempotency of money actions; rate limits and resource exhaustion;
   PII in logs and metrics; new or bumped dependencies and their licences; CI and workflow
   changes (`pull_request_target`, token scopes, untrusted input in `run:`).
   Mark each security finding **security** and prove it like the others.

## Drafts for the user to approve (before any post)
Before posting anything, write every planned GitHub comment to
`../pr-reviews/<repo>/<PR>/<date>-post/drafts.json`, then stop and tell the user. Format:
`{"pr": <PR>, "head": "<sha>", "verdict": "request_changes|approve_with_nits|approve|comment",
"summary": "<review body>", "items": [{"id": "D1", "kind": "inline|reply|summary",
"finding": "M1", "severity": "blocker|major|minor|nit|security", "proof": "proven|not proven|not testable",
"path": "<file>", "line": <n>, "in_reply_to": <comment id or null>, "body": "<text>"}]}`.
The user approves, rejects or edits each item in Review Ops, which writes `decisions.json` next
to it. Post **only** the items marked approved there, with the user's edits, after the user
confirms in this session. Never post rejected items.

## Output
- Report: `../pr-reviews/<repo>/<PR>/<today>-review.md`; check and probe files:
  `../pr-reviews/<repo>/<PR>/<today>-gates/`.
- Layout: verdict (approve / approve with nits / request changes), short summary, findings by
  severity (each: `file:line` at the PR head, problem, concrete failure case, fix, proof
  status, new/prior), a Validation section, check results, what CI does not cover, and which
  pass found each finding.
- Short sentences, plain English, no praise. Then tell the user the verdict in a few lines.
