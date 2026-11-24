"""Builds what the UI shows from the registry plus the read-only collectors."""
import datetime as dt
import json
import os
import threading
import re
import time
from concurrent.futures import ThreadPoolExecutor

import collect as C
import config as CFG
import registry as R

BOX_FILE = os.path.join(C.CACHE_DIR, "box.json")


def _load_box():
    try:
        with open(BOX_FILE) as f:
            return {(k.split("#")[0], int(k.split("#")[1])): v for k, v in json.load(f).items()}
    except (OSError, ValueError, IndexError):
        return {}


def _save_box():
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    with open(BOX_FILE, "w") as f:
        json.dump({f"{k[0]}#{k[1]}": v for k, v in _box.items()}, f)


EXCLUDE_AUTHORS = CFG.EXCLUDE_AUTHORS   # authors left out of the lists (config: exclude_authors)


def requested_me(pr):
    for n in (pr.get("reviewRequests") or {}).get("nodes") or []:
        if ((n.get("requestedReviewer") or {}).get("login") or "") == C.ME:
            return True
    return False


ACTIVE_BOX = {"db", "crap", "mutate", "cmut", "running", "starting"}


def section(s, has_rounds):
    """One of: pending, in_progress, to_post, reviewed, next, approved, done.
    - to_post: the latest round is finished but not on GitHub yet (see unposted())
    - pending: not reviewed yet
    - in_progress: a review is going on (session working or waiting for you, box run, or in_progress)
    - reviewed: we reviewed; no reply or fix from the author yet (also closed without a merge)
    - next: we reviewed, and new commits, replies or a new review request need another review
    - approved: approved by us (with or without nits), not merged, nothing new
    - done: merged"""
    if s["gh_state"] == "MERGED":
        return "done"
    if s["gh_state"] == "CLOSED":
        return "reviewed" if has_rounds else "pending"
    sess, box = s["session"] or {}, s.get("box") or {}
    if sess.get("state") == "working" or box.get("phase") in ACTIVE_BOX or s["verdict"] == "in_progress":
        return "in_progress"
    if has_rounds and s.get("unposted"):
        return "to_post"
    if sess.get("waiting_for_you"):
        return "in_progress"
    if not has_rounds:
        return "pending"
    if s["new_commits"] or s["new_replies"] or s.get("requested_me"):
        return "next"
    if s["verdict"] in ("approved", "approve_with_nits"):
        return "approved"
    return "reviewed"


_box = _load_box()   # (repo, number) -> last box status; filled only on refresh


def ts(s):
    if not s:
        return 0.0
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        try:
            return dt.datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            return 0.0


def login(x):
    return ((x or {}).get("author") or {}).get("login") or "ghost"


def threads_of(pr):
    out = []
    for t in (pr.get("reviewThreads") or {}).get("nodes") or []:
        cs = (t.get("comments") or {}).get("nodes") or []
        if not cs:
            continue
        first, last = cs[0], cs[-1]
        out.append({
            "path": t.get("path"), "line": t.get("line") or t.get("originalLine"),
            "orig_line": t.get("originalLine"), "cur_line": t.get("line"),
            "id": t.get("id"),
            "resolved": t.get("isResolved"), "outdated": t.get("isOutdated"),
            "started_by": login(first), "review_id": (first.get("pullRequestReview") or {}).get("databaseId"),
            "last_by": login(last), "last_at": last.get("createdAt"), "url": first.get("url"),
            "comments": [{"by": login(c), "at": c.get("createdAt"), "body": (c.get("body") or "")[:4000],
                          "url": c.get("url"), "review_id": (c.get("pullRequestReview") or {}).get("databaseId")}
                         for c in cs],
        })
    return out


def our_reviews(pr, replies=False):
    """Our real reviews. GitHub also makes a 'review' for each thread reply; those are left out
    unless replies=True."""
    rs = [r for r in (pr.get("reviews") or {}).get("nodes") or [] if login(r) == C.ME]
    if replies:
        return rs
    starts = {}
    for t in (pr.get("reviewThreads") or {}).get("nodes") or []:
        cs = (t.get("comments") or {}).get("nodes") or []
        if cs and cs[0].get("pullRequestReview"):
            rid = cs[0]["pullRequestReview"].get("databaseId")
            starts[rid] = starts.get(rid, 0) + 1
    return [r for r in rs if r.get("state") in ("APPROVED", "CHANGES_REQUESTED")
            or (r.get("body") or "").strip() or starts.get(r.get("databaseId"))]


def last_look(p, pr):
    """Time of our last look: our latest GitHub review, or a later recorded round check."""
    t = max([ts(r.get("submittedAt")) for r in our_reviews(pr, replies=True)] or [0.0])
    rnd = R.latest_round(p)
    if rnd and rnd.get("checked_at"):
        t = max(t, ts(rnd["checked_at"]))
    return t


def commits_after(pr, head, since):
    nodes = [n["commit"] for n in (pr.get("commits") or {}).get("nodes") or []]
    if head:
        for i, c in enumerate(nodes):
            if c["oid"].startswith(head):
                return nodes[i + 1:], True
    # reviewed head not in the list (force-push or older than the last 100): fall back to dates
    return [c for c in nodes if since and ts(c.get("committedDate")) > since], False


