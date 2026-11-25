"""My PRs: "Address comments" — send the PR's work session the reviewers' comments to validate and fix.

Read-only on GitHub. Writes only the registry (defaults per repo and a record of each send).
"""
import datetime as dt
import re

import collect as C
import model as M
import registry as R

OPTIONS = [
    {"key": "triage", "label": "Triage first: prove each comment and let me decide in Review Ops before any fix (two phases)", "default": True},
    {"key": "validate", "label": "Validate each comment first (agree / partly / disagree, with evidence from the code)", "default": True},
    {"key": "fix_tdd", "label": "Fix the agreed ones, test first", "default": True},
    {"key": "gates", "label": "Run the code-discipline gates on the box after the fixes (quality-remote --tool all, both mutation engines; --db when DB code changed)", "default": True},
    {"key": "ci", "label": "Fix the failing CI checks", "default": False},
    {"key": "replies", "label": "Draft a reply per thread (fixed in <commit> / why not)", "default": True},
    {"key": "drafts", "label": "Write the replies to a drafts file for approval in Review Ops", "default": True},
    {"key": "confirm", "label": "Push and post only after I confirm in the session", "default": True, "locked": True},
]


def _key(repo, number):
    return f"{repo}#{int(number)}"


def history(repo, number):
    reg = R.load()
    return ((reg.get("mine") or {}).get(_key(repo, number)) or {}).get("address", [])


def items(repo, number):
    """Open threads and review comments from others (no bots), and failing CI checks."""
    pr = C.gh_pr(repo, number)
    sent = {}
    for h in history(repo, number):
        for tid in h.get("threads", []):
            sent[tid] = h["at"]
    last_sent = max([h["at"] for h in history(repo, number)] or [""])
    out = []
    for t in M.threads_of(pr):
        others = [c for c in t["comments"] if c["by"] != C.ME and not M.BOT.search(c["by"])]
        if not others or t["resolved"]:
            continue
        first, last = t["comments"][0], t["comments"][-1]
        newest_other = max(c["at"] or "" for c in others)
        out.append({
            "id": t["id"] or t["url"], "kind": "thread", "reviewer": others[0]["by"], "path": t["path"],
            "line": t["line"], "outdated": t["outdated"], "resolved": t["resolved"], "url": t["url"],
            "text": first["body"][:2000], "first_by": first["by"],
            "last": {"by": last["by"], "at": last["at"], "body": last["body"][:1200]} if len(t["comments"]) > 1 else None,
            "waiting": last["by"] != C.ME, "comments": len(t["comments"]),
            "sent_at": sent.get(t["id"] or t["url"], ""),
            "new_since_send": bool(last_sent) and newest_other > last_sent,
        })
    for r in (pr.get("reviews") or {}).get("nodes") or []:
        who = M.login(r)
        body = (r.get("body") or "").strip()
        if who == C.ME or M.BOT.search(who) or not body:
            continue
        rid = f"review-{r.get('databaseId')}"
        out.append({"id": rid, "kind": "review", "reviewer": who, "path": "", "line": None, "outdated": False,
                    "resolved": False, "url": r.get("url"), "text": body[:3000], "first_by": who,
                    "last": None, "waiting": True, "state": r.get("state"), "at": r.get("submittedAt"),
                    "sent_at": sent.get(rid, ""),
                    "new_since_send": bool(last_sent) and (r.get("submittedAt") or "") > last_sent})
    ci = C.ci_summary(pr)
    checks = [{"id": f"ci-{n}", "kind": "ci", "name": n} for n in ci.get("failing") or []]
    return {"items": out, "checks": checks, "head": (pr.get("headRefOid") or "")[:8],
            "branch": pr.get("headRefName"), "base": pr.get("baseRefName"), "title": pr.get("title"),
            "last_sent": last_sent, "history": history(repo, number)[-5:]}


def spec(repo, number):
    data = items(repo, number)
    reg = R.load()
    saved = ((reg.get("address_defaults") or {}).get(repo)) or {}
    opts = []
    for o in OPTIONS:
        v = saved.get(o["key"], o["default"])
        if o["key"] == "ci":
            v = bool(data["checks"])  # on when CI fails
        if o.get("locked"):
            v = True
        opts.append(dict(o, value=v))
    for it in data["items"]:
        it["ticked"] = bool(it["waiting"])  # open threads where a reviewer spoke last; review bodies
    for c in data["checks"]:
        c["ticked"] = True
    return dict(data, options=opts, saved=bool(saved))


def normalize(opts):
    o = {x["key"]: bool((opts or {}).get(x["key"], x["default"])) for x in OPTIONS}
    o["confirm"] = True
    return o


def save_defaults(repo, opts):
    with R.locked():
        reg = R.load()
        d = dict(opts)
        d.pop("ci", None)  # follows the PR's CI, not habit
        reg.setdefault("address_defaults", {})[repo] = d
        R.save(reg)


