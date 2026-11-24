> Notes for Claude sessions that work on this code. People should start with README.md.

**Feature freeze since 2026-11-19.** Add no new features. The 2026-11-20 UI revamp already dropped the Description and Files changed tabs; on 2026-11-26 check that nothing is missed.

You are taking over work on "Review Ops", a local web dashboard for the PR reviews that
Claude sessions run for me. It is built and running. Find the app and read its code before
you change anything.

## What it is made of
- Python 3 standard library + plain HTML/JS/CSS (no npm, no pip).
- server.py: HTTP server on 127.0.0.1:8765 only. It checks the Host header; every POST needs
  an "X-Review-Ops: 1" header and a local Origin. Flag: --dry-run.
- registry.py: the registry JSON is the source of truth. Every save writes the status
  markdown file again.
- collect.py: read-only data:
  - gh GraphQL (one call per PR, cached on disk; after 3 min the old answer is served at once and refreshed in a background thread; Refresh forces a fresh fetch) and `gh pr list` (open PRs + `review-requested:@me`)
  - ps + Claude session transcripts (last activity, last message)
  - iTerm tabs (focus tool --list)
  - gate file parsing
  - `quality-remote status --pr N` (25 s timeout)
- model.py: summaries, "needs you" items, the section of each PR, a timeline grouped by each
  review of ours, threads, findings. Leaves out PRs by the authors in `exclude_authors` (config).
- actions.py:
  - Re-review: resume the session in a new iTerm tab with a recheck prompt. Always enabled.
    If the session is already open, it does NOT open a second copy: it shows the prompt with
    Copy prompt and Focus tab buttons. A warning under the actions says when the PR is already
    approved and when the session is open.
  - New review: fill the brief template, start `claude --session-id <uuid>`. The dialog is a shared
    picker (makePicker): repo menu + the repo's open PRs (/api/openprs/<repo>: review requests
    first, then newest; PRs that already have a session are greyed out) + one search box. No
    number field: typing a number that is not in the list offers "Use this number".
  - Link / Unlink: link groups (one feature across repos). reg["links"] = [{id, name, prs}].
    A PR is in one group at most; linked PRs need not be tracked. PR page has a "Linked PRs" box
    (members with verdict/state, "Link a PR", "Unlink this PR", and suggestions: open PRs in other
    repos with the same branch name, and PRs named in the description). Sidebar cards show
    "🔗 name". Re-review prompts and new briefs get a "## Linked PRs" block. STATUS.md lists groups.
  - Focus tab
  - Every action supports dry_run.
- cli.py: registry CLI. static/: index.html, app.js, md.js (safe markdown), style.css.
- Launcher command `review-ops`: start [--dry-run] | stop | restart | open | status | log,
  plus the registry commands below.

## Registry CLI
review-ops list | show <repo> <N>
review-ops add|update <repo> <N> [--title --author --session --tab --verdict --waiting --notes --previous M]
  (update also takes --verdict-note --posted-note; --session adds a new session id)
review-ops round <repo> <N> --head SHA [--verdict V] [--kind review|recheck] [--passes a,b] [--report REL] [--gates REL] [--pr M]
review-ops posted <repo> <N> [--review-id ID --comments K --state S]   (no id: read ours from gh)
review-ops archive|unarchive <repo> <N>   (take off / put back on the list)
review-ops link "<feature>" <repo> <N> <repo> <N> [...] | unlink <repo> <N> | links
review-ops remove | status | import | help
Verdicts: request_changes, approve_with_nits, approved, not_reviewed, on_hold, in_progress