def replies_after(pr, since, author):
    """Comments by others after `since`: thread comments, PR comments and other reviews with a body."""
    out = []
    for t in threads_of(pr):
        for c in t["comments"]:
            if c["by"] != C.ME and ts(c["at"]) > since:
                out.append({"by": c["by"], "at": c["at"], "where": f"{t['path']}:{t['line']}",
                            "body": c["body"][:300], "url": c["url"], "kind": "thread",
                            "is_author": c["by"] == author})
    for c in (pr.get("comments") or {}).get("nodes") or []:
        if login(c) != C.ME and ts(c.get("createdAt")) > since:
            out.append({"by": login(c), "at": c.get("createdAt"), "where": "conversation",
                        "body": (c.get("body") or "")[:300], "url": c.get("url"), "kind": "comment",
                        "is_author": login(c) == author})
    for r in (pr.get("reviews") or {}).get("nodes") or []:
        if login(r) != C.ME and ts(r.get("submittedAt")) > since and (r.get("body") or "").strip():
            out.append({"by": login(r), "at": r.get("submittedAt"), "where": f"review ({r.get('state', '').lower()})",
                        "body": (r.get("body") or "")[:300], "url": r.get("url"), "kind": "review",
                        "is_author": login(r) == author})
    # bots are noise for "needs you"
    out = [x for x in out if not x["by"].endswith("[bot]") and x["by"] not in ("github-actions", "coderabbitai")]
    out.sort(key=lambda x: x["at"] or "")
    return out


def _draft_counts(p):
    import drafts as D  # drafts imports this module
    return D.quick_counts(p["repo"], p["number"])


def unposted(p, pr):
    """Why the latest finished round is not on GitHub yet, or "" when it is (or there is none).
    A round counts as posted when one of our real reviews is at the round's head, or was sent
    after the round was recorded."""
    if pr.get("_error"):
        return ""
    import drafts as D
    dp = D.drafts_path(p["repo"], p["number"])
    if dp:  # a drafts file newer than our last review = planned comments not on GitHub yet
        last_any = max([ts(r.get("submittedAt")) for r in our_reviews(pr, replies=True)] or [0])
        if os.path.getmtime(dp) > last_any:
            return "drafts written after our last review"
    rnd = R.latest_round(p)
    if not rnd or rnd.get("pr", p["number"]) != p["number"]:
        return ""
    ours = our_reviews(pr)
    if not ours:
        return "no review of ours on GitHub"
    head = rnd.get("head") or ""
    if head and any(((r.get("commit") or {}).get("oid") or "").startswith(head[:7]) for r in ours):
        return ""
    when = ts(rnd.get("checked_at")) if rnd.get("checked_at") else 0
    if not when and rnd.get("report"):
        path = os.path.join(R.PR_REVIEWS, rnd["report"])
        when = os.path.getmtime(path) if os.path.exists(path) else 0
    last = max(ts(r.get("submittedAt")) for r in ours)
    if when and last >= when:
        return ""
    return f"round {rnd['n']} at {head[:8]} is newer than our last review"


def needs(p, pr, sess, box, new_commits, replies, ci):
    items = []
    state = pr.get("state")
    if state == "MERGED":
        items.append({"kind": "info", "text": "Merged"})
        return items
    if state == "CLOSED":
        items.append({"kind": "info", "text": "Closed"})
        return items
    if sess.get("waiting_for_you") and sess.get("tty"):
        items.append({"kind": "you", "text": f"Answer in tab {sess['tty']}"})
    if not p.get("rounds") and p.get("verdict") == "not_reviewed":
        items.append({"kind": "you", "text": "Not reviewed"})
    by_author = sum(1 for r in replies if r["is_author"])
    others = len(replies) - by_author
    if by_author:
        items.append({"kind": "you", "text": f"Author replied ({by_author})"})
    if others:
        items.append({"kind": "you", "text": f"Others commented ({others})"})
    if p.get("rounds") and requested_me(pr):
        items.append({"kind": "you", "text": "Review asked again"})
    if new_commits and p.get("rounds"):
        items.append({"kind": "you", "text": f"New commits since our review ({len(new_commits)})"})
    if ci.get("failing"):
        items.append({"kind": "warn", "text": f"CI failing ({len(ci['failing'])})"})
    up = unposted(p, pr)
    if up:
        items.append({"kind": "you", "text": "Not posted yet: " + up})
    if p.get("verdict") == "approved" and pr.get("mergeStateStatus") == "CLEAN" and not ci.get("failing"):
        items.append({"kind": "info", "text": "Ready to merge"})
    if box and box.get("phase") == "sso_expired":
        items.append({"kind": "warn", "text": "SSO expired"})
    return items


