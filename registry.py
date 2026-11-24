"""Registry of PR reviews: pr-reviews/registry.json is the source of truth.

STATUS.md is generated from it (write_status). Standard library only.
"""
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import tempfile

import config as CFG

WUP = CFG.WORKSPACE
PR_REVIEWS = CFG.PR_REVIEWS
REGISTRY = os.path.join(PR_REVIEWS, "registry.json")
STATUS_MD = os.path.join(PR_REVIEWS, "STATUS.md")
LOCK = os.path.join(PR_REVIEWS, ".registry.lock")
ORG = CFG.ORG
# GitHub repo name -> local folder under the workspace
REPO_DIRS = CFG.REPOS

VERDICTS = {
    "request_changes": "Request changes",
    "approve_with_nits": "Approve with nits",
    "approved": "Approved",
    "not_reviewed": "Not reviewed",
    "on_hold": "On hold",
    "in_progress": "In progress",
}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_dir(repo):
    return os.path.join(WUP, REPO_DIRS.get(repo, repo))


def verdict_code(text):
    t = (text or "").lower().replace("*", "")
    if "request" in t and "change" in t:
        return "request_changes"
    if "nit" in t:
        return "approve_with_nits"
    if "approve" in t:
        return "approved"
    if "hold" in t:
        return "on_hold"
    if "progress" in t or "running" in t:
        return "in_progress"
    return "not_reviewed"


