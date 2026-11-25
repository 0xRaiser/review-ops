"""Planned review comments ("drafts") and the user's decisions on them.

The review session writes pr-reviews/<repo>/<PR>/<date>-post/drafts.json. The dashboard writes
decisions.json next to it (atomic). The session posts only approved items, after the user confirms
in the session. Nothing here talks to GitHub except reads (cached).
"""
import glob
import json
import os
import re
import tempfile

import collect as C
import model as M
import registry as R

DECISIONS = ("approved", "rejected", "pending")
# Step 0 of every review: map the PR the way the user reviews by hand, before any logic pass.
# Which steps run depends on the review depth (light / standard / full).
MAP_STEPS = {
    "scope": """**Scope.** Find the intent first. If the PR body, its links or the branch name point to a Linear ticket, read
   the ticket (Linear tools or the link): it says what the feature needs; an AI-written PR body only says what the
   code does. Then mark every changed file **expected** (the feature needs it), **surprising** (may be fine; say
   why it is touched) or **unrelated** (not part of this feature). Read surprising and unrelated files first.""",
    "flow": """**Flow.** Connect the changed parts across folders and crates: which part calls which, what data moves
   between them, in what order. Short steps.""",
    "reuse": """**New components and reuse.** For every new module, struct, trait, client, helper, table, queue/subject,
   job or config section: search for an existing one that does the same or a close job. Say whether it could be
   used or extended (**yes / partly / no**) and why. A new component next to an existing one needs a reason
   (different contract, performance, isolation, a planned replacement); with no reason it is a finding.""",
    "triggers": """**Triggers.** For each new or changed piece of logic: what starts it (HTTP route, NATS subject, timer,
   startup, config flag, another call) and the path that reaches it. Flag logic that nothing triggers (dead code)
   and triggers that can fire twice or out of order.""",
    "tests": """**Tests as the spec.** Do the tests state the intended behaviour (from the ticket and the domain), or do
   they only repeat what the code does? Look for tests that pass by design (asserting the code's own output,
   mocking the thing under test, counting rows instead of checking states), missing failure and edge cases, and
   behaviour the ticket asks for that no test covers. Mark each important test **spec**, **mirror** or **missing**.""",
    "rollout": """**Rollout and ops.** Deploy order across services and repos, migrations against live tables, feature
   flags and their defaults in stage and prod, config and secrets that must exist first, backward compatibility of
   messages and APIs during the rollout, rollback path, and the metrics, logs or alerts that show it works. A
   correct change shipped in the wrong order is still a bug.""",
    "right_size": """**Right-sized? (after the logic passes).** Judge the design both ways:
   - **Overengineered:** an abstraction, trait, generic layer or config option with one user or none; a
     framework for a case that does not exist yet; caching or concurrency with no measured need; indirection
     that makes the flow harder to follow. Simpler code that does the same job is the fix, and it is a finding.
   - **Under-built:** duplication, special cases piled up, unclear boundaries, code the PR leaves unused.
   Then list **what could be better**: at most 5 changes that keep maintenance low while staying efficient and
   easy to understand, ranked by value for cost. Prefer removing and simplifying over adding. Each: what, where,
   why, effort (small / medium / large) and when (**this PR** only if small and it prevents bugs or drift;
   otherwise **follow-up**). Never suggest an abstraction for one caller, renames, style, or a rewrite with no
   clear payoff. Suggestions, not blockers, unless one prevents a bug.""",
    "practice": """**Industry practice, judged on fit.** Only where it matters (money, auth, data, deploy). Where the PR
   solves a known problem, name the common practice and one line on why it exists (for example idempotency keys
   and an outbox for money actions, backoff with jitter, expand-and-contract migrations, the venue's official
   signing scheme). If the PR departs from it, look for the reason; a good reason (our scale, latency, a venue
   quirk, simpler for this team, a known flaw in the usual way) makes it fine. No reason, and the usual way is
   safer or simpler here: a finding. Never push a practice that adds cost with no gain for this code base.""",
}
DEPTH_STEPS = {
    "light": ["scope", "rollout"],
    "standard": ["scope", "flow", "reuse", "triggers", "tests", "rollout"],
    "full": ["scope", "flow", "reuse", "triggers", "tests", "rollout", "right_size", "practice"],
}
STEP_KEYS = {"scope": "scope", "flow": "flow", "reuse": "new_components", "triggers": "triggers", "tests": "tests",
             "rollout": "rollout", "right_size": "improvements", "practice": "practices"}