def summary(p, force=False):
    pr = C.gh_pr(p["repo"], p["number"], force=force)
    sess = C.session_state(p)
    box = _box.get((p["repo"], p["number"]))
    err = pr.get("_error")
    rnd = R.latest_round(p)
    since = last_look(p, pr) if not err else 0
    head = (rnd or {}).get("head", "")
    if rnd and rnd.get("pr", p["number"]) != p["number"]:
        head = ""  # the last round was on an older PR number
    new_commits, head_found = commits_after(pr, head, since) if not err and (head or since) else ([], True)
    replies = replies_after(pr, since, p.get("author") or login(pr)) if not err and since else []
    ci = C.ci_summary(pr) if not err else {"state": "", "failing": [], "pending": []}
    return {
        "repo": p["repo"], "number": p["number"], "previous_numbers": p.get("previous_numbers", []),
        "title": pr.get("title") or p.get("title") or f"PR #{p['number']}",
        "author": login(pr) if not err else p.get("author"), "gh_state": pr.get("state", ""),
        "draft": pr.get("isDraft", False),
        "verdict": p.get("verdict"), "verdict_label": R.VERDICTS.get(p.get("verdict"), p.get("verdict")),
        "round": len(p.get("rounds", [])), "waiting_on": p.get("waiting_on", ""),
        "head_reviewed": (rnd or {}).get("head", "")[:8], "head_now": (pr.get("headRefOid") or "")[:8],
        "head_found": head_found,
        "new_commits": len(new_commits), "new_replies": len(replies),
        "session": {k: sess.get(k) for k in ("state", "tty", "session", "waiting_for_you", "tab_title")},
        "box": box, "ci": ci, "needs": needs(p, pr, sess, box, new_commits, replies, ci),
        "error": err, "fetched": pr.get("_fetched"), "requested_me": requested_me(pr) if not err else False,
        "unposted": unposted(p, pr) if not err else "",
        "drafts": _draft_counts(p),
        "_pr": pr, "_new_commits": new_commits, "_replies": replies, "_since": since,
    }


def with_section(s, p, reg=None):
    s["section"] = section(s, bool(p.get("rounds")))
    s["link"] = link_label(reg or R.load(), p["repo"], p["number"])
    return s


def public(s):
    return {k: v for k, v in s.items() if not k.startswith("_")}


def all_summaries(force=False):
    reg = R.load()
    with ThreadPoolExecutor(max_workers=6) as ex:
        sums = list(ex.map(lambda p: with_section(summary(p, force), p, reg), R.active(reg)))
    tracked = {(p["repo"], n) for p in reg["prs"] for n in [p["number"]] + p.get("previous_numbers", [])}
    untracked = []
    with ThreadPoolExecutor(max_workers=6) as ex:  # warm the repo lists in parallel
        list(ex.map(lambda f: f[0](f[1], force=force),
                    [(fn, r) for r in R.REPO_DIRS for fn in (C.gh_open_prs, C.gh_requested)]))
    for repo in R.REPO_DIRS:
        asked = {o["number"] for o in C.gh_requested(repo, force=force)}
        seen = set()
        for o in C.gh_open_prs(repo, force=force) + C.gh_requested(repo, force=force):
            if o["number"] in seen:
                continue
            seen.add(o["number"])
            o.setdefault("reviewRequests", [{"login": C.ME}] if o["number"] in asked else [])
            author = (o.get("author") or {}).get("login")
            if (repo, o["number"]) not in tracked and author not in EXCLUDE_AUTHORS:
                untracked.append({"link": link_label(reg, repo, o["number"]), "requested_me": any((r.get("login") == C.ME) for r in o.get("reviewRequests") or []),"repo": repo, "number": o["number"], "title": o["title"],
                                  "author": (o.get("author") or {}).get("login"), "draft": o.get("isDraft"),
                                  "updated": o.get("updatedAt")})
    return {"prs": [public(s) for s in sums], "untracked": untracked, "updated": reg.get("updated"),
            "box_checked": max([b.get("checked", 0) for b in _box.values()] or [0])}


def refresh_box(only=None):
    """quality-remote status for PRs that have a run. Stops early once SSO is known to be expired."""
    reg = R.load()
    targets = [p for p in R.active(reg) if C.has_box_run(p) and (only is None or (p["repo"], p["number"]) == only)
               and p.get("rounds") is not None]
    if not targets or not C.QUALITY_REMOTE:  # no quality_remote in the config: no box status
        return {}
    first = C.box_status(targets[0])
    _box[(targets[0]["repo"], targets[0]["number"])] = first
    if first["phase"] == "sso_expired":
        for p in targets[1:]:
            _box[(p["repo"], p["number"])] = dict(first)
        _save_box()
        return {"sso_expired": True}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for p, st in zip(targets[1:], ex.map(C.box_status, targets[1:])):
            _box[(p["repo"], p["number"])] = st
    _save_box()
    return {"sso_expired": False}


# ---------------------------------------------------------------- PR page

SEVERITIES = ["blocker", "major", "minor", "nit", "question", "pre-existing", "dropped"]