def prompt(repo, number, thread_ids, check_ids, opts, folder=""):
    data = items(repo, number)
    by = {i["id"]: i for i in data["items"]}
    picked = [by[t] for t in thread_ids if t in by]
    checks = [c for c in data["checks"] if c["id"] in set(check_ids)]
    o = normalize(opts)
    today = dt.date.today().isoformat()
    L = [f"Address the review comments on my PR {R.ORG}/{repo} #{number} \"{data['title']}\" "
         f"(`{data['branch']}` → `{data['base']}`, head `{data['head']}`).", "",
         f"## Comments to address ({len(picked)})"]
    for i in picked:
        where = f"{i['path']}:{i['line']}" if i["path"] else "review summary"
        text = re.sub(r"\s+", " ", i["text"])[:400]
        L.append(f"- [{i['id']}] {i['reviewer']} on {where}" + (" (outdated)" if i.get("outdated") else "") + f": \"{text}\"")
        if i.get("last") and i["last"]["by"] != i["first_by"]:
            reply = re.sub(r"\s+", " ", i["last"]["body"])[:200]
            L.append(f"  - last reply by {i['last']['by']}: \"{reply}\"")
        if i.get("url"):
            L.append(f"  - {i['url']}")
    if checks:
        L += ["", "## Failing CI checks", *[f"- {c['name']}" for c in checks],
              f"  (`gh pr checks {number} -R {R.ORG}/{repo}` and the job logs)"]
    if o.get("triage"):
        import triage as TR
        tpath = f"../pr-reviews/{repo}/{number}/{today}-triage/triage.json"
        if checks and o["ci"]:
            L += ["", "Also note the failing CI checks above in the triage (as items with reviewer \"ci\"); do not fix them yet."]
        L += ["", TR.PHASE1_TEXT.format(triage_path=tpath, number=number),
              "Add no AI attribution anywhere. Do not push, post, resolve threads or re-request review."]
        return "\n".join(L) + "\n", picked, checks
    L += ["", "## How"]
    steps = ["Read each comment in full on GitHub (and the code it points at) before you change anything."]
    if o["validate"]:
        steps.append("Validate each comment first: **agree**, **partly** or **disagree**, with evidence from the code "
                     "(file:line, a test, or a run). Do not fix what you disagree with; explain why.")
    steps.append("Give me a short plan first (one line per comment: verdict and what you will do), then start.")
    if o["fix_tdd"]:
        steps.append("Fix the agreed ones test first: a test that fails on the current head, then the fix, then the test passes.")
    if o["ci"] and checks:
        steps.append("Fix the failing CI checks listed above. Find the cause from the job logs.")
    if o["gates"]:
        # Working-tree mode: the fixes are local and not pushed yet, so `pr N` mode (the PR head on GitHub)
        # would test the old code.
        folder = folder or R.repo_dir(repo)
        steps.append("After the fixes, run the code-discipline gates on the box **on your working tree** (it includes "
                     "the uncommitted and unpushed fixes): "
                     f"`quality-remote start --repo {folder} --base origin/{data['base']} --tool all <changed files>` "
                     "(add `--db` when DB code changed and `--features` where needed; the changed files are the PR's "
                     f"files plus your fixes, from `git diff --name-only origin/{data['base']}`), then "
                     f"`quality-remote wait --repo {folder}` and `quality-remote fetch --repo {folder}`. "
                     f"Do not use `quality-remote pr {number}`: it tests the PR head on GitHub, without your fixes. "
                     f"First check that `git -C {folder} branch --show-current` is `{data['branch']}`; if it is not, "
                     "stop and tell me (the gates would test the wrong branch). "
                     "Never run crapper, mutator, cargo-mutants or llvm-cov on this Mac.")
    else:
        steps.append("Never run crapper, mutator, cargo-mutants or llvm-cov on this Mac (only through `quality-remote`).")
    if o["replies"]:
        steps.append("Draft a reply per thread: \"Fixed in <commit>: …\" or why not, in short plain sentences.")
    if o["drafts"]:
        steps.append(f"Write the replies to `../pr-reviews/{repo}/{number}/{today}-post/drafts.json` in the Review Ops "
                     "drafts format: {\"pr\", \"head\", \"verdict\": \"comment\", \"summary\", \"items\": [{\"id\", "
                     "\"kind\": \"reply\", \"finding\": <thread id>, \"severity\", \"proof\", \"path\", \"line\", "
                     "\"in_reply_to\": <comment id>, \"body\"}]}. I approve or edit each one in Review Ops; post only "
                     "the approved ones, with my edits.")
    steps.append("**Do not push, post replies, resolve threads or re-request review until I confirm in this session.**")
    steps.append("Add no AI attribution anywhere (commits, replies, PR text).")
    L += [f"{n}. {s}" for n, s in enumerate(steps, 1)]
    return "\n".join(L) + "\n", picked, checks


def record(repo, number, thread_ids, check_ids, opts, how, tty=""):
    with R.locked():
        reg = R.load()
        e = reg.setdefault("mine", {}).setdefault(_key(repo, number), {})
        e.setdefault("address", []).append({"at": R.now_iso(), "threads": list(thread_ids), "checks": list(check_ids),
                                            "options": normalize(opts), "how": how, "tty": tty})
        e["address"] = e["address"][-20:]
        R.save(reg)