@contextlib.contextmanager
def locked():
    os.makedirs(PR_REVIEWS, exist_ok=True)
    with open(LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load():
    if not os.path.exists(REGISTRY):
        return {"version": 1, "updated": now_iso(), "prs": [], "links": []}
    with open(REGISTRY) as f:
        return json.load(f)


def save(reg):
    reg["updated"] = now_iso()
    reg["prs"].sort(key=lambda p: (p["repo"], p["number"]))
    fd, tmp = tempfile.mkstemp(dir=PR_REVIEWS, prefix=".registry-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(reg, f, indent=2)
        f.write("\n")
    os.replace(tmp, REGISTRY)
    write_status(reg)


def find(reg, repo, number):
    number = int(number)
    for p in reg["prs"]:
        if p["repo"] == repo and (p["number"] == number or number in p.get("previous_numbers", [])):
            return p
    return None


def new_pr(repo, number, **kw):
    p = {
        "repo": repo, "number": int(number), "title": "", "author": "", "previous_numbers": [],
        "sessions": [], "tab": "", "verdict": "not_reviewed", "verdict_note": "",
        "rounds": [], "posted": [], "posted_note": "", "waiting_on": "", "notes": "",
        "added": now_iso(),
    }
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def active(reg):
    """PRs still on the list (removed ones stay in the registry with archived: true)."""
    return [p for p in reg["prs"] if not p.get("archived")]


# ---------------------------------------------------------------- linked PRs
# reg["links"] = [{"id": slug, "name": text, "prs": [{"repo": r, "number": n}], "created": iso}]
# A PR is in at most one group. Linked PRs need not be tracked.

def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "group"


def link_of(reg, repo, number):
    for g in reg.get("links", []):
        if any(x["repo"] == repo and x["number"] == int(number) for x in g["prs"]):
            return g
    return None


def link(reg, name, members):
    """Put members (list of (repo, number)) into the group called name; make it if needed.
    A member already in another group moves here."""
    reg.setdefault("links", [])
    gid = _slug(name)
    g = next((x for x in reg["links"] if x["id"] == gid), None)
    if not g:
        g = {"id": gid, "name": name.strip(), "prs": [], "created": now_iso()}
        reg["links"].append(g)
    for repo, n in members:
        old = link_of(reg, repo, n)
        if old and old is not g:
            old["prs"] = [x for x in old["prs"] if not (x["repo"] == repo and x["number"] == int(n))]
        if not any(x["repo"] == repo and x["number"] == int(n) for x in g["prs"]):
            g["prs"].append({"repo": repo, "number": int(n)})
    reg["links"] = [x for x in reg["links"] if x["prs"]]
    return g


def unlink(reg, repo, number):
    g = link_of(reg, repo, number)
    if g:
        g["prs"] = [x for x in g["prs"] if not (x["repo"] == repo and x["number"] == int(number))]
        if len(g["prs"]) < 2:  # a group of one links nothing
            reg["links"].remove(g)
    return g


def current_session(p):
    return p["sessions"][-1]["id"] if p.get("sessions") else ""


def latest_round(p):
    return p["rounds"][-1] if p.get("rounds") else None


# ---------------------------------------------------------------- STATUS.md

def _cell(s):
    return (s or "—").replace("|", "/").replace("\n", " ")


def write_status(reg):
    lines = [
        "# PR review status", "",
        "Generated from `registry.json` by review-ops. Do not edit by hand: use",
        "`review-ops update|round|add` (see `review-ops help`). Dashboard: http://127.0.0.1:8765", "",
        f"Updated: {reg.get('updated', now_iso()).replace('T', ' ').replace('Z', ' UTC')}", "",
        "| PR | Author | Tab | Head reviewed | Verdict | Round | Posted | Waiting on |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in active(reg):
        name = f"{p['repo']} #{p['number']}"
        if p.get("previous_numbers"):
            name += " (was " + ", ".join(f"#{n}" for n in p["previous_numbers"]) + ")"
        r = latest_round(p)
        head = (r or {}).get("head", "")[:8]
        if r and r.get("head_note"):
            head += f" ({r['head_note']})"
        verdict = VERDICTS.get(p["verdict"], p["verdict"])
        if p["verdict"] == "approved":
            verdict = f"**{verdict}**"
        if p.get("verdict_note"):
            verdict += f" ({p['verdict_note']})"
        posted = p.get("posted_note") or ("Yes" if p.get("posted") else "")
        lines.append("| " + " | ".join(_cell(x) for x in [
            name, p.get("author"), p.get("tab"), head, verdict,
            str(len(p["rounds"])) if p["rounds"] else "", posted, p.get("waiting_on")]) + " |")
    if reg.get("links"):
        lines += ["", "## Linked PRs", ""]
        for g in reg["links"]:
            lines.append(f"- **{g['name']}**: " + ", ".join(f"{x['repo']} #{x['number']}" for x in g["prs"]))
    lines += [
        "", "Reports: `pr-reviews/<repo>/<PR>/<date>-review.md`", "",
        "## Review sessions (resume to keep context)", "",
        "Closing a tab is safe. To continue a review with its full context, open a tab and run:",
        f"`cd {WUP.replace(os.path.expanduser('~'), '~', 1)}/<repo> && claude --resume <session id>`", "",
        "| PR | Session id |", "|---|---|",
    ]
    for p in active(reg):
        for s in p.get("sessions", []):
            nums = " / ".join(f"#{n}" for n in [p["number"]] + p.get("previous_numbers", []))
            lines.append(f"| {p['repo']} {nums} | {s['id']} |")
    lines.append("")
    fd, tmp = tempfile.mkstemp(dir=PR_REVIEWS, prefix=".status-", suffix=".md")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines))
    os.replace(tmp, STATUS_MD)


# ---------------------------------------------------------------- import

SHA = re.compile(r"`([0-9a-f]{7,40})`")
PASS_WORDS = [
    ("code-review", r"/code-review"), ("pr-reviewer", r"pr-reviewer"), ("self-review", r"self-review"),
    ("crapper", r"crapper"), ("mutator", r"\bmutator\b"), ("cargo-mutants", r"cargo-mutants|\bcmut\b"),
    ("validation", r"^## Validation"),
]


def _passes(text):
    return [name for name, rx in PASS_WORDS if re.search(rx, text, re.M | re.I)]


def _verdict_in(text):
    m = re.search(r"^#+ Verdict[^\n]*?:\s*([^\n]+)", text, re.M | re.I)
    if m:
        return verdict_code(m.group(1))
    m = re.search(r"^#+ Verdict[^\n]*\n+\s*\**([^\n]+)", text, re.M | re.I)
    return verdict_code(m.group(1)) if m else ""


def rounds_from_report(rel_report, number):
    """Round 1 from the report top; one more round per '## Recheck' section."""
    path = os.path.join(PR_REVIEWS, rel_report)
    text = open(path).read()
    date = (re.search(r"(\d{4}-\d{2}-\d{2})-review\.md$", rel_report) or [None, ""])[1]
    gates_rel = rel_report.replace("-review.md", "-gates")
    gates_rel = gates_rel if os.path.isdir(os.path.join(PR_REVIEWS, gates_rel)) else ""
    parts = re.split(r"^(## Recheck[^\n]*)$", text, flags=re.M)
    head_m = SHA.search("\n".join(text.splitlines()[:6]))
    info = os.path.join(PR_REVIEWS, gates_rel, "info") if gates_rel else ""
    if info and os.path.isfile(info):
        m = re.search(r"\bhead=([0-9a-f]{7,40})", open(info).read())
        head_m = m or head_m
    rounds = [{
        "n": 1, "kind": "review", "pr": number, "head": head_m.group(1) if head_m else "",
        "date": date, "passes": _passes(parts[0]), "verdict": _verdict_in(parts[0]),
        "report": rel_report, "gates": gates_rel, "section": "",
    }]
    for i in range(1, len(parts), 2):
        heading, body = parts[i], parts[i + 1]
        m = SHA.search(heading) or re.search(r"\bat ([0-9a-f]{7,40})\b", heading) or SHA.search(body)
        n = len(rounds) + 1
        sub = ""
        if gates_rel:
            for cand in (f"recheck{len(rounds)}", "recheck" if len(rounds) == 1 else None):
                if cand and os.path.isdir(os.path.join(PR_REVIEWS, gates_rel, cand)):
                    sub = f"{gates_rel}/{cand}"
                    break
        rounds.append({
            "n": n, "kind": "recheck", "pr": number, "head": m.group(1) if m else "",
            "date": date, "passes": _passes(body), "verdict": _verdict_in(body) or rounds[-1]["verdict"],
            "report": rel_report, "gates": sub or gates_rel, "section": heading.lstrip("# ").strip(),
            # the recheck was written by the time the report file was last saved
            "checked_at": dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "checked_at_source": "report file time",
        })
    return rounds


def _parse_status_md(text):
    rows, sessions = [], {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and re.match(r"[\w-]+ #\d+", cells[0]):
            rows.append(cells)
        elif len(cells) == 2 and re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", cells[1]):
            for n in re.findall(r"#(\d+)", cells[0]):
                sessions.setdefault(int(n), cells[1])
    return rows, sessions


def import_existing(status_text=None):
    """One-time import from STATUS.md and the report folders. Keeps entries already present."""
    reg = load()
    text = status_text if status_text is not None else open(STATUS_MD).read()
    rows, sessions = _parse_status_md(text)
    for cells in rows:
        repo, num = re.match(r"([\w-]+) #(\d+)", cells[0]).groups()
        num = int(num)
        prev = [int(x) for x in re.findall(r"was #(\d+)", cells[0])]
        if find(reg, repo, num):
            continue
        dash = lambda s: "" if s in ("—", "-", "") else s
        verdict_cell = cells[4].replace("*", "")
        note = (re.search(r"\(([^)]*)\)", verdict_cell) or [None, ""])[1]
        p = new_pr(repo, num, author=cells[1], tab=dash(cells[2]), previous_numbers=prev,
                   verdict=verdict_code(verdict_cell), verdict_note=note,
                   posted_note=dash(cells[5]), waiting_on=dash(cells[6]))
        sid = sessions.get(num) or next((sessions[n] for n in prev if n in sessions), "")
        if sid:
            p["sessions"].append({"id": sid, "tab": p["tab"], "started": "", "kind": "review"})
        head_note = (re.search(r"\(([^)]*)\)", cells[3]) or [None, ""])[1]
        for n in prev + [num]:
            folder = os.path.join(PR_REVIEWS, repo, str(n))
            if not os.path.isdir(folder):
                continue
            for rep in sorted(f for f in os.listdir(folder) if f.endswith("-review.md")):
                for r in rounds_from_report(f"{repo}/{n}/{rep}", n):
                    r["n"] = len(p["rounds"]) + 1
                    p["rounds"].append(r)
        if p["rounds"]:
            p["rounds"][-1]["head_note"] = head_note
            m = re.match(r"([0-9a-f]{7,40})", cells[3])
            if m:  # STATUS.md names the head that was actually reviewed
                p["rounds"][-1]["head"] = m.group(1)
            # The table's verdict is the latest word; keep it on the last round.
            if p["verdict"] in ("request_changes", "approve_with_nits", "approved"):
                p["rounds"][-1]["verdict"] = p["verdict"]
        reg["prs"].append(p)
    save(reg)
    return reg