# A finding starts a line with bold: "**M1. Title**", "**1. Title**", "1. **Title**" or "**n1** text".
# Bold labels such as "**Where:**" are not findings.
ITEM_RX = re.compile(r"^(?:\d+\.\s+)?\*\*(?:(R\d+-[A-Za-z]+\d+|[A-Za-z]?\d+[a-z]?)\.?(?:\*\*\s*|\s+))?([^*\n]{0,200})(?:\*\*)?([^\n]*)$", re.M)
REF_RX = re.compile(r"`([\w./-]+\.\w+):(\d+)(?:-(\d+))?|`:(\d+)(?:-(\d+))?")
STATUS_WORDS = [("unchanged", "open"), ("deferred", "kept"), ("agreed", "kept"), ("accepted", "kept"),
                ("not blocking", "kept"), ("still present", "open"), ("still open", "open"), ("not fixed", "open"), ("open", "open"),
                ("partly", "partial"), ("partial", "partial"), ("changed", "partial"),
                ("fixed", "fixed"), ("answered", "answered"), ("kept", "kept"), ("refuted", "dropped"),
                ("dropped", "dropped"), ("gone", "fixed"), ("resolved", "fixed")]


def _refs(block):
    out, last = [], None
    for m in REF_RX.finditer(block):
        if m.group(1):
            last = m.group(1)
            lo = int(m.group(2)); hi = int(m.group(3) or lo)
        elif last:
            lo = int(m.group(4)); hi = int(m.group(5) or lo)
        else:
            continue
        out.append((last, lo, hi))
    return out


def findings_of(text):
    """Findings per severity under '## Findings' (before any Recheck), each with its file:line refs."""
    body = re.split(r"^## Recheck", text, flags=re.M)[0]
    m = re.search(r"^## Findings[^\n]*\n(.*?)(?=^## (?!#))", body, re.M | re.S)
    if not m:
        return []
    out = []
    for sec in re.split(r"^### ", m.group(1), flags=re.M)[1:]:
        title, _, rest = sec.partition("\n")
        hits = [h for h in ITEM_RX.finditer(rest)
                if h.group(1) or (len(h.group(2).strip()) >= 3 and not h.group(2).strip().endswith(":"))]
        items = []
        if hits:
            for i, h in enumerate(hits):
                block = rest[h.start():hits[i + 1].start() if i + 1 < len(hits) else len(rest)]
                name = re.sub(r"\*+", "", h.group(2).strip() or h.group(3)).strip(" :—-")  # "**n1** text" puts the text after the bold
                items.append({"id": h.group(1) or "", "title": name[:200], "refs": _refs(block)})
        else:
            for line in re.findall(r"^(?:\d+\.|-) (.+)$", rest, re.M):
                items.append({"id": "", "title": line[:160], "refs": _refs(line)})
        out.append({"severity": title.strip(), "count": len(items), "items": items[:60]})
    return out


def _status_of(cell):
    t = re.sub(r"[*`]", "", cell).lower()
    for word, code in STATUS_WORDS:
        if word in t:
            return code
    return ""


def status_rows(text):
    """Rows of every markdown table with a 'Status' column, after the findings (rechecks, round tables)."""
    rows = []
    lines = text.splitlines()
    section = ""
    for i, line in enumerate(lines):
        if line.startswith("## ") or line.startswith("### "):
            section = line.lstrip("# ").strip()
        if not (line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1])):
            continue
        head = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        if "status" not in head:
            continue
        si = head.index("status")
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
            if len(cells) > si and _status_of(cells[si]):
                key = " ".join(c for k, c in enumerate(cells[:si]))
                note = " ".join(cells[si + 1:])
                rows.append({"key": key, "status": _status_of(cells[si]), "label": re.sub(r"[*`]", "", cells[si]),
                             "note": note[:300], "section": section})
            j += 1
    return rows


def _words(s):
    return {w for w in re.findall(r"[a-z0-9_]{3,}", s.lower())} - {"the", "and", "with", "for", "not", "from", "that", "this"}


def _match_row(item, rows):
    """The last row about this finding: by id first, then by shared words."""
    best = None
    fid = item["id"]
    for r in rows:
        k = re.sub(r"[*`]", "", r["key"]).strip()
        first = (k.split() or [""])[0]
        if fid and (first == fid or re.fullmatch(re.escape(fid) + r"[a-z]", first) or k.startswith(fid + " ")):
            best = r
    if best:
        return best
    tw = _words(item["title"])
    score_best = 0
    for r in rows:
        rw = _words(r["key"])
        if not rw or not tw:
            continue
        s = len(tw & rw) / len(tw | rw)
        if s >= 0.3 and s >= score_best:
            best, score_best = r, s
    return best


def _match_thread(item, threads):
    best, dist = None, 999
    for file, lo, hi in item["refs"]:
        for t in threads:
            if t["started_by"] != C.ME or not t.get("path"):
                continue
            path = t["path"]
            if not (path == file or path.endswith("/" + file)):
                continue
            for ln in (t.get("cur_line"), t.get("orig_line")):
                if ln is None:
                    continue
                d = 0 if lo <= ln <= hi else min(abs(ln - lo), abs(ln - hi))
                if d <= 3 and d < dist:
                    best, dist = t, d
        if best:
            break
    return best