## UI decisions the user made (keep them)
- Sidebar has a "Mine | All" switch. Mine = tracked PRs + open PRs that ask me (`github_user` in the config) for a review.
- Seven sections, in this order, each one can be opened and closed (kept in localStorage):
  - Pending: not reviewed yet
  - In progress: in the review process (session working, box run, in_progress; or waiting for my answer)
  - To post: the latest round is finished but not on GitHub (model.unposted(): no real review of
    ours yet, or none at the round's head and none sent after the round was recorded). It comes
    after "working / box run" and before "waiting for my answer". Chip: "Not posted yet".
  - Reviewed: reviewed, no reply or fix from the author yet (also closed without a merge)
  - Next review: reviewed, and new commits / replies / a new review request need another review
  - Approved: approved by us (with or without nits), not merged, nothing new
  - Done: merged only (starts closed)
- Sidebar filters: search box (number, title, author), Repo, Author and Feature (link group)
  menus, Clear button; kept in localStorage. Any filter opens every section.
- Merged (and closed) PRs have a "Remove" button (card and PR page, with a confirm dialog).
  It sets archived: true in the registry; nothing changes on GitHub. Archived PRs are left out
  of the sidebar, the refresh and STATUS.md.
- "All" adds a folded "Other open PRs, active in 14 days" list.
- Sidebar card: title, repo #N · author, and at most 3 chips: verdict, round (R1), and ONE
  "needs you" chip (the most important). A small dot shows an open session (green = working,
  blue = idle). No walls of chips.
- PR page (UI revamp 2026-11-20; old files in static.bak-20261120/): title + one meta line, then a
  "Next step" panel: ONE next action in plain words with its button (app.js reviewNext / myNext:
  session asks you → Focus; drafts pending → Open drafts; not posted → Focus or Drafts; working →
  wait; new replies/commits → Re-review; merged → Remove), warnings, and 4 KPIs (Verdict · R,
  Open findings of latest list, Waiting on, New since our look). Under it a facts line (Session,
  Box, CI, Merge) with the other buttons (Re-review, Focus tab, Refresh, GitHub ↗, Diff ↗).
  Tabs: Overview, Drafts, Comments, Timeline, Report & gates (gates are one fold per round,
  newest open, above the report). My PRs: Overview, Drafts, Comments, Timeline.
  Overview = two columns (one below 1350 px): main = Needs you, Findings, New replies, New
  commits (only when any); side = Review state, Session, Linked PRs, Description (folded).
  My PR overview: main = Threads waiting for you, Work sessions; side = Reviewers, Checks,
  Linked PRs, Description.
  Dropped: the Description tab (now a fold) and the Files changed tab (use Diff ↗ on GitHub).
  Old hash links #/repo/N/gates|description|files map to report / overview (TAB_ALIAS).
- Keys: / search (Enter opens the first match, Esc clears), j/k next/previous PR in the open
  sidebar sections, 1-5 tabs, r refresh this PR. The open PR scrolls into view in the sidebar.
- style.css is one token set (spacing --s1..--s6, type --fs-xs..--fs-xl, colours with meaning:
  ok green, bad red, warn amber, info blue, sec purple; "you" = solid accent), light and dark.
- Updates: the sidebar list reloads every 60 s from the server cache (GitHub cached 3 min); the open
  PR page and box status update only on Refresh.
- Local review files are listed under Report & gates and open in a dialog (gate folder links too).
- Timeline: one fold per checkpoint (each review of ours), with a summary
  ("After it: N commits · N replies · N rounds"). The newest is open. Expand all / Collapse all.
- Overview has a Findings box per review round (newest open, older folded), under "Needs you".
  Each finding shows two states: the latest recheck table in the report (any table with a
  "Status" column; match by id such as M1/m2/R2-M1, then by shared words), and our GitHub thread
  (match by file and line within 3 lines; Open/Resolved, "<author> replied"). Summary chips:
  "N of M still open", "GitHub: X resolved, Y open", "Recheck: A done, B open".
  "Show only open" hides findings that are resolved on GitHub or fixed/answered/kept in a recheck.
- Short, plain English labels.

## Review checklist (New review and Re-review dialogs)
- checklist.py: GROUPS (Review passes, Quality gates on the box, Proof, Output, Linked PRs) with
  bool / text / number / select items, defaults, and the pass name each item records.
  Re-review only: "Recheck earlier findings" and "Draft thread replies".
- Values: built-in defaults → the user's last choices per repo and kind
  (reg["checklist_defaults"][repo]["new"|"rereview"], saved on a real run, cleared by
  POST /api/checklist/reset) → automatic rules: --db on when the PR touches migrations / .sql /
  db/ / store/ / repository / sqlx files (with a note); "linked PRs agree" on only when the PR is
  in a link group (disabled otherwise). GET /api/checklist?repo&number&kind.
- Gates map to `quality-remote pr N --tool all|both|crap|mutate|cmut` (+ --base main, --db,
  --features, --workers for mutator); a pair with no single tool value becomes two runs; none
  ticked = "no box gates". Effort goes to `claude --effort` and to the skill steps.
- New review: the brief is the template's header and Output, with "## Passes" rebuilt from the
  ticked steps (template text for 1, 2, 3, 3b; gates and proof steps written from the options),
  posting/approval lines, the `review-ops round ... --passes <list>` line, and the linked-PR
  block when ticked. A new run writes brief.md again (old one kept as brief.md.prev).
- Re-review: the "What to do" steps are built from the options (security text copied from 3b).
- The dialog: one fieldset per group with a group checkbox (mixed state when partly ticked),
  live dry-run preview of exactly what is sent, Reset to defaults link.
- p["planned"] = {kind, passes, options} is saved on a real run; `review-ops round` uses those
  passes when --passes is not given and moves the options onto the round. Gates and Timeline
  tabs show the round's passes ("Ran: ...").

## Drafts (approve each planned comment before posting)
- Contract (brief template, "Drafts for the user to approve"): the session writes
  pr-reviews/<repo>/<PR>/<date>-post/drafts.json {pr, head, verdict, summary, items[{id, kind
  inline|reply|summary, finding, severity blocker|major|minor|nit|security, proof, path, line,
  in_reply_to, body}]}. The dashboard writes decisions.json next to it (atomic) {head, verdict,
  summary, items[{id, decision approved|rejected|pending, body if edited, note}], decided_at}.
- drafts.py: load (newest *-post/drafts.json; diff context ±3 lines from the PR patch; the thread
  for replies by comment id), save (validates ids, decisions, verdict; records counts on the
  latest round as round["decisions"]), send_prompt (warns and blocks on a moved head or pending
  items; force allowed), ask_prompt (for reviews with no drafts file).
- Drafts tab (reviewing PRs): verdict select, editable summary with preview, cards with severity
  (security purple), kind, finding, proof labels, file:line, diff context, thread, body with Edit +
  live preview, Approve / Reject (click again = pending), note; filters by severity and decision;
  bulk: approve all proven, approve all shown, reject all nits, set all to pending. Saves on every
  change ("Saved"). Title by job: "Planned review comments" (PRs we review) or "Planned
  replies" (my PRs; load() returns job review|replies).
- Step bar (2026-11-20) at the top of the Drafts tab: Review (N pending) → Sent to session →
  Confirm in <tty> → Posted, with ONE main button per step: "Approve the N pending first"
  (disabled; small "Send anyway…"), then "Copy prompt and go to <tty>" when the session is open
  (copies, POST /api/action/drafts_copied records the send and focuses the tab) or "Send N
  approved to the session" when closed (dialog with the command → resume in a new tab), then
  "Waiting for you to confirm in <tty> · Focus" + "Check again" (refresh), then "Posted ✓ view on
  GitHub". Line under it: "Nothing posts from here. The session posts after you confirm in its tab."
  The dashboard approval is never the final confirmation; the session still asks.
- sent.json next to decisions.json {at, head, how new_tab|copied, tty, counts}: written by a real
  Send (actions.send_drafts, only when the tab opened) or by drafts_copied. It goes stale when
  drafts.json or decisions.json change after it (you must send again).
- Posted = our reviews/replies on GitHub after sent.at (or, with no sent.json, after drafts.json
  was written; cached gh data), else a posted*.json the session wrote in the -post folder, newer
  than drafts.json (formats vary; a pullrequestreview link is preferred). Decisions are not
  checked for "posted" (sessions save them around posting); the Posted step has "Send again…". load() returns session {kind
  review|work, session, open, tty}, sent, posted. quick_counts adds sent/posted flags, and the
  PR page "Next step" panel uses them ("Confirm in the session tab" / "The session posted the
  drafts"). The To post section rule itself is unchanged.
- A drafts file newer than our last GitHub review puts the PR in To post; its card chip shows
  "N pending · N ok · N no". Brief builder keeps template parts between Passes and Output.
- Test with REVIEW_OPS_PR_REVIEWS=<temp copy> python3 server.py --port 8766 --dry-run.

## My PRs view
- Sidebar top switch: "Reviewing | My PRs" (kept in localStorage; `?mode=mine` in the URL opens it).
- My PRs = PRs I authored in the 3 repos (`gh pr list --author @me --state all`): open ones, plus
  merged in the last 14 days. /api/myprs; model.my_state() per PR from the cached GraphQL data.
- Sections: To fix or answer (changes asked and no push since, or an open thread where a reviewer
  spoke last), Waiting for re-review (pushed after every change request), Waiting for review,
  Draft, Approved, Merged (last 14 days, starts closed). Bots are left out of reviewers.
- Card chips: reviewers (✓ approvals ✗ changes ⏳ waiting) + one of: "N to answer", CI failing,
  "N open threads".
- PR page for my PR: Next step panel (answer threads / fix changes / CI failing → Address
  comments; else waiting or approved), KPIs (Approvals, Changes asked, Waiting on, To answer),
  facts line (Session, CI, Merge) with Focus/Resume, Address comments, Start new session, Refresh,
  GitHub, Diff. No review actions.

- Work sessions for my PRs: collect.live_sessions() reads ~/.claude/sessions/<pid>.json (pid →
  sessionId, cwd, busy/idle; only alive pids) — also used for review sessions now.
  collect.find_sessions() greps transcripts (the repo's project folders incl. worktrees,
  such as a `-t99` worktree, last 30 days) for the PR link and branch name, ranks by mentions (branch + 3 × link),
  leaves out review sessions; cached 10 min and warmed in the background when My PRs loads.
  PR page: main button Focus tab (session open) or Resume session, plus Start new session; a
  "Work sessions" box (top 3, rest folded) with Focus / Resume / Use this one. The pick is saved in
  reg["mine"]["<repo>#<N>"]. Resume/Start prompts list reviewers, threads to answer, failing CI,
  and say: do not push, post, resolve or ask for review until I confirm. An open session gets
  Copy prompt + Focus tab instead of a second copy.

- Address comments (My PR page; address.py): dialog lists every open thread and review body from
  others (no bots), grouped by reviewer then file, with file:line, text, last reply, outdated,
  "waiting for you", "sent …" / "new since last send"; checkbox per thread, group checkboxes per
  reviewer and per file (mixed state), Tick all / Untick all / Only waiting for you; failing CI
  checks as items. Default: open threads where a reviewer spoke last, all review bodies, all failing
  checks. Options: validate first, fix test first, gates on the box, fix CI (on when CI fails),
  draft replies, write replies to <date>-post/drafts.json (Drafts tab works on my PRs too; Send to
  session uses the work session), and "push and post only after I confirm" (locked on). Live
  dry-run preview of the exact prompt (plan first, validate before fixing, never push / post /
  resolve / re-request review before I confirm, gates only via quality-remote, no AI attribution).
  Gates step uses WORKING-TREE mode in the session's folder (fixes are local and unpushed):
  `quality-remote start --repo <session folder> --base origin/<PR base> --tool all [--db]
  [--features] <changed files>`, then `wait` / `fetch --repo <folder>`. `pr N` mode is only for
  reviews of other people's PRs (it tests the PR head on GitHub). The prompt makes the session check
  that the folder is on the PR branch first, and stop if not.
  Flow: open session → Copy prompt (records the send) + Focus tab; closed → resume; none → start in
  the same folder. Options saved per repo (reg["address_defaults"]); each send recorded in
  reg["mine"]["<repo>#<N>"]["address"] (time, thread ids, check ids, options, how, tty); the page
  shows "Sent to session … (N comments …)".
- Work-session order: the chosen session first, then by match strength. Being open does not win
  (a planning session that names every PR is often open).

## Layout rules (no sideways page scroll)
- Checked 2026-11-20 after the revamp at 1280 and 1920 px: #1245 overview and report, #1219
  overview, comments and timeline, #1239 drafts, my #1235: all equal. Findings table uses
  table-layout fixed with a colgroup so its headers do not squeeze.
- .layout uses `340px minmax(0, 1fr)`; cards, grids, flex children and dialogs have min-width: 0;
  text, paths, URLs and ids use overflow-wrap: anywhere.
- Code: `pre` keeps white-space: pre with its own overflow-x: auto; diff tables sit in
  `<div class="codewrap">` (own scroll bar). "Wrap lines" checkbox in the top bar (localStorage
  ro-wrap) switches code to pre-wrap.
- Test hook: add `checkwidth=1` to the URL; the title becomes "W scrollWidth/clientWidth M main…"
  and the first 3 file diffs open. Read it with Brave `--headless=new --dump-dom` (it does not save
  anything). Checked at 1280 and 1600 px on #1235 Drafts, #1219 Files changed, #1245 Report,
  #1235 Comments: all equal.

## Hard rules
- Never post, approve, resolve, merge or push on GitHub from the dashboard. That stays inside the
  review session, after I confirm there.
- Do not change the review sessions, their transcripts, the repos (no git state changes, no
  checkouts) or GitHub. Write only the registry, the status file and the app's own files.
- Never start heavy box runs (crapper/mutator/cargo-mutants) from the dashboard or on this Mac.
  Only `quality-remote status`, and only on Refresh. Exit 4 with "SSO" in stderr means SSO expired:
  show "run aws sso login" (with `--profile <aws_profile>` when the config sets one).
- No secrets in the UI or logs.
- Never commit, push or open a PR unless I say so. No AI attribution anywhere.
- In testing, do not open real tabs or resume sessions. Use `review-ops restart --dry-run` and
  dry_run requests, then `review-ops restart` at the end.
- Keep your context small: trim command output.
- Keep this handoff file current whenever you change the app.

## How to test
- curl: /api/health, /api/prs, /api/pr/<repo>/<N>, /api/file?path=...,
  POST /api/refresh and /api/action/{rereview,new,focus} with {"dry_run": true} and the
  headers above.
- Check new-commit and new-reply counts against gh REST
  (pulls/<N>/commits, /comments, /issues/<N>/comments, /reviews).
- Screenshots: a headless browser (Brave: --headless=new --user-data-dir=<tmp>
  --screenshot=<file> --virtual-time-budget=20000 <url>). The first shot with a new profile
  often fails; run it again.
- JS syntax: node --check on app.js

## Known gaps
- "Last look" = our latest GitHub review, or a later round's checked_at. Imported recheck rounds
  use the report file's time.
- "Answer in tab" is a guess from the session's last message. "Working" = transcript written
  in the last 90 s.
- Tab numbers in the registry can be old. The live tab always comes from ps.
- GitHub data: last 100 commits, 100 threads, 60 comments per thread.
- Your own brief template (`pr-reviews/brief-template.md`) wins over the starter one in `templates/`.
- Finding matching is heuristic: a finding with no file:line, or a thread on another line, shows
  "—" for GitHub; recheck rows with no id fall back to word overlap.

First, run `review-ops status` and `review-ops list`, open http://127.0.0.1:8765, then wait
for my next request.
