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
        "decided_at": dec.get("decided_at"),
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