def map_text(depth="full"):
    steps = DEPTH_STEPS.get(depth, DEPTH_STEPS["full"])
    before = [k for k in steps if k not in ("right_size", "practice")]
    after = [k for k in steps if k in ("right_size", "practice")]
    lines = [f"## Step 0: map the PR first (depth: {depth})",
             "Review in this order, the way the user does by hand. Finish the map before the logic passes, and use it in them."]
    n = 0
    for k in before:
        n += 1
        lines.append(f"{n}. {MAP_STEPS[k]}")
    lines.append(f"{n + 1}. **Logic:** the passes below.")
    n += 1
    for k in after:
        n += 1
        lines.append(f"{n}. {MAP_STEPS[k]}")
    keys = ", ".join(f"`{STEP_KEYS[k]}`" for k in steps)
    lines += ["",
              "**Evidence:** every map claim cites `file:line` at the PR head. A claim that something is missing (\"nothing "
              "triggers this\", \"no existing component\", \"no test covers it\") also says how you searched (the grep or "
              "the call sites you read). If you could not check a claim, say so; do not guess.",
              f"**Tag each finding** with the step that found it: `step` in each drafts item, one of "
              f"{', '.join(steps + ['logic'])}. The learner uses this to see which steps pay off.",
              f"Then the comments, then the probes. Put the map in the drafts `walkthrough` ({keys}) and in the report "
              "under **Map**. Steps not listed here are skipped at this depth; say so in one line."]
    return "\n".join(lines) + "\n"


MAP_TEXT = map_text("full")

# Plain-language notes for the user, written by the review session into drafts.json and never posted.
EXPLAIN_TEXT = """## Explain each draft for the user (in drafts.json; never posted)
The user decides on each draft without reading the code. Write for them the way a senior engineer explains an
issue to a junior: plain words, short sentences, no jargon without a one-line meaning.
- Each item also has `"step"`: the review step that found it (scope, flow, reuse, triggers, tests, rollout,
  right_size, practice or logic).
- Add `"explain"` to every item: `{"does": "what this code does, in one or two sentences", "breaks": "what goes
  wrong, as a concrete story (who does what, what happens)", "impact": "real-world effect: money, users, data,
  ops; say if nothing bad can happen", "why_severity": "why this severity and not one higher or lower",
  "confidence": "how sure we are and how we know (probe, read, assumption)", "decide": "what the user is
  deciding, and a one-line recommendation"}`.
- Add a top-level `"walkthrough"`: `{"summary": "what the PR adds or changes, 3-5 sentences", "flow": "how data or
  control moves through the new code, as short steps", "scope": [{"path": "file or folder", "verdict":
  "expected|surprising|unrelated", "why": "..."}], "new_components": [{"name": "...", "path": "...", "similar":
  "existing component or none", "reuse": "yes|partly|no", "why": "..."}], "triggers": [{"what": "logic", "path":
  "...", "trigger": "route / subject / timer / startup / flag / call", "how": "path that reaches it"}], "improvements": [{"what": "...", "where": "...", "why": "cost it saves",
  "kind": "simplify|remove|dedupe|clarify|other", "effort": "small|medium|large", "when": "this PR|follow-up"}],
  "tests": [{"test": "name or area", "verdict": "spec|mirror|missing", "why": "..."}], "rollout": [{"item": "deploy order /
  migration / flag / config / compat / rollback / monitoring", "status": "ok|risk|missing", "why": "..."}],
  "practices": [{"problem": "...", "practice": "the common way, and why it exists", "pr": "follows|departs",
  "reason": "the PR's reason, or none found", "verdict": "fine|finding|practice does not fit here"}], "risky":
  [{"path": "file or area", "why": "..."}], "glossary": [{"term": "...", "meaning": "..."}]}`. Group files by
  folder in `scope` when there are many; list every surprising or unrelated file on its own. Keep it short: a map
  before the details.
- The item `body` stays written for the PR author. Never post `explain` or `walkthrough`.
"""

