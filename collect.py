"""Read-only data collectors: GitHub (gh, cached), sessions (ps + transcripts), gates, box status.

Nothing here writes to GitHub, the repos or the transcripts.
"""
import glob
import json
import os
import re
import subprocess
import threading
import time

import config as CFG
import registry as R

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
GH_TTL = 180          # seconds a GitHub answer stays fresh
BOX_TIMEOUT = 25      # seconds for one quality-remote status call
ME = CFG.GITHUB_USER
QUALITY_REMOTE = CFG.QUALITY_REMOTE
FOCUS = os.path.join(CFG.TOOLS, "focus.py")
IT2PY = CFG.ITERM_PYTHON
ACTIVE_SECS = 90      # transcript written this recently = session is working

_lock = threading.Lock()
_mem = {}


def run(cmd, timeout=20, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as e:
        return 127, "", str(e)


def transcripts_dir(repo):
    return os.path.expanduser("~/.claude/projects/" + R.repo_dir(repo).replace("/", "-"))


# ---------------------------------------------------------------- GitHub

PR_QUERY = """
query($owner:String!,$repo:String!,$n:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$n){
 number title url body state isDraft merged mergedAt closedAt author{login} headRefName baseRefName headRefOid
 mergeable mergeStateStatus reviewDecision additions deletions changedFiles updatedAt createdAt
 commits(last:100){totalCount nodes{commit{oid committedDate messageHeadline url
   author{user{login} name}
   statusCheckRollup{state contexts(first:60){nodes{
     __typename ... on CheckRun{name status conclusion detailsUrl}
     ... on StatusContext{context state targetUrl}}}}}}}
 reviewRequests(first:20){nodes{requestedReviewer{__typename ... on User{login}}}}
 reviews(first:100){nodes{databaseId author{login} state submittedAt url body commit{oid} comments{totalCount}}}
 reviewThreads(first:100){nodes{id isResolved isOutdated path line originalLine
   comments(first:60){nodes{databaseId author{login} body createdAt url pullRequestReview{databaseId}}}}}
 comments(first:100){nodes{databaseId author{login} body createdAt url}}
}}}"""


def _cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, re.sub(r"[^\w.-]", "_", key) + ".json")


_inflight = set()


def _swr(path, force, refetch):
    """Stale-while-revalidate: return the cached answer at once (None if there is none or force).
    When it is older than GH_TTL, refetch it in a background thread so the next call is fresh."""
    if force or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - os.path.getmtime(path) >= GH_TTL:
        with _lock:
            if path in _inflight:
                return data
            _inflight.add(path)

        def work():
            try:
                refetch()
            finally:
                with _lock:
                    _inflight.discard(path)
        threading.Thread(target=work, daemon=True).start()
    return data


def gh_pr(repo, number, force=False):
    """PR data from one GraphQL call, cached on disk; refreshed in the background after GH_TTL seconds."""
    key = f"gh-{repo}-{number}"
    path = _cache_path(key)
    cached = _swr(path, force, lambda: gh_pr(repo, number, force=True))
    if cached is not None:
        return cached
    rc, out, err = run(["gh", "api", "graphql", "-F", f"owner={R.ORG}", "-F", f"repo={repo}",
                        "-F", f"n={int(number)}", "-f", f"query={PR_QUERY}"], timeout=30)
    if rc != 0:
        if os.path.exists(path):  # keep showing the old answer
            with open(path) as f:
                d = json.load(f)
            d["_error"] = "GitHub call failed; showing older data"
            return d
        return {"_error": "GitHub call failed: " + err.strip().splitlines()[-1][:200] if err.strip() else "GitHub call failed"}
    try:
        pr = json.loads(out)["data"]["repository"]["pullRequest"]
    except (KeyError, TypeError, ValueError):
        return {"_error": "PR not found on GitHub"}
    if pr is None:
        return {"_error": "PR not found on GitHub"}
    pr["_fetched"] = time.time()
    with open(path, "w") as f:
        json.dump(pr, f)
    return pr


