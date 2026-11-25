"""Triage of review comments on my PRs: prove each comment before anything is fixed.

Phase 1 (the work session): read each comment, try to prove or disprove it, judge the suggested fix, and
write <pr_reviews>/<repo>/<PR>/<date>-triage/triage.json. Nothing changes in the code.
Phase 2 (after the user decides in Review Ops): fix only the comments marked "fix", with the smallest change,
test first; draft replies for every decided comment; push and post only after the user confirms.
The user's decisions go to triage-decisions.json next to triage.json.
"""
import glob
import json
import os
import tempfile

import collect as C
import registry as R

VERDICTS = {
    "valid_proven": "Valid, proven", "valid_unproven": "Valid, not reproduced", "handled": "Already handled",
    "misread": "Misread", "out_of_scope": "Out of scope", "style": "Style preference",
    "conflict": "Conflicts with another comment", "duplicate": "Duplicate",
}
FIX_JUDGEMENT = {"good": "Good fix", "partial": "Partly right", "overengineered": "Overengineered",
                 "wrong": "Wrong fix", "none": "No fix suggested"}
ACTIONS = ("fix", "decline", "followup", "ask", "pending")

PHASE1_TEXT = """## Phase 1: triage every comment before changing any code
AI and human reviewers can both be wrong. Prove each comment; do not just agree with it.
For each comment (thread or review body):
1. Read it in full, and the code it points at, at the PR head. Read the thread's replies too.
2. **Test the claim.** If it says something breaks, try to show it: a probe test or a run on the box, or the exact
   `file:line` that makes it impossible. Say how you checked. Do not change the PR's code in this phase; probes
   live in scratch files or on the box and are removed after.
3. Pick one verdict: `valid_proven`, `valid_unproven` (reasoning holds, not reproduced), `handled` (show where),
   `misread` (show the code that proves it), `out_of_scope`, `style`, `conflict` (with another comment; name it),
   `duplicate`.
4. **Judge the suggested fix on its own**, even when the problem is real: `good`, `partial`, `overengineered`
   (an abstraction for one caller, a framework for a case that does not exist, unmeasured caching), `wrong`, or
   `none`. Apply the right-sized and industry-practice checks to the suggestion, judged on fit for this code base.
5. Plan the **smallest** change that solves the real problem, or the reply if no change is needed.
6. Recommend one action: `fix`, `decline` (with a reply), `followup` (a ticket, later), or `ask` (the user must
   decide, for example a product choice or a conflict between reviewers).
7. Write a plain-language `explain` for the user (they decide without reading the code), the way a senior explains
   to a junior: what the code does, what the reviewer says is wrong, whether that is true and how we know, the
   real-world impact, and your recommendation.

Write `{triage_path}`:
`{{"pr": {number}, "head": "<sha>", "items": [{{"id": "T1", "thread": "<thread id>", "comment_id": <id or null>,
"reviewer": "<login>", "bot": true|false, "path": "<file or null>", "line": <n or null>, "comment": "<the
comment, trimmed>", "verdict": "<one of the verdicts>", "evidence": "<file:line, test or run, and how you
checked>", "proof": "proven|not proven|not testable", "suggested_fix": "<what the reviewer suggests>",
"fix_judgement": "good|partial|overengineered|wrong|none", "plan": "<smallest change, or the reply>",
"recommend": "fix|decline|followup|ask", "conflicts_with": ["T2"], "explain": {{"does": "...", "claim": "...",
"truth": "...", "impact": "...", "decide": "..."}}}}]}}`.
Then **stop** and tell the user to decide each item in Review Ops (My PRs → Triage). Change no code, push nothing,
post nothing.
"""


def folder(repo, number):
    return os.path.join(R.PR_REVIEWS, repo, str(int(number)))


def triage_path(repo, number):
    files = sorted(glob.glob(os.path.join(folder(repo, number), "*-triage", "triage.json")))
    return files[-1] if files else ""


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load(repo, number):
    p = triage_path(repo, number)
    if not p:
        return {"exists": False}
    d = _read(p)
    if d is None:
        return {"exists": True, "error": "triage.json is not valid JSON", "path": os.path.relpath(p, R.WUP)}
    dec = _read(os.path.join(os.path.dirname(p), "triage-decisions.json")) or {}
    by = {x["id"]: x for x in dec.get("items") or [] if isinstance(x, dict) and "id" in x}
    items = []
    for it in d.get("items") or []:
        if not isinstance(it, dict) or "id" not in it:
            continue
        x = dict(it)
        dd = by.get(it["id"]) or {}
        x["action"] = dd.get("action", "pending")
        x["note"] = dd.get("note", "")
        items.append(x)
    head_now = (C.gh_pr(repo, number).get("headRefOid") or "")
    head = d.get("head") or ""
    counts = {a: sum(1 for x in items if x["action"] == a) for a in ACTIONS}
    import actions as A  # late import: actions imports this module
    return {"exists": True, "path": os.path.relpath(p, R.WUP), "head": head[:8], "head_now": head_now[:8],
            "head_moved": bool(head and head_now and not head_now.startswith(head[:7]) and not head.startswith(head_now[:7])),
            "items": items, "counts": counts, "verdicts": VERDICTS, "fix_judgement": FIX_JUDGEMENT,
            "decided_at": dec.get("decided_at"), "sent": dec.get("sent"), "session": A.drafts_session(repo, number)}