# Optional reasons for a decision (one click in the Drafts tab). The learner reads them.
REASONS = {
    "rejected": {"not_real": "Not a real issue", "preexisting": "Pre-existing, not made worse", "too_minor": "Too minor",
                 "wrong_severity": "Wrong severity", "duplicate": "Already raised", "out_of_scope": "Out of scope",
                 "not_proven": "Not proven", "tone": "Tone or wording"},
    "approved": {"important": "Important catch", "good_proof": "Good proof", "would_miss": "Would have missed it"},
}
VERDICTS = ("request_changes", "approve_with_nits", "approve", "comment")
SEVERITIES = ("blocker", "major", "minor", "nit", "security")
MAX_TEXT = 65000


def drafts_path(repo, number):
    """The newest <date>-post/drafts.json of this PR, or ""."""
    folder = os.path.join(R.PR_REVIEWS, repo, str(int(number)))
    hits = sorted(glob.glob(os.path.join(folder, "*-post", "drafts.json")))
    return hits[-1] if hits else ""


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _hunk(patch, line, around=3):
    """Lines of the PR diff around a new-file line number: [{o, n, kind, text}]."""
    if not patch or not line:
        return []
    rows, o, n = [], 0, 0
    for l in patch.split("\n"):
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)", l)
        if m:
            o, n = int(m.group(1)), int(m.group(2))
            continue
        c = l[:1]
        if c == "+":
            rows.append({"o": "", "n": n, "kind": "add", "text": l})
            n += 1
        elif c == "-":
            rows.append({"o": o, "n": "", "kind": "del", "text": l})
            o += 1
        else:
            rows.append({"o": o, "n": n, "kind": "", "text": l})
            o += 1
            n += 1
    idx = [i for i, r in enumerate(rows) if r["n"] == line]
    if not idx:
        return []
    i = idx[0]
    return rows[max(0, i - around): i + around + 1]


def counts(dec, items):
    c = {"approved": 0, "rejected": 0, "pending": 0}
    by = {d["id"]: d for d in (dec or {}).get("items", [])}
    for it in items:
        c[(by.get(it["id"]) or {}).get("decision", "pending")] += 1
    return c


def quick_counts(repo, number):
    """Counts only (for the sidebar); None when there is no drafts file."""
    p = drafts_path(repo, number)
    if not p:
        return None
    d = _read(p) or {}
    dec = _read(os.path.join(os.path.dirname(p), "decisions.json")) or {}
    out = counts(dec, d.get("items") or [])
    sent = sent_info(p)
    out["sent"] = bool(sent)
    out["posted"] = bool(posted_info(C.gh_pr(repo, number), sent, p))
    return out