def gh_open_prs(repo, force=False):
    path = _cache_path(f"gh-open-{repo}")
    cached = _swr(path, force, lambda: gh_open_prs(repo, force=True))
    if cached is not None:
        return cached
    rc, out, _ = run(["gh", "pr", "list", "-R", f"{R.ORG}/{repo}", "--state", "open", "--limit", "100",
                      "--json", "number,title,author,isDraft,updatedAt,headRefName,baseRefName,reviewRequests"],
                     timeout=30)
    data = json.loads(out) if rc == 0 and out.strip() else []
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def gh_pr_files(repo, number, force=False):
    """Changed files of a PR with their patches (GitHub REST, cached like the rest)."""
    path = _cache_path(f"gh-files-{repo}-{number}")
    cached = _swr(path, force, lambda: gh_pr_files(repo, number, force=True))
    if cached is not None:
        return cached
    rc, out, err = run(["gh", "api", f"repos/{R.ORG}/{repo}/pulls/{int(number)}/files", "--paginate",
                        "--jq", ".[] | {filename, previous_filename, status, additions, deletions, patch}"], timeout=60)
    if rc != 0:
        return {"_error": "GitHub call failed: " + (err.strip().splitlines() or [""])[-1][:200]}
    files = [json.loads(l) for l in out.splitlines() if l.strip()]
    data = {"files": files}
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def gh_my_prs(repo, force=False):
    """PRs the user authored in a repo (open and recent), newest first."""
    path = _cache_path(f"gh-mine-{repo}")
    cached = _swr(path, force, lambda: gh_my_prs(repo, force=True))
    if cached is not None:
        return cached
    rc, out, _ = run(["gh", "pr", "list", "-R", f"{R.ORG}/{repo}", "--author", "@me", "--state", "all",
                      "--limit", "40", "--json", "number,title,state,isDraft,updatedAt,mergedAt,closedAt"], timeout=30)
    data = json.loads(out) if rc == 0 and out.strip() else []
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def gh_requested(repo, force=False):
    """Open PRs that ask the user for a review (GitHub search, so the 50-PR list limit does not apply)."""
    path = _cache_path(f"gh-requested-{repo}")
    cached = _swr(path, force, lambda: gh_requested(repo, force=True))
    if cached is not None:
        return cached
    rc, out, _ = run(["gh", "pr", "list", "-R", f"{R.ORG}/{repo}", "--state", "open", "--limit", "100",
                      "--search", "review-requested:@me",
                      "--json", "number,title,author,isDraft,updatedAt,headRefName,baseRefName"], timeout=30)
    data = json.loads(out) if rc == 0 and out.strip() else []
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def ci_summary(pr):
    nodes = (pr.get("commits") or {}).get("nodes") or []
    if not nodes:
        return {"state": "", "failing": [], "pending": []}
    roll = nodes[-1]["commit"].get("statusCheckRollup") or {}
    failing, pending = [], []
    for c in (roll.get("contexts") or {}).get("nodes") or []:
        if c.get("__typename") == "CheckRun":
            name, st, concl = c.get("name"), c.get("status"), c.get("conclusion")
            if st != "COMPLETED":
                pending.append(name)
            elif concl in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"):
                failing.append(name)
        else:
            if c.get("state") in ("FAILURE", "ERROR"):
                failing.append(c.get("context"))
            elif c.get("state") == "PENDING":
                pending.append(c.get("context"))
    return {"state": roll.get("state", ""), "failing": failing, "pending": pending}


# ---------------------------------------------------------------- sessions

def processes():
    """Claude processes: [{pid, tty, cmd}]. Cached for 5 s."""
    with _lock:
        c = _mem.get("ps")
        if c and time.time() - c[0] < 5:
            return c[1]
    rc, out, _ = run(["ps", "-axo", "pid=,tty=,command="], timeout=10)
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, tty, cmd = parts
        exe = os.path.basename(cmd.split()[0])
        if exe == "claude" or re.match(r"\d+\.\d+\.\d+$", exe) and "/claude/" in cmd:
            procs.append({"pid": int(pid), "tty": tty, "cmd": cmd[:600]})
    with _lock:
        _mem["ps"] = (time.time(), procs)
    return procs