def save(repo, number, payload):
    p = triage_path(repo, number)
    if not p:
        return {"ok": False, "error": "No triage file"}
    ids = {it.get("id") for it in (_read(p) or {}).get("items") or [] if isinstance(it, dict)}
    old = _read(os.path.join(os.path.dirname(p), "triage-decisions.json")) or {}
    rows = []
    for x in payload.get("items") or []:
        if not isinstance(x, dict) or x.get("id") not in ids:
            continue
        row = {"id": x["id"], "action": x.get("action") if x.get("action") in ACTIONS else "pending"}
        if x.get("note"):
            row["note"] = str(x["note"])[:2000]
        rows.append(row)
    data = {"items": rows, "decided_at": R.now_iso(), "sent": old.get("sent")}
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".triage-decisions-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, os.path.join(os.path.dirname(p), "triage-decisions.json"))
    return {"ok": True, "decided_at": data["decided_at"],
            "counts": {a: sum(1 for r in rows if r["action"] == a) for a in ACTIONS}}


def phase2_prompt(repo, number, folder_dir, base, branch, today):
    """The fix phase, built from the user's decisions. Returns (prompt, error)."""
    info = load(repo, number)
    if not info.get("exists") or info.get("error"):
        return "", info.get("error") or "No triage file"
    items = info["items"]
    pending = [x["id"] for x in items if x["action"] == "pending"]
    if pending:
        return "", "Decide every item first (pending: " + ", ".join(pending) + ")"
    rel = triage_path(repo, number)  # absolute: the session may run in the repo or in a worktree
    def line(x):
        where = f"{x.get('path')}:{x.get('line')}" if x.get("path") else "review body"
        note = f" User note: \"{x['note']}\"" if x.get("note") else ""
        return f"- **{x['id']}** ({x.get('reviewer', '?')}, {where}): {x.get('plan', '')}{note}"
    groups = {a: [x for x in items if x["action"] == a] for a in ("fix", "decline", "followup", "ask")}
    L = [f"## Phase 2: act on the user's triage decisions for {R.ORG}/{repo} #{number}",
         f"The decisions are in `{os.path.join(os.path.dirname(rel), 'triage-decisions.json')}` next to `{rel}`.", ""]
    if groups["fix"]:
        L += ["**Fix these, and only these:**", *[line(x) for x in groups["fix"]], ""]
    if groups["decline"]:
        L += ["**Decline these with a reply (no code change):**", *[line(x) for x in groups["decline"]], ""]
    if groups["followup"]:
        L += ["**Follow-up (no code change now):** draft a Linear ticket for each (title, why, scope); create it only "
              "after the user confirms. Reply on the thread with the plan.", *[line(x) for x in groups["followup"]], ""]
    if groups["ask"]:
        L += ["**The user wants to talk about these first:** ask them here, one short question each, before you "
              "touch them.", *[line(x) for x in groups["ask"]], ""]
    L += ["## How",
          f"1. Check you are on `{branch}` (`git -C {folder_dir} branch --show-current`). If not, stop and tell the user.",
          "2. **Smallest change** for each fix: solve the real problem the comment points at; no refactors, renames or "
          "new abstractions beyond it. If the reviewer's suggested fix was judged overengineered or wrong, do the "
          "simpler correct fix and say so in the reply.",
          "3. **Test first:** for each fix, a test that fails on the current head and passes after the fix. If a fix "
          "cannot be tested, say why.",
          f"4. **Scope-check the fix diff** (`git diff origin/{base}` minus the PR before your fixes): every file you "
          "touched must be needed by an accepted comment. Undo anything else.",
          f"5. Run the gates on the box on your working tree: `quality-remote start --repo {folder_dir} --base "
          f"origin/{base} --tool all <changed files>` (`--db` when DB code changed), then `quality-remote wait` and "
          "`fetch`. Never run crapper, mutator, cargo-mutants or llvm-cov on this Mac.",
          f"6. Draft one reply per decided thread in `../pr-reviews/{repo}/{number}/{today}-post/drafts.json` "
          "(Review Ops drafts format, `kind: reply`, with `in_reply_to`, an `explain` block and `step: \"triage\"`):",
          "   - **Fixed:** the commit, one line on what changed, and the test that proves it.",
          "   - **Declined:** polite, with the evidence (`file:line`, a test or a run). Never just \"not needed\".",
          "   - **Follow-up:** the plan and the ticket (after the user confirms it).",
          "7. Show the user a short summary. **Do not commit, push, post replies, resolve threads or re-request "
          "review until the user confirms in this session.** No AI attribution anywhere."]
    return "\n".join(L) + "\n", ""


def mark_sent(repo, number, how, tty=""):
    p = triage_path(repo, number)
    if not p:
        return
    dp = os.path.join(os.path.dirname(p), "triage-decisions.json")
    d = _read(dp) or {"items": []}
    d["sent"] = {"at": R.now_iso(), "how": how, "tty": tty}
    with open(dp, "w") as f:
        json.dump(d, f, indent=2)