def findings_with_status(p, reports, prs_by_number):
    """For each review round: its findings, the latest report status and the matching GitHub thread."""
    rows = []
    for rel in reports:  # later reports and later tables win
        path = os.path.join(R.PR_REVIEWS, rel)
        if os.path.exists(path):
            rows += status_rows(open(path).read())
    out = []
    for r in p.get("rounds", []):
        if r.get("kind") != "review" or not r.get("report"):
            continue
        path = os.path.join(R.PR_REVIEWS, r["report"])
        if not os.path.exists(path):
            continue
        threads = threads_of(prs_by_number.get(r.get("pr", p["number"])) or {})
        secs = findings_of(open(path).read())
        tally = {"items": 0, "gh_resolved": 0, "gh_open": 0, "fixed": 0, "open": 0}
        for sec in secs:
            for it in sec["items"]:
                tally["items"] += 1
                row = _match_row(it, rows)
                it["report_status"] = {k: row[k] for k in ("status", "label", "note", "section")} if row else None
                t = _match_thread(it, threads)
                it["thread"] = ({"resolved": t["resolved"], "outdated": t["outdated"], "last_by": t["last_by"],
                                 "url": t["url"], "where": f"{t['path']}:{t['line']}", "comments": len(t["comments"]),
                                 "others_last": t["last_by"] != C.ME}
                                if t else None)
                if t:
                    tally["gh_resolved" if t["resolved"] else "gh_open"] += 1
                if row and row["status"] in ("fixed", "answered", "kept", "dropped"):
                    tally["fixed"] += 1
                elif row and row["status"] in ("open", "partial"):
                    tally["open"] += 1
                it["refs"] = [f"{f}:{lo}" + (f"-{hi}" if hi != lo else "") for f, lo, hi in it["refs"][:4]]
        out.append({"round": r["n"], "pr": r.get("pr", p["number"]), "report": r["report"], "sections": secs,
                    "tally": tally})
    return out


def timeline(p, pr, number, mine=False):
    ev = []
    real = {r.get("databaseId") for r in our_reviews(pr)}
    starts = {}
    for t in threads_of(pr):
        starts[t["review_id"]] = starts.get(t["review_id"], 0) + 1
    for r in (pr.get("reviews") or {}).get("nodes") or []:
        if login(r) == C.ME and r.get("databaseId") not in real:
            continue  # a reply; it shows as a "reply" event from the thread below
        if login(r) != C.ME and r.get("state") == "COMMENTED" and not (r.get("body") or "").strip() \
                and not starts.get(r.get("databaseId")):
            continue  # someone else's thread reply, also shown as a "reply" event
        ev.append({"t": r.get("submittedAt"), "kind": "our_review" if login(r) == C.ME else "review",
                   "by": login(r), "state": r.get("state"), "url": r.get("url"),
                   "comments": (r.get("comments") or {}).get("totalCount", 0),
                   "sha": ((r.get("commit") or {}).get("oid") or "")[:8], "body": (r.get("body") or "")[:3000],
                   "review_id": r.get("databaseId")})
    for n in (pr.get("commits") or {}).get("nodes") or []:
        c = n["commit"]
        ev.append({"t": c.get("committedDate"), "kind": "commit", "sha": c["oid"][:8], "url": c.get("url"),
                   "by": ((c.get("author") or {}).get("user") or {}).get("login") or (c.get("author") or {}).get("name"),
                   "text": c.get("messageHeadline")})
    for c in (pr.get("comments") or {}).get("nodes") or []:
        ev.append({"t": c.get("createdAt"), "kind": "comment", "by": login(c), "url": c.get("url"),
                   "text": (c.get("body") or "")[:1500]})
    for t in threads_of(pr):
        for c in t["comments"][1:]:
            ev.append({"t": c["at"], "kind": "reply", "by": c["by"], "url": c["url"],
                       "where": f"{t['path']}:{t['line']}", "text": c["body"][:600]})
    for r in p.get("rounds", []):
        if r.get("pr", p["number"]) == number:
            t = r.get("checked_at")
            if not t:  # only a date is known: use the report's file time, or the start of that day
                rp = os.path.join(R.PR_REVIEWS, r["report"]) if r.get("report") else ""
                if rp and os.path.exists(rp):
                    t = dt.datetime.fromtimestamp(os.path.getmtime(rp), dt.timezone.utc).isoformat()
                elif r.get("date"):
                    t = r["date"] + "T00:00:00Z"
            ev.append({"t": t or "",
                       "kind": "round", "n": r["n"], "round_kind": r.get("kind"), "sha": r.get("head", "")[:8],
                       "passes": r.get("passes") or [],
                       "verdict": R.VERDICTS.get(r.get("verdict"), r.get("verdict")),
                       "approx": not r.get("checked_at")})
    ev.sort(key=lambda e: ts(e["t"]))
    # Group into rounds: everything up to our first review, then one group per review of ours.
    groups, cur = [], {"title": "Before the first review" if mine else "Before our review", "events": []}
    n_rev = 0
    for e in ev:
        start = (e["kind"] == "our_review") if not mine else \
            (e["kind"] == "review" and (e.get("state") in ("APPROVED", "CHANGES_REQUESTED") or e.get("body") or e.get("comments")))
        if start:
            groups.append(cur)
            n_rev += 1
            cur = {"title": (f"Review {n_rev} by {e.get('by')}" if mine else f"Our review {n_rev}"), "events": []}
        cur["events"].append(e)
    groups.append(cur)
    return [g for g in groups if g["events"]]