def tabs():
    """iTerm tabs: {ttysNNN: title}. Cached for 20 s."""
    with _lock:
        c = _mem.get("tabs")
        if c and time.time() - c[0] < 20:
            return c[1]
    rc, out, _ = run(["perl", "-e", "alarm 15; exec @ARGV", IT2PY, FOCUS, "--list"], timeout=20)
    res = {}
    for line in out.splitlines():
        m = re.match(r"/dev/(tty\w+)\s+(.*)", line.strip())
        if m:
            res[m.group(1)] = m.group(2)
    with _lock:
        _mem["tabs"] = (time.time(), res)
    return res


def _tail_entries(path, max_bytes=400_000):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", "replace")
    out = []
    for line in data.splitlines()[1 if size > max_bytes else 0:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _text_of(msg):
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


ASKS = re.compile(r"(\?\s*$|confirm|shall i|should i|want me to|ok to post|go ahead|say the word|your call)",
                  re.I | re.M)


def transcript_info(repo, sid):
    path = os.path.join(transcripts_dir(repo), f"{sid}.jsonl")
    if not sid or not os.path.exists(path):
        return {"exists": False}
    mtime = os.path.getmtime(path)
    last_role, last_text, last_ts = "", "", ""
    for e in reversed(_tail_entries(path)):
        if e.get("type") in ("assistant", "user") and e.get("message"):
            m = e["message"]
            if e["type"] == "user" and isinstance(m.get("content"), list) and \
                    all(isinstance(x, dict) and x.get("type") == "tool_result" for x in m["content"]):
                last_role = "tool"
                last_ts = e.get("timestamp", "")
                break
            t = _text_of(m)
            if e["type"] == "assistant" and not t.strip():
                last_role, last_ts = "assistant-tool", e.get("timestamp", "")
                break
            last_role, last_text, last_ts = e["type"], t, e.get("timestamp", "")
            break
    tail = last_text.strip()[-600:]
    return {"exists": True, "mtime": mtime, "last_role": last_role, "last_ts": last_ts,
            "last_text": tail, "asks": bool(last_role == "assistant" and ASKS.search(tail[-300:]))}


def session_state(p):
    """Live state of the PR's review session."""
    sid = R.current_session(p)
    procs = processes()
    nums = [p["number"]] + p.get("previous_numbers", [])
    brief_rx = re.compile(r"pr-reviews/%s/(%s)/brief" % (re.escape(p["repo"]), "|".join(map(str, nums))))
    proc = None
    live = live_sessions().get(sid) if sid else None
    if live:
        proc = {"pid": live["pid"], "tty": live["tty"], "cmd": ""}
    for pr in procs if not proc else []:
        if (sid and sid in pr["cmd"]) or brief_rx.search(pr["cmd"]):
            proc = pr
            break
    tinfo = transcript_info(p["repo"], sid) if sid else {"exists": False}
    st = {"session": sid, "tty": proc["tty"] if proc else "", "pid": proc["pid"] if proc else None,
          "transcript": tinfo}
    if not sid and not proc:
        st["state"] = "none"
    elif not proc:
        st["state"] = "closed"
    elif tinfo.get("exists") and time.time() - tinfo["mtime"] < ACTIVE_SECS and \
            tinfo.get("last_role") in ("tool", "assistant-tool", "user"):
        st["state"] = "working"
    else:
        st["state"] = "idle"
    if proc:
        st["tab_title"] = tabs().get(proc["tty"], "")
    st["waiting_for_you"] = st["state"] == "idle" and bool(tinfo.get("asks"))
    return st


# ---------------------------------------------------------------- gates

CRAP_ROW = re.compile(r"^(\S+)\s+(\S+)\s+(\d+)\s+([\d.]+)%\s+([\d.]+)\s*$")
MUT_ROW = re.compile(r"^(KILLED|SURVIVED|UNCOVERED|TIMEOUT|ERROR)\s+(\S+)\s+(.*)$")
CMUT_SUM = re.compile(r"(\d+) mutants tested[^:]*:\s*(.*)")


def parse_crap(path):
    rows, in_table = [], False
    with open(path, errors="replace") as f:
        for line in f:
            if line.startswith("Function") and "CRAP" in line:
                in_table = True
                continue
            if in_table:
                if line.startswith("---"):
                    continue
                m = CRAP_ROW.match(line.strip())
                if m:
                    rows.append({"fn": m.group(1), "ns": m.group(2), "cc": int(m.group(3)),
                                 "cov": float(m.group(4)), "crap": float(m.group(5))})
                elif line.strip() == "" and rows:
                    in_table = False
    seen, over = set(), []
    for r in sorted(rows, key=lambda r: -r["crap"]):
        k = (r["fn"], r["ns"])
        if r["crap"] > 10 and k not in seen:
            seen.add(k)
            over.append(r)
    return {"file": path, "functions": len(rows), "over10": over}


CURATED_ROW = re.compile(r"^\s*([\d.]+)\s+CC\s+(\d+)\s+cov\s+([\d.]+)%\s+(\S+)\s+\[([^\]]*)\]")


def parse_crap_curated(path):
    over = []
    for line in open(path, errors="replace"):
        m = CURATED_ROW.match(line)
        if m and float(m.group(1)) > 10:
            over.append({"fn": m.group(4), "ns": m.group(5), "cc": int(m.group(2)), "cov": float(m.group(3)),
                         "crap": float(m.group(1))})
    return {"file": path, "functions": len(over), "over10": over}


def parse_mutate(path):
    counts, survivors = {}, []
    with open(path, errors="replace") as f:
        for line in f:
            m = MUT_ROW.match(line.strip())
            if m:
                counts[m.group(1).lower()] = counts.get(m.group(1).lower(), 0) + 1
                if m.group(1) == "SURVIVED":
                    survivors.append(f"{m.group(2)} {m.group(3)}")
    if not counts:
        return None
    k, s = counts.get("killed", 0), counts.get("survived", 0)
    counts["score"] = round(100.0 * k / (k + s), 1) if k + s else None
    return {"file": path, "counts": counts, "survivors": survivors}


def parse_cmut_dir(d):
    def lines(name):
        p = os.path.join(d, name)
        return [l.rstrip() for l in open(p, errors="replace") if l.strip()] if os.path.exists(p) else []
    missed = lines("missed.txt")
    res = {"file": d, "caught": len(lines("caught.txt")), "missed": len(missed),
           "timeout": len(lines("timeout.txt")), "unviable": len(lines("unviable.txt")), "missed_list": missed}
    return res


def parse_cmut_txt(path):
    txt = open(path, errors="replace").read()
    m = None
    for m in CMUT_SUM.finditer(txt):
        pass
    if not m:
        return None
    res = {"file": path, "total": int(m.group(1)), "caught": 0, "missed": 0, "timeout": 0, "unviable": 0,
           "missed_list": re.findall(r"^MISSED\s+(.*)$", txt, re.M)}
    for n, word in re.findall(r"(\d+) (missed|caught|unviable|timeouts?)", m.group(2)):
        res["timeout" if word.startswith("timeout") else word] = int(n)
    return res


def gates_for(path, only_top=False):
    """Gate results in a folder: crapper, mutator and cargo-mutants, newest file of each kind."""
    if not path or not os.path.isdir(path):
        return {}
    files = []
    for root, dirs, fs in os.walk(path):
        rel = os.path.relpath(root, path)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if only_top and re.search(r"recheck", rel):
            dirs[:] = []
            continue
        if depth > 3:
            dirs[:] = []
            continue
        for f in fs:
            files.append(os.path.join(root, f))
    out = {"crap": None, "mutate": None, "cmut": []}
    curated = [f for f in files if re.search(r"/crap[^/]*over-10[^/]*\.txt$", f)]
    craps = [f for f in files if re.search(r"/crap[^/]*\.txt$", f) and "over-10" not in f]
    # crap.txt first, then a run with DB tests, then the biggest file
    craps.sort(key=lambda f: (os.path.basename(f) != "crap.txt", "db" not in os.path.basename(f), -os.path.getsize(f)))
    for f in craps:
        r = parse_crap(f)
        if r["functions"]:
            out["crap"] = r
            break
    if curated:  # a list the review made of changed functions above 10: it wins
        r = parse_crap_curated(curated[0])
        if r["over10"]:
            r["functions"] = out["crap"]["functions"] if out["crap"] else len(r["over10"])
            r["note"] = "changed functions only, from the review's list"
            out["crap"] = r
    muts = sorted((f for f in files if re.search(r"/mutate[^/]*\.txt$", f)), key=os.path.getmtime, reverse=True)
    for f in muts:
        r = parse_mutate(f)
        if r:
            if out["mutate"] is None:
                out["mutate"] = r
            else:
                out.setdefault("mutate_more", []).append(r)
    cdirs = sorted({os.path.dirname(f) for f in files if f.endswith("/missed.txt") or f.endswith("/caught.txt")})
    out["cmut"] = [parse_cmut_dir(d) for d in cdirs]
    if not out["cmut"]:
        for f in (f for f in files if re.search(r"/cmut[^/]*\.txt$", f)):
            r = parse_cmut_txt(f)
            if r:
                out["cmut"].append(r)
    for f in files:
        if os.path.basename(f) == "times":
            out["times"] = open(f).read().strip().splitlines()[:6]
            break
    return out


def round_gates(p, rnd):
    g = rnd.get("gates")
    if not g:
        return {}
    path = os.path.join(R.PR_REVIEWS, g)
    res = gates_for(path, only_top=(rnd.get("kind") == "review"))
    # The box run fetched into the repo (read-only) fills gaps, for the latest round of this PR number.
    if rnd is R.latest_round(p) or rnd.get("kind") == "review":
        box = os.path.join(R.repo_dir(p["repo"]), ".metrics", f"remote-pr-{rnd.get('pr', p['number'])}", "run")
        if os.path.isdir(box):
            extra = gates_for(box)
            for k in ("crap", "mutate"):
                if not res.get(k) and extra.get(k):
                    res[k] = extra[k]
            if not res.get("cmut") and extra.get("cmut"):
                res["cmut"] = extra["cmut"]
    for k in ("crap", "mutate"):
        if res.get(k):
            res[k]["file"] = os.path.relpath(res[k]["file"], R.WUP)
    for c in res.get("cmut") or []:
        c["file"] = os.path.relpath(c["file"], R.WUP)
    for m in res.get("mutate_more") or []:
        m["file"] = os.path.relpath(m["file"], R.WUP)
    return res


# ---------------------------------------------------------------- box

BOX_RX = re.compile(r"^(finished|running \((\w+)\)|stopped without finishing|no run)", re.I)


def box_status(p):
    """quality-remote status --pr N (read-only on the box). Only called on refresh."""
    rc, out, err = run([QUALITY_REMOTE, "status", "--repo", R.repo_dir(p["repo"]), "--pr", str(p["number"])],
                       timeout=BOX_TIMEOUT)
    line = (out.strip().splitlines() or [""])[-1][:400]
    res = {"rc": rc, "line": line, "checked": time.time()}
    if rc == 4 and "sso" in err.lower():
        res["phase"] = "sso_expired"
        res["line"] = "AWS SSO expired: run aws sso login" + (f" --profile {CFG.AWS_PROFILE}" if CFG.AWS_PROFILE else "")
    elif rc == 4:
        res["phase"] = "unreachable"
        res["line"] = "The box cannot be reached (AWS login is fine)"
    elif rc == 124:
        res["phase"] = "timeout"
    elif rc == 0:
        res["phase"] = "done"
    elif rc == 3:
        m = BOX_RX.match(line)
        res["phase"] = (m.group(2) if m and m.group(2) else "running")
    elif "no run" in line:
        res["phase"] = "no_run"
    elif "stopped" in line:
        res["phase"] = "stopped"
    else:
        res["phase"] = "error"
        res["line"] = (line or err.strip().splitlines()[-1:] or [""])[0][:300] if (line or err.strip()) else ""
    return res


def has_box_run(p):
    base = os.path.join(R.repo_dir(p["repo"]), ".metrics")
    return any(os.path.isdir(os.path.join(base, f"remote-pr-{n}")) for n in [p["number"]] + p.get("previous_numbers", []))



# ---------------------------------------------------------------- live sessions and session search

SESS_DIR = os.path.expanduser("~/.claude/sessions")
PROJECTS = os.path.expanduser("~/.claude/projects")


def live_sessions():
    """Running Claude processes from ~/.claude/sessions/<pid>.json: {session_id: {pid, tty, cwd, status}}.
    Cached for 5 s. Only pids that are alive count."""
    with _lock:
        c = _mem.get("live")
        if c and time.time() - c[0] < 5:
            return c[1]
    ttys = {p["pid"]: p["tty"] for p in processes()}
    rc, out, _ = run(["ps", "-axo", "pid=,tty="], timeout=10)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            ttys.setdefault(int(parts[0]), parts[1])
    res = {}
    for f in glob.glob(os.path.join(SESS_DIR, "*.json")):
        try:
            with open(f) as fh:
                d = json.load(fh)
            pid = int(d.get("pid") or 0)
            os.kill(pid, 0)
        except (OSError, ValueError, TypeError):
            continue
        sid = d.get("sessionId")
        if sid:
            res[sid] = {"pid": pid, "tty": ttys.get(pid, ""), "cwd": d.get("cwd", ""), "status": d.get("status", ""),
                        "name": d.get("name", "")}
    with _lock:
        _mem["live"] = (time.time(), res)
    return res


def _transcript_cwd(path):
    try:
        with open(path, errors="replace") as f:
            for _ in range(40):
                line = f.readline()
                if not line:
                    break
                if '"cwd"' in line:
                    try:
                        cwd = json.loads(line).get("cwd")
                        if cwd:
                            return cwd
                    except ValueError:
                        pass
    except OSError:
        pass
    return ""


def find_sessions(repo, number, branch, days=30, force=False):
    """Claude sessions that worked on this PR: transcripts (in the repo's project folders, changed in
    the last `days`) that name the PR link or the branch. Newest first. Cached 10 min."""
    key = f"sess-{repo}-{number}"
    with _lock:
        c = _mem.get(key)
        if c and not force and time.time() - c[0] < 600:
            return c[1]
    base = R.repo_dir(repo).replace("/", "-")
    dirs = [d for d in glob.glob(os.path.join(PROJECTS, base + "*")) if os.path.isdir(d)]
    cutoff = time.time() - days * 86400
    files = [f for d in dirs for f in glob.glob(os.path.join(d, "*.jsonl")) if os.path.getmtime(f) > cutoff]
    link = f"github.com/{R.ORG}/{repo}/pull/{int(number)}"
    pats = [link] + ([branch] if branch and "/" in branch and len(branch) >= 6 else [])
    if not files:
        res = []
    else:
        args = ["grep", "-l", "-F"]
        for p_ in pats:
            args += ["-e", p_]
        rc, out, _ = run(args + ["--"] + files, timeout=40)  # one pass over all files
        hits = out.splitlines()
        counts = {}
        for p_ in pats:  # small second pass, only on the files that matched
            if hits:
                rc, out, _ = run(["grep", "-c", "-F", "--", p_] + hits, timeout=20)
                for line in out.splitlines():
                    f, _, n = line.rpartition(":")
                    counts.setdefault(f, {})[p_] = int(n or 0)
        reg = R.load()
        review_sids = {s["id"] for p in reg["prs"] for s in p.get("sessions", [])}
        res = []
        for f in hits:
            sid = os.path.basename(f)[:-6]
            if sid in review_sids:
                continue  # a review session of mine, not work on the PR
            cn = counts.get(f, {})
            nb, nl = cn.get(branch, 0) if branch else 0, cn.get(link, 0)
            why = ", ".join(x for x in [f"branch named {nb}x" if nb else "", f"PR link {nl}x" if nl else ""] if x)
            res.append({"session": sid, "why": why, "score": nb + 3 * nl, "mtime": os.path.getmtime(f),
                        "cwd": _transcript_cwd(f), "project": os.path.basename(os.path.dirname(f))})
        res.sort(key=lambda x: (-x["score"], -x["mtime"]))
    with _lock:
        _mem[key] = (time.time(), res[:8])
    return res[:8]