def load(repo, number):
    p = drafts_path(repo, number)
    pr = C.gh_pr(repo, number)
    head_now = pr.get("headRefOid") or ""
    if not p:
        return {"exists": False, "folder": os.path.relpath(os.path.join(R.PR_REVIEWS, repo, str(number)), R.WUP),
                "ask_prompt": ask_prompt(repo, number), "head_now": head_now[:8]}
    d = _read(p)
    if d is None:
        return {"exists": True, "error": "drafts.json is not valid JSON", "path": os.path.relpath(p, R.WUP)}
    dpath = os.path.join(os.path.dirname(p), "decisions.json")
    dec = _read(dpath) or {}
    by = {x["id"]: x for x in dec.get("items", []) if isinstance(x, dict) and "id" in x}
    files = {f["filename"]: f for f in (C.gh_pr_files(repo, number).get("files") or [])}
    threads = {}
    for t in M.threads_of(pr):
        for cm in t["comments"]:
            cid = (cm.get("url") or "").rsplit("r", 1)[-1]
            threads[cid] = t
    items = []
    for it in d.get("items") or []:
        if not isinstance(it, dict) or "id" not in it:
            continue
        x = dict(it)
        dd = by.get(it["id"]) or {}
        x["decision"] = dd.get("decision", "pending")
        x["edited_body"] = dd.get("body")
        x["note"] = dd.get("note", "")
        x["reasons"] = [r for r in dd.get("reasons") or [] if r in REASONS.get(x["decision"], {})]
        f = files.get(it.get("path") or "")
        x["context"] = _hunk((f or {}).get("patch"), it.get("line"))
        th = threads.get(str(it.get("in_reply_to"))) if it.get("in_reply_to") else None
        x["thread"] = ({"where": f"{th['path']}:{th['line']}", "resolved": th["resolved"], "url": th["url"],
                        "comments": [{"by": c["by"], "at": c["at"], "body": c["body"][:1200]} for c in th["comments"]]}
                       if th else None)
        items.append(x)
    head = (d.get("head") or "")
    sent = sent_info(p)
    import actions as A  # late import: actions imports this module too
    return {
        "job": "replies" if M.login(pr) == C.ME else "review",
        "session": A.drafts_session(repo, number), "sent": sent, "posted": posted_info(pr, sent, p),
        "exists": True, "path": os.path.relpath(p, R.WUP), "decisions_path": os.path.relpath(dpath, R.WUP),
        "head": head[:8], "head_now": head_now[:8],
        "head_moved": bool(head and head_now and not head_now.startswith(head[:7]) and not head.startswith(head_now[:7])),
        "verdict": dec.get("verdict") or d.get("verdict"), "verdict_drafted": d.get("verdict"),
        "summary": dec.get("summary") if dec.get("summary") is not None else d.get("summary", ""),
        "summary_drafted": d.get("summary", ""), "items": items, "counts": counts(dec, items),
        "decided_at": dec.get("decided_at"), "reasons": REASONS, "walkthrough": d.get("walkthrough"),
    }


def save(repo, number, payload):
    """Write decisions.json (atomic) from the dashboard's state; record the counts on the round."""
    p = drafts_path(repo, number)
    if not p:
        return {"ok": False, "error": "No drafts file"}
    d = _read(p) or {}
    ids = {it["id"]: it for it in d.get("items") or [] if isinstance(it, dict) and "id" in it}
    verdict = payload.get("verdict")
    if verdict not in VERDICTS:
        return {"ok": False, "error": "bad verdict"}
    summary = str(payload.get("summary") or "")[:MAX_TEXT]
    out_items = []
    for x in payload.get("items") or []:
        if not isinstance(x, dict) or x.get("id") not in ids:
            continue
        dec = x.get("decision") if x.get("decision") in DECISIONS else "pending"
        row = {"id": x["id"], "decision": dec}
        body = x.get("body")
        if isinstance(body, str) and body != ids[x["id"]].get("body"):
            row["body"] = body[:MAX_TEXT]
        if x.get("note"):
            row["note"] = str(x["note"])[:2000]
        rs = [r for r in x.get("reasons") or [] if r in REASONS.get(dec, {})]
        if rs:
            row["reasons"] = rs
        out_items.append(row)
    data = {"head": d.get("head", ""), "verdict": verdict, "summary": summary, "items": out_items,
            "decided_at": R.now_iso()}
    folder = os.path.dirname(p)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".decisions-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, os.path.join(folder, "decisions.json"))
    c = counts(data, list(ids.values()))
    with R.locked():
        reg = R.load()
        pe = R.find(reg, repo, number)
        rnd = R.latest_round(pe) if pe else None
        if rnd is not None:
            rnd["decisions"] = dict(c, verdict=verdict, at=data["decided_at"], drafts=os.path.relpath(p, R.PR_REVIEWS))
            R.save(reg)
    return {"ok": True, "counts": c, "decided_at": data["decided_at"]}