def detail(repo, number, force=False):
    reg = R.load()
    p = R.find(reg, repo, number)
    if not p:
        p = R.new_pr(repo, number)
        p["_untracked"] = True
    s = summary(p, force=force)
    pr = s["_pr"]
    by_review = {}
    for t in threads_of(pr):
        by_review.setdefault(t["review_id"], []).append(t)
    reviews = []
    for r in our_reviews(pr) + [r for r in (pr.get("reviews") or {}).get("nodes") or [] if login(r) != C.ME]:
        th = by_review.get(r.get("databaseId"), [])
        if not th and login(r) != C.ME and not (r.get("body") or "").strip():
            continue
        reviews.append({"id": r.get("databaseId"), "by": login(r), "state": r.get("state"),
                        "at": r.get("submittedAt"), "url": r.get("url"), "sha": ((r.get("commit") or {}).get("oid") or "")[:8],
                        "comments": (r.get("comments") or {}).get("totalCount", 0), "body": (r.get("body") or "")[:3000],
                        "threads": th, "ours": login(r) == C.ME,
                        "open": sum(1 for t in th if not t["resolved"]),
                        "resolved": sum(1 for t in th if t["resolved"])})
    rounds = []
    for r in p.get("rounds", []):
        rep_path = os.path.join(R.PR_REVIEWS, r["report"]) if r.get("report") else ""
        text = open(rep_path).read() if rep_path and os.path.exists(rep_path) else ""
        rounds.append(dict(r, gates_data=C.round_gates(p, r),
                           findings=[],
                           report_exists=bool(text),
                           verdict_label=R.VERDICTS.get(r.get("verdict"), r.get("verdict"))))
    older = []
    for n in p.get("previous_numbers", []):
        opr = C.gh_pr(repo, n, force=force)
        older.append({"number": n, "title": opr.get("title"), "state": opr.get("state"), "url": opr.get("url"),
                      "timeline": timeline(p, opr, n) if not opr.get("_error") else []})
    prs_by_number = {number: pr}
    for n in p.get("previous_numbers", []):
        prs_by_number[n] = C.gh_pr(repo, n)
    reports = []
    for r in p.get("rounds", []):
        if r.get("report") and r["report"] not in reports:
            reports.append(r["report"])
    findings = findings_with_status(p, reports, prs_by_number) if not p.get("_untracked") else []
    folder = os.path.join(R.PR_REVIEWS, repo, str(number))
    files = []
    for n in [number] + p.get("previous_numbers", []):
        fd = os.path.join(R.PR_REVIEWS, repo, str(n))
        if os.path.isdir(fd):
            for f in sorted(os.listdir(fd)):
                if not f.startswith("."):
                    files.append(os.path.join(fd, f))
    return {
        "summary": public(s), "entry": {k: v for k, v in p.items() if k != "rounds"},
        "tracked": not p.get("_untracked"),
        "header": {"title": pr.get("title"), "url": pr.get("url"), "author": login(pr) if pr.get("author") else p.get("author"),
                   "branch": pr.get("headRefName"), "base": pr.get("baseRefName"), "head": pr.get("headRefOid"),
                   "state": pr.get("state"), "draft": pr.get("isDraft"), "mergeable": pr.get("mergeable"),
                   "merge_state": pr.get("mergeStateStatus"), "review_decision": pr.get("reviewDecision"),
                   "additions": pr.get("additions"), "deletions": pr.get("deletions"), "files": pr.get("changedFiles"),
                   "ci": s["ci"], "commits": (pr.get("commits") or {}).get("totalCount"),
                   "body": pr.get("body") or "", "created": pr.get("createdAt")},
        "timeline": timeline(p, pr, number, mine=login(pr) == C.ME) if not s["error"] else [],
        "mine": dict(my_state(repo, number), sessions=my_sessions(repo, number, pr.get("headRefName"), force=force))
        if login(pr) == C.ME else None,
        "older": older, "reviews": reviews, "rounds": rounds, "findings": findings,
        "links": links_for(repo, number, pr) if not s["error"] else {"group": None, "suggested": []},
        "new_commits": [{"sha": c["oid"][:8], "text": c.get("messageHeadline"), "at": c.get("committedDate"),
                         "url": c.get("url")} for c in s["_new_commits"]],
        "replies": s["_replies"], "since": s["_since"],
        "files": [os.path.relpath(f, R.WUP) for f in files], "folder": os.path.relpath(folder, R.WUP),
    }


