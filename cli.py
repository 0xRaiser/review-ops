#!/usr/bin/env python3
"""review-ops registry CLI, for the master Claude session (and review sessions).

  review-ops list
  review-ops show   <repo> <N>
  review-ops add    <repo> <N> [--title T] [--author A] [--session ID] [--tab ttysNNN]
                    [--verdict V] [--waiting W] [--notes N] [--previous M]
  review-ops update <repo> <N> [--verdict V] [--verdict-note X] [--waiting W] [--tab ttysNNN]
                    [--session ID] [--notes N] [--posted-note X] [--title T] [--author A] [--previous M]
  review-ops round  <repo> <N> --head SHA [--verdict V] [--kind review|recheck] [--passes a,b,c]
                    [--report REL] [--gates REL] [--pr M] [--date YYYY-MM-DD]
  review-ops posted <repo> <N> [--review-id ID --comments K --state S]   (no ids: read ours from gh)
  review-ops link "<feature name>" <repo> <N> <repo> <N> [...]   link PRs across repos
  review-ops unlink <repo> <N>
  review-ops links
  review-ops archive <repo> <N>     take it off the list (kept in the registry)
  review-ops unarchive <repo> <N>   put it back on the list
  review-ops remove <repo> <N>      delete it from the registry
  review-ops status          write STATUS.md from the registry again
  review-ops import          import STATUS.md and the folders (only PRs not in the registry yet)

V is one of: request_changes, approve_with_nits, approved, not_reviewed, on_hold, in_progress
(free text such as "approve with nits" also works). Paths are relative to pr-reviews/.
--session adds a new session id (the latest one is the one Re-review resumes).
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C  # noqa: E402
import registry as R  # noqa: E402


def die(msg):
    print("review-ops: " + msg, file=sys.stderr)
    sys.exit(1)


def vcode(v):
    if v is None:
        return None
    return v if v in R.VERDICTS else R.verdict_code(v)


def get(reg, a, must=True):
    p = R.find(reg, a.repo, a.number)
    if must and not p:
        die(f"{a.repo} #{a.number} is not in the registry (use add)")
    return p


def apply_common(p, a):
    for field, attr in [("title", "title"), ("author", "author"), ("waiting_on", "waiting"), ("notes", "notes"),
                        ("verdict_note", "verdict_note"), ("posted_note", "posted_note")]:
        v = getattr(a, attr, None)
        if v is not None:
            p[field] = v
    if getattr(a, "verdict", None):
        p["verdict"] = vcode(a.verdict)
    if getattr(a, "tab", None) is not None:
        p["tab"] = a.tab
        if p["sessions"]:
            p["sessions"][-1]["tab"] = a.tab
    if getattr(a, "session", None):
        if not p["sessions"] or p["sessions"][-1]["id"] != a.session:
            p["sessions"].append({"id": a.session, "tab": p.get("tab", ""), "started": R.now_iso(), "kind": "review"})
    if getattr(a, "previous", None):
        if a.previous not in p["previous_numbers"]:
            p["previous_numbers"].append(a.previous)


def cmd_list(reg, a):
    for p in reg["prs"]:
        r = R.latest_round(p) or {}
        if p.get("archived"):
            print(f"{p['repo']:<12} #{p['number']:<5} (archived)")
            continue
        print(f"{p['repo']:<12} #{p['number']:<5} r{len(p['rounds'])} {R.VERDICTS.get(p['verdict'], p['verdict']):<18}"
              f" head {r.get('head', '')[:8]:<8} tab {p.get('tab') or '-':<8} wait: {p.get('waiting_on') or '-'}")


def cmd_show(reg, a):
    print(json.dumps(get(reg, a), indent=2))


def cmd_add(reg, a):
    if get(reg, a, must=False):
        die("already in the registry (use update)")
    p = R.new_pr(a.repo, a.number)
    apply_common(p, a)
    reg["prs"].append(p)
    return f"added {a.repo} #{a.number}"


def cmd_update(reg, a):
    p = get(reg, a)
    apply_common(p, a)
    return f"updated {a.repo} #{p['number']}"


def cmd_round(reg, a):
    p = get(reg, a)
    prn = a.pr or p["number"]
    date = a.date or dt.date.today().isoformat()
    report = a.report
    if not report:
        folder = os.path.join(R.PR_REVIEWS, a.repo, str(prn))
        reps = sorted(f for f in os.listdir(folder) if f.endswith("-review.md")) if os.path.isdir(folder) else []
        report = f"{a.repo}/{prn}/{reps[-1]}" if reps else ""
    gates = a.gates
    if gates is None and report:
        g = report.replace("-review.md", "-gates")
        gates = g if os.path.isdir(os.path.join(R.PR_REVIEWS, g)) else ""
    r = {"n": len(p["rounds"]) + 1, "kind": a.kind, "pr": prn, "head": a.head, "date": date,
         "passes": ([x.strip() for x in a.passes.split(",") if x.strip()] if a.passes
                    else list((p.get("planned") or {}).get("passes") or [])),
         "verdict": vcode(a.verdict) or p.get("verdict"), "report": report, "gates": gates or "",
         "section": "", "checked_at": R.now_iso()}
    if p.get("planned"):
        r["options"] = p["planned"].get("options")
        p.pop("planned")
    p["rounds"].append(r)
    if a.verdict:
        p["verdict"] = vcode(a.verdict)
    msg = f"round {r['n']} recorded for {a.repo} #{p['number']} at {a.head[:8]}"
    try:  # feed the report's lessons to the learner (never blocks the round)
        import learner as L
        msg += "; " + L.harvest(a.repo, prn, report, a.head)
    except Exception as e:  # noqa: BLE001
        msg += f"; lessons not harvested ({e})"
    return msg


def cmd_posted(reg, a):
    p = get(reg, a)
    if a.review_id:
        p["posted"] = [x for x in p["posted"] if x["review_id"] != a.review_id]
        p["posted"].append({"review_id": a.review_id, "comments": a.comments or 0, "state": a.state or "COMMENTED",
                            "pr": p["number"], "at": R.now_iso()})
        return f"posted review {a.review_id} recorded"
    n = 0
    for num in [p["number"]] + p.get("previous_numbers", []):
        pr = C.gh_pr(a.repo, num, force=True)
        if pr.get("_error"):
            die(pr["_error"])
        for r in (pr.get("reviews") or {}).get("nodes") or []:
            if (r.get("author") or {}).get("login") == C.ME:
                p["posted"] = [x for x in p["posted"] if x["review_id"] != r["databaseId"]]
                p["posted"].append({"review_id": r["databaseId"], "comments": r["comments"]["totalCount"],
                                    "state": r["state"], "pr": num, "at": r["submittedAt"], "url": r["url"]})
                n += 1
    p["posted"].sort(key=lambda x: x.get("at", ""))
    return f"{n} posted review(s) of ours found on GitHub"


def cmd_archive(reg, a):
    p = get(reg, a)
    p["archived"], p["archived_at"] = True, R.now_iso()
    return f"archived {a.repo} #{p['number']}"


def cmd_unarchive(reg, a):
    p = get(reg, a)
    p.pop("archived", None)
    p.pop("archived_at", None)
    return f"{a.repo} #{p['number']} is back on the list"


def cmd_remove(reg, a):
    p = get(reg, a)
    reg["prs"].remove(p)
    return f"removed {a.repo} #{a.number}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="review-ops", description="Review Ops registry", add_help=True)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("import")
    sub.add_parser("help")
    for name in ("show", "add", "update", "round", "posted", "remove", "archive", "unarchive"):
        sp = sub.add_parser(name)
        sp.add_argument("repo", choices=sorted(R.REPO_DIRS))
        sp.add_argument("number", type=int)
        if name in ("add", "update"):
            for opt in ("title", "author", "session", "tab", "verdict", "waiting", "notes"):
                sp.add_argument("--" + opt)
            sp.add_argument("--previous", type=int)
            if name == "update":
                sp.add_argument("--verdict-note", dest="verdict_note")
                sp.add_argument("--posted-note", dest="posted_note")
        if name == "round":
            sp.add_argument("--head", required=True)
            sp.add_argument("--verdict")
            sp.add_argument("--kind", choices=["review", "recheck"], default="recheck")
            sp.add_argument("--passes")
            sp.add_argument("--report")
            sp.add_argument("--gates")
            sp.add_argument("--pr", type=int)
            sp.add_argument("--date")
        if name == "posted":
            sp.add_argument("--review-id", dest="review_id", type=int)
            sp.add_argument("--comments", type=int)
            sp.add_argument("--state")
    lp = sub.add_parser("link")
    lp.add_argument("name")
    lp.add_argument("members", nargs="+", help="repo N repo N ...")
    up = sub.add_parser("unlink")
    up.add_argument("repo", choices=sorted(R.REPO_DIRS))
    up.add_argument("number", type=int)
    sub.add_parser("links")
    kp = sub.add_parser("learn", help="curate the reviewer knowledge (see learner.py)")
    kp.add_argument("--status", action="store_true", help="lesson counts, budgets and open inbox items")
    kp.add_argument("--harvest", action="store_true", help="read lessons from every recorded report")
    kp.add_argument("--all", action="store_true", help="curate every file, not only those with new input")
    kp.add_argument("--file", help="curate one file (area name such as oms, or general)")
    kp.add_argument("--diff", action="store_true", help="show the waiting proposal")
    kp.add_argument("--apply", action="store_true", help="make the waiting proposal live")
    kp.add_argument("--model", help="model for the curator (default: claude's default)")
    kp.add_argument("--reviewers", action="store_true", help="hit rate of each reviewer on my PRs (from triage)")
    kp.add_argument("--steps", action="store_true", help="which review step found each finding, and what you did with it")
    a = ap.parse_args(argv)
    if a.cmd == "learn":
        import learner as L
        if a.status:
            lines = L.status()
        elif a.steps:
            lines = L.step_report()
        elif a.reviewers:
            L.harvest_triage()
            lines = L.reviewer_report()
        elif a.harvest:
            lines = L.harvest_all() or ["nothing new to harvest"]
        elif a.diff:
            lines = L.diff(a.file) or ["no proposal"]
        elif a.apply:
            lines = L.apply()
        else:
            lines = L.learn(only=a.file, all_files=a.all, model=a.model) or \
                ["nothing to curate (no new input, every file within budget); use --all to force"]
            lines.append("review it: `review-ops learn --diff`; make it live: `review-ops learn --apply`")
        print("\n".join(lines))
        return
    if a.cmd in (None, "help"):
        print(__doc__.strip())
        return
    if a.cmd == "import":
        reg = R.import_existing()
        print(f"registry has {len(reg['prs'])} PRs")
        return
    with R.locked():
        reg = R.load()
        if a.cmd == "list":
            return cmd_list(reg, a)
        if a.cmd == "show":
            return cmd_show(reg, a)
        if a.cmd == "links":
            for g in reg.get("links", []):
                print(f"{g['name']}: " + ", ".join(f"{x['repo']} #{x['number']}" for x in g["prs"]))
            return
        if a.cmd == "link":
            if len(a.members) % 2 or len(a.members) < 4:
                die("give pairs: repo N repo N (at least two PRs)")
            pairs = []
            for i in range(0, len(a.members), 2):
                if a.members[i] not in R.REPO_DIRS or not a.members[i + 1].isdigit():
                    die(f"bad PR: {a.members[i]} {a.members[i + 1]}")
                pairs.append((a.members[i], int(a.members[i + 1])))
            g = R.link(reg, a.name, pairs)
            R.save(reg)
            print(f"linked {len(g['prs'])} PRs as '{g['name']}'; STATUS.md written")
            return
        if a.cmd == "unlink":
            g = R.unlink(reg, a.repo, a.number)
            R.save(reg)
            print(f"unlinked {a.repo} #{a.number}" + (f" from '{g['name']}'" if g else " (it was not linked)"))
            return
        if a.cmd == "status":
            R.write_status(reg)
            print("STATUS.md written")
            return
        msg = globals()["cmd_" + a.cmd](reg, a)
        R.save(reg)
        print(msg + "; STATUS.md written")


if __name__ == "__main__":
    main()