def _sent_path(drafts_file):
    return os.path.join(os.path.dirname(drafts_file), "sent.json")


def mark_sent(repo, number, how, tty="", c=None):
    """Write sent.json next to decisions.json: when the approved drafts went to the session.
    how: new_tab (Send opened a tab) or copied (the user copied the prompt into the open tab)."""
    p = drafts_path(repo, number)
    if not p:
        return {"ok": False, "error": "No drafts file"}
    head = (C.gh_pr(repo, number).get("headRefOid") or "")[:8]
    data = {"at": R.now_iso(), "head": head, "how": how, "tty": tty, "counts": c or {}}
    folder = os.path.dirname(p)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".sent-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, _sent_path(p))
    return {"ok": True, "sent": data}


def _changed_at(drafts_file):
    """Last change of the drafts or of the user's decisions (mtime)."""
    t = 0.0
    for f in (drafts_file, os.path.join(os.path.dirname(drafts_file), "decisions.json")):
        try:
            t = max(t, os.path.getmtime(f))
        except OSError:
            pass
    return t


def sent_info(drafts_file):
    """The sent marker, or None. New drafts or new decisions after the send make it stale."""
    s = _read(_sent_path(drafts_file))
    if not isinstance(s, dict) or not s.get("at"):
        return None
    if _changed_at(drafts_file) > M.ts(s["at"]) + 1:
        return None
    return s