def pr_files(repo, number):
    """Changed files with patches, plus our review threads on each file."""
    data = C.gh_pr_files(repo, number)
    if data.get("_error"):
        return data
    pr = C.gh_pr(repo, number)
    by_path = {}
    for t in threads_of(pr):
        by_path.setdefault(t["path"], []).append(
            {"line": t["line"], "resolved": t["resolved"], "url": t["url"], "by": t["started_by"],
             "ours": t["started_by"] == C.ME, "body": (t["comments"][0]["body"] or "")[:200]})
    files = []
    for f in data["files"]:
        th = by_path.get(f["filename"], [])
        files.append(dict(f, threads=th, open=sum(1 for t in th if not t["resolved"])))
    return {"files": files, "head": (pr.get("headRefOid") or "")[:8],
            "additions": sum(f["additions"] for f in files), "deletions": sum(f["deletions"] for f in files)}


def open_prs(repo):
    """Open PRs of a repo for the New review picker (cached GitHub data)."""
    reg = R.load()
    asked = {o["number"] for o in C.gh_requested(repo)}
    seen, out = set(), []
    for o in C.gh_open_prs(repo) + C.gh_requested(repo):
        n = o["number"]
        author = (o.get("author") or {}).get("login") or ""
        if n in seen or author in EXCLUDE_AUTHORS:
            continue
        seen.add(n)
        p = R.find(reg, repo, n)
        out.append({"number": n, "title": o.get("title", ""), "author": author, "draft": o.get("isDraft", False),
                    "updated": o.get("updatedAt", ""), "base": o.get("baseRefName", ""),
                    "requested_me": n in asked, "tracked": bool(p and not p.get("archived")),
                    "has_session": bool(p and R.current_session(p))})
    out.sort(key=lambda x: (not x["requested_me"], x["has_session"], x["updated"] and -ts(x["updated"])))
    return {"repo": repo, "prs": out}


# ---------------------------------------------------------------- linked PRs

PR_URL_RX = re.compile(r"github\.com/%s/([\w-]+)/pull/(\d+)" % R.ORG)
PR_REF_RX = re.compile(r"\b(%s)\s*#(\d+)" % "|".join(re.escape(r) for r in sorted(R.REPO_DIRS, key=len, reverse=True)))


def _brief(repo, n):
    pr = C.gh_pr(repo, n)
    reg = R.load()
    p = R.find(reg, repo, n)
    return {"repo": repo, "number": n, "title": pr.get("title") or (p or {}).get("title") or "",
            "state": pr.get("state", ""), "author": login(pr) if pr.get("author") else "",
            "branch": pr.get("headRefName", ""), "tracked": bool(p and not p.get("archived")),
            "verdict": (p or {}).get("verdict", ""), "verdict_label": R.VERDICTS.get((p or {}).get("verdict", ""), "")}


def links_for(repo, number, pr):
    """The PR's link group (with each member's title and state) and suggested links:
    open PRs in other repos with the same branch name, and PRs named in the description."""
    reg = R.load()
    g = R.link_of(reg, repo, number)
    group = None
    if g:
        group = {"id": g["id"], "name": g["name"],
                 "prs": [_brief(x["repo"], x["number"]) for x in g["prs"]
                         if not (x["repo"] == repo and x["number"] == number)]}
    linked = {(x["repo"], x["number"]) for x in (g or {}).get("prs", [])}
    sugg, seen = [], {(repo, number)} | linked
    branch = pr.get("headRefName") or ""
    if branch and branch not in ("dev", "main", "master"):
        for r in R.REPO_DIRS:
            if r == repo:
                continue
            for o in C.gh_open_prs(r):
                if o.get("headRefName") == branch and (r, o["number"]) not in seen:
                    seen.add((r, o["number"]))
                    sugg.append({"repo": r, "number": o["number"], "title": o["title"], "why": f"same branch {branch}"})
    for rx in (PR_URL_RX, PR_REF_RX):
        for r, n in rx.findall(pr.get("body") or ""):
            n = int(n)
            if r in R.REPO_DIRS and (r, n) not in seen:
                seen.add((r, n))
                o = C.gh_pr(r, n)
                if not o.get("_error"):
                    sugg.append({"repo": r, "number": n, "title": o.get("title", ""), "why": "named in the description"})
    return {"group": group, "suggested": sugg[:10]}


def link_label(reg, repo, number):
    g = R.link_of(reg, repo, number)
    return {"id": g["id"], "name": g["name"], "size": len(g["prs"])} if g else None



# ---------------------------------------------------------------- my PRs (I am the author)

BOT = re.compile(r"(\[bot\]$|^github-actions$|^coderabbitai$|^copilot)", re.I)


def my_state(repo, number, force=False):
    """Review progress of a PR I wrote: reviewers, threads to answer, CI, section."""
    pr = C.gh_pr(repo, number, force=force)
    if pr.get("_error"):
        return {"repo": repo, "number": number, "error": pr["_error"], "section": "waiting_review", "title": f"PR #{number}"}
    latest = {}
    for r in (pr.get("reviews") or {}).get("nodes") or []:
        who = login(r)
        if who == C.ME or BOT.search(who):
            continue
        st = r.get("state")
        if st in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED") or who not in latest:
            latest[who] = {"by": who, "state": st, "at": r.get("submittedAt"), "url": r.get("url")}
        elif latest[who]["state"] == "COMMENTED":
            latest[who].update({"at": r.get("submittedAt"), "url": r.get("url")})
    requested = [((n.get("requestedReviewer") or {}).get("login") or "team")
                 for n in (pr.get("reviewRequests") or {}).get("nodes") or []]
    for who in requested:
        latest.setdefault(who, {"by": who, "state": "REQUESTED", "at": "", "url": ""})
        if latest[who]["state"] != "REQUESTED":
            latest[who]["asked_again"] = True
    threads = threads_of(pr)
    to_answer = [t for t in threads if not t["resolved"] and t["last_by"] != C.ME and not BOT.search(t["last_by"])]
    open_threads = [t for t in threads if not t["resolved"]]
    commits = [n["commit"] for n in (pr.get("commits") or {}).get("nodes") or []]
    last_push = max([ts(c.get("committedDate")) for c in commits] or [0])
    changes = [v for v in latest.values() if v["state"] == "CHANGES_REQUESTED"]
    approvals = [v for v in latest.values() if v["state"] == "APPROVED"]
    fixed_since = bool(changes) and all(last_push > ts(v["at"]) for v in changes)
    ci = C.ci_summary(pr)
    if pr.get("state") == "MERGED":
        sec = "merged"
    elif pr.get("state") == "CLOSED":
        sec = "closed"
    elif pr.get("isDraft"):
        sec = "draft"
    elif to_answer or (changes and not fixed_since):
        sec = "to_fix"
    elif changes and fixed_since:
        sec = "rereview"
    elif approvals or pr.get("reviewDecision") == "APPROVED":
        sec = "approved"
    else:
        sec = "waiting_review"
    reg = R.load()
    return {
        "repo": repo, "number": number, "title": pr.get("title", ""), "author": login(pr), "mine": True,
        "gh_state": pr.get("state"), "draft": pr.get("isDraft"), "section": sec,
        "base": pr.get("baseRefName"), "branch": pr.get("headRefName"), "updated": pr.get("updatedAt"),
        "reviewers": sorted(latest.values(), key=lambda v: v["by"]),
        "approvals": len(approvals), "changes": len(changes),
        "pending": sum(1 for v in latest.values() if v["state"] == "REQUESTED"),
        "to_answer": len(to_answer), "open_threads": len(open_threads),
        "to_answer_list": [{"where": f"{t['path']}:{t['line']}", "by": t["last_by"], "at": t["last_at"], "url": t["url"],
                            "body": (t["comments"][-1]["body"] or "")[:300]} for t in to_answer[:40]],
        "review_decision": pr.get("reviewDecision"), "merge_state": pr.get("mergeStateStatus"), "ci": ci,
        "link": link_label(reg, repo, number),
        "last_address": (((reg.get("mine") or {}).get(f"{repo}#{number}") or {}).get("address") or [None])[-1],
    }


def my_prs(force=False, merged_days=14):
    items = []
    cutoff = time.time() - merged_days * 86400
    for repo in R.REPO_DIRS:
        for o in C.gh_my_prs(repo, force=force):
            st = o.get("state")
            if st == "OPEN" or (st == "MERGED" and ts(o.get("mergedAt")) > cutoff):
                items.append((repo, o["number"]))
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda x: my_state(x[0], x[1], force), items))
    def warm():  # find the sessions of open PRs ahead of time, so their pages open fast
        for s in res:
            if s.get("gh_state") == "OPEN":
                try:
                    C.find_sessions(s["repo"], s["number"], s.get("branch"))
                except Exception:  # a failed search only means a slower page later
                    pass
    threading.Thread(target=warm, daemon=True).start()
    reg = R.load()
    live = C.live_sessions()
    for s in res:
        sid = ((reg.get("mine") or {}).get(f"{s['repo']}#{s['number']}") or {}).get("session")
        lv = live.get(sid) if sid else None
        s["session_live"] = {"tty": lv["tty"], "status": lv["status"]} if lv else None
    return {"prs": res, "merged_days": merged_days}



def my_sessions(repo, number, branch, force=False):
    """Sessions for one of my PRs: the one I chose or started (registry), then found ones; each with
    its live state (open in a tab, busy/idle) or closed."""
    reg = R.load()
    chosen = (reg.get("mine") or {}).get(f"{repo}#{number}") or {}
    live = C.live_sessions()
    found = C.find_sessions(repo, number, branch, force=force)
    if chosen.get("session") and not any(f["session"] == chosen["session"] for f in found):
        found.insert(0, {"session": chosen["session"], "why": "started or picked here", "mtime": 0,
                         "cwd": chosen.get("cwd", ""), "project": ""})
    out = []
    for f in found:
        lv = live.get(f["session"])
        out.append(dict(f, chosen=f["session"] == chosen.get("session"),
                        live=bool(lv), tty=(lv or {}).get("tty", ""), status=(lv or {}).get("status", ""),
                        cwd=f.get("cwd") or (lv or {}).get("cwd", "")))
    # The chosen one first; then by match strength (the search order). Being open is not a reason to win:
    # a planning session that mentions every PR is often open.
    out.sort(key=lambda x: not x["chosen"])
    return out