def posted_info(pr, sent, drafts_file=""):
    """Posted after the drafts went out: {at, url, count, source}, or None.
    1) our reviews or replies on GitHub after the send, or (no send recorded) after drafts.json
       was written (cached gh data);
    2) else a posted*.json the session wrote next to drafts.json, newer than drafts.json.
    Decisions are not checked here (sessions save them around posting); "Send again" covers a
    second post."""
    t_drafts = os.path.getmtime(drafts_file) if drafts_file and os.path.exists(drafts_file) else 0
    t0 = M.ts(sent["at"]) if sent else t_drafts
    if t0:
        rs = [r for r in M.our_reviews(pr, replies=True) if M.ts(r.get("submittedAt")) > t0]
        if rs:
            rs.sort(key=lambda r: M.ts(r.get("submittedAt")))
            main = next((r for r in rs if r.get("state") in ("APPROVED", "CHANGES_REQUESTED") or (r.get("body") or "").strip()), rs[0])
            return {"at": rs[-1].get("submittedAt"), "url": main.get("url") or pr.get("url"), "count": len(rs), "source": "github"}
    if not drafts_file:
        return None
    t_min = max(t_drafts, M.ts(sent["at"]) if sent else 0) - 1
    best, urls = 0.0, []
    for f in glob.glob(os.path.join(os.path.dirname(drafts_file), "posted*.json")):
        try:
            mt = os.path.getmtime(f)
            if mt < t_min:
                continue
            with open(f) as fh:
                text = fh.read(200000)
        except OSError:
            continue
        best = max(best, mt)
        urls += re.findall(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+[#\w-]*", text)
    if not best:
        return None
    url = next((u for u in urls if "pullrequestreview" in u), urls[0] if urls else pr.get("url"))
    return {"at": __import__("datetime").datetime.fromtimestamp(best, __import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            "url": url, "count": len(set(urls)) or 1, "source": "file"}


def send_prompt(repo, number):
    """The prompt that asks the review session to post the approved items; plus warnings."""
    info = load(repo, number)
    if not info.get("exists") or info.get("error"):
        return {"ok": False, "error": info.get("error") or "No drafts file"}
    c = info["counts"]
    warn, block = [], False
    if info["head_moved"]:
        warn.append(f"The PR head moved since the drafts were written ({info['head']} → {info['head_now']}). "
                    "Line numbers and findings may be out of date.")
        block = True
    if c["pending"]:
        warn.append(f"{c['pending']} item(s) are still pending. They will not be posted.")
        block = True
    if not c["approved"]:
        warn.append("No item is approved. Only the verdict and summary would be posted.")
    dpath = os.path.normpath(os.path.join(R.WUP, info["decisions_path"]))
    prompt = (f"Post the review for {repo} PR #{number} at `{info['head']}`.\n\n"
              f"- Use only the items marked **approved** in `{dpath}`, with their "
              "edited bodies where the file has one.\n"
              f"- Use the verdict and summary in that file (verdict: **{info['verdict']}**).\n"
              "- Do not post rejected or pending items.\n"
              f"- Approved: {c['approved']}, rejected: {c['rejected']}, pending: {c['pending']}.\n"
              "- First show me exactly what you will post (each comment with file:line), then post only after I "
              "confirm in this session.\n"
              f"- After posting, record it: `review-ops posted {repo} {number}`.\n")
    return {"ok": True, "prompt": prompt, "warnings": warn, "blocked": block, "counts": c}


def ask_prompt(repo, number):
    today = __import__("datetime").date.today().isoformat()
    return (f"Before posting anything on {repo} PR #{number}, write every planned GitHub comment to "
            f"`../pr-reviews/{repo}/{number}/{today}-post/drafts.json`, as the brief template's section "
            "\"Drafts for the user to approve\" says: "
            '{"pr", "head", "verdict": "request_changes|approve_with_nits|approve|comment", "summary", "items": '
            '[{"id", "kind": "inline|reply|summary", "finding", "severity": "blocker|major|minor|nit|security", '
            '"proof": "proven|not proven|not testable", "path", "line", "in_reply_to", "body"}]}. '
            "Then stop and tell me. I approve, reject or edit each item in Review Ops. Do not post anything yet.\n")


def ask(repo, number, kind, item_id, text):
    """A question for the review session about the drafts (copied by the dashboard; nothing runs here).
    kind "question": about one draft; kind "explain_all": add the plain-language notes to every draft.
    Questions are also logged for the learner: they show where drafts were hard to judge."""
    p = drafts_path(repo, number)
    if not p:
        return {"ok": False, "error": "No drafts file"}
    rel = os.path.relpath(p, R.WUP)
    d = _read(p) or {}
    it = next((x for x in d.get("items") or [] if isinstance(x, dict) and x.get("id") == item_id), None)
    if kind == "question":
        if not it or not text.strip():
            return {"ok": False, "error": "Pick a draft and write a question"}
        prompt = (f"Question from the user about draft {item_id} ({it.get('severity', '?')}, "
                  f"{it.get('path', '')}:{it.get('line', '')}) on {repo} #{number}. Do not post anything.\n\n"
                  f"> {text.strip()[:2000]}\n\n"
                  "Answer here in plain words, the way a senior engineer explains to a junior: short sentences, a "
                  "concrete example, and a one-line meaning for any term. Read the code at the drafts head if you need to. "
                  f"If the answer changes the draft (severity, wording, or it should be dropped), update the item and its "
                  f"`explain` in `{rel}` and say what changed.")
    elif kind == "explain_all":
        prompt = (f"Add plain-language notes to the drafts of {repo} #{number} in `{rel}`. Do not post anything and do "
                  "not change any item's `body`.\n\n" + EXPLAIN_TEXT)
    else:
        return {"ok": False, "error": "unknown kind"}
    try:
        import learner as L
        L.log_question(repo, number, rel, item_id, kind, text, it)
    except Exception:  # noqa: BLE001  (logging must never block the question)
        pass
    sess = C.session_state(R.find(R.load(), repo, number) or {"repo": repo, "number": number}) or {}
    return {"ok": True, "prompt": prompt, "tty": sess.get("tty", "")}
