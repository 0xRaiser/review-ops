"""Actions: re-review, new review, focus tab. They open iTerm tabs; none of them touches GitHub.

Every action supports dry_run: it returns the command (and prompt) without running anything.
"""
import datetime as dt
import json
import os
import re
import shlex
import uuid

import checklist as K
import config as CFG
import drafts as D
import knowledge as KN
import collect as C
import model as M
import registry as R

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = os.path.join(HERE, "prompts")
NEWTAB = os.path.join(CFG.TOOLS, "newtab.py")
GLOBAL_DRY_RUN = os.environ.get("REVIEW_OPS_DRY_RUN") == "1"


def today():
    return dt.date.today().isoformat()


def _open_tab(command, dry_run):
    argv = ["perl", "-e", "alarm 25; exec @ARGV", C.IT2PY, NEWTAB, command]
    if dry_run or GLOBAL_DRY_RUN:
        return {"dry_run": True, "would_run": " ".join(shlex.quote(a) for a in argv), "tab_command": command}
    rc, out, err = C.run(argv, timeout=30)
    m = re.search(r"(ttys\d+)", out)
    if rc != 0 or not m:
        return {"ok": False, "error": (err or out).strip()[-300:] or "could not open the tab"}
    return {"ok": True, "tty": m.group(1), "tab_command": command}


def _write_prompt(repo, number, text, dry_run):
    name = f"{repo}-{number}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    path = os.path.join(PROMPTS, name)
    if not (dry_run or GLOBAL_DRY_RUN):
        os.makedirs(PROMPTS, exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    return path


def recheck_prompt(p, s, opts=None):
    rnd = R.latest_round(p) or {}
    n = p["number"]
    pr = s["_pr"]
    base = pr.get("baseRefName") or "dev"
    k = len(p.get("rounds", [])) + 1
    lines = [
        f"Recheck {p['repo']} PR #{n} (this is round {k}). Keep the context of your earlier review.",
        "",
        "## State now (from the Review Ops dashboard)",
        f"- Head now: `{(pr.get('headRefOid') or '?')[:8]}`. Head we reviewed: `{rnd.get('head', '?')[:8]}`"
        + ("" if s["head_found"] else " (not in the PR's commits any more: force-push or rebase?)"),
        f"- Our verdict so far: {R.VERDICTS.get(p.get('verdict'), p.get('verdict'))}.",
    ]
    if s["_new_commits"]:
        lines.append(f"- New commits since our review ({len(s['_new_commits'])}):")
        lines += [f"  - `{c['oid'][:8]}` {c.get('messageHeadline', '')}" for c in s["_new_commits"][:30]]
    else:
        lines.append("- No new commits since our review.")
    if s["_replies"]:
        lines.append(f"- New replies since our last look ({len(s['_replies'])}); read each in full on GitHub:")
        for r in s["_replies"][:40]:
            body = re.sub(r"\s+", " ", r["body"])[:140]
            lines.append(f"  - {r['by']} on {r['where']}: \"{body}\"")
    else:
        lines.append("- No new replies since our last look.")
    ci = s["ci"]
    if ci.get("failing"):
        lines.append("- CI failing: " + ", ".join(ci["failing"]))
    o = opts or K.normalize("rereview", {})
    steps = []
    if o["round1"]:
        steps.append("Read the new commits and every new reply (`gh api repos/%s/%s/pulls/%d/comments`, "
                     "`/issues/%d/comments`, `/pulls/%d/reviews`). Check each of our earlier findings and each "
                     "reply against the code at the new head (fetch refs only; never check out)." % (R.ORG, p["repo"], n, n, n))
    else:
        steps.append("Read the new commits (fetch refs only; never check out). Earlier findings and replies are not "
                     "part of this recheck.")
    effort = o["effort"]
    if o["code_review"]:
        steps.append(f"Run the `/code-review` skill at **{effort}** effort on the new commits.")
    if o["pr_reviewer"]:
        steps.append("Run the in-depth `pr-reviewer` review with the repo skills that fit, on what changed.")
    if o["self_review"]:
        steps.append(f"Run the user's `self-review` skill at **{effort}** effort (read "
                     "`~/.claude/skills/self-review/SKILL.md`; no `--comment` or `--fix`).")
    if o["security"]:
        steps.append("Run a security pass on what changed: " + _security_text())
    cmds = K.gate_commands(n, o, base)
    if cmds:
        g = "Run the box gates: " + "; then ".join(f"`{c}`" for c in cmds) + ". Never on this Mac."
        if o["limit"]:
            g += " Limit the paths to the money and logic files (pass them after `--`) and say which you left out."
        steps.append(g)
    else:
        steps.append("No box gates for this recheck (turned off in the checklist).")
    proof = []
    if o["probe_major"]:
        proof.append("each new or still-open blocker and major")
    if o["probe_minor"]:
        proof.append("each minor that claims a wrong result")
    if proof:
        steps.append("Prove " + " and ".join(proof) + " with a probe on the box, as in round 1.")
    if o["kill_tests"] and (o["mutator"] or o["cmut"]):
        steps.append("For each mutation survivor, give the test that kills it and check it fails under the mutant.")
    steps.append(f"Add a `## Recheck at <sha> ({today()})` section to the report, with the status of each finding"
                 + (", the author's replies checked against the code" if o["round1"] else "")
                 + f" and the gate results. Put gate files in `{today()}-gates/recheck{k - 1}/`.")
    if o["drafts"]:
        steps.append("Draft a reply for each thread that needs one, and write every planned comment to "
                     f"`../pr-reviews/{p['repo']}/{n}/<date>-post/drafts.json` (same format as round 1). Give every item "
                     "an `explain` block and the file a `walkthrough`, as described under \"Explain each draft\" below. "
                     "Show the drafts first.")
    if o["post"]:
        steps.append("**Post only after the user confirms in this session.** Show the draft first.")
    else:
        steps.append("**Do not post anything on GitHub.** The report is the output.")
    if o["approve"]:
        steps.append("If the PR is clean, say so and offer to approve; approve only after the user confirms.")
    else:
        steps.append("Do not approve the PR; the user decides.")
    pl = ",".join(K.passes("rereview", o))
    steps.append(f"When done, record the round: `review-ops round {p['repo']} {n} --head <sha> --verdict "
                 f"<request_changes|approve_with_nits|approved> --kind recheck --passes {pl}`.")
    steps.insert(1, "Update the PR map (Step 0 below) for the new commits: scope of each newly changed file, flow, "
                    "new components and whether an existing one could be reused, and triggers. Do this before the "
                    "logic passes.")
    lines += ["", "## What to do"] + [f"{i}. {t}" for i, t in enumerate(steps, 1)]
    known = ""
    if o.get("knowledge"):
        fl = C.gh_pr_files(p["repo"], n)
        known = KN.section(p["repo"], [f.get("filename", "") for f in (fl.get("files") or [])])
    return ("\n".join(lines) + "\n" + "\n" + D.map_text(o.get("depth") or "standard") + ("\n" + D.EXPLAIN_TEXT if o["drafts"] else "") + ("\n" + known if known else "")
            + (linked_text(p["repo"], n) if o["linked"] else ""))


def _plan(repo, number, kind, opts):
    """Remember what this run asked for; `review-ops round` uses it when --passes is not given."""
    with R.locked():
        reg = R.load()
        p = R.find(reg, repo, number)
        if p:
            p["planned"] = {"kind": kind, "passes": K.passes(kind, opts), "options": opts, "at": R.now_iso()}
            R.save(reg)
    K.save_defaults(repo, kind, opts)


def re_review(repo, number, dry_run=False, opts=None):
    reg = R.load()
    p = R.find(reg, repo, number)
    if not p:
        return {"ok": False, "error": "This PR is not in the registry. Use New review."}
    sid = R.current_session(p)
    if not sid:
        return new_review(repo, p["number"], dry_run=dry_run, opts=opts)
    o = K.normalize("rereview", opts if opts is not None else K.values(repo, p["number"], "rereview"))
    s = M.summary(p, force=True)
    sess = C.session_state(p)
    prompt = recheck_prompt(p, s, o)
    if sess.get("pid"):
        # A second `claude --resume` on a running session is not safe: hand over the prompt instead.
        return {"ok": False, "session_open": True, "tty": sess.get("tty", ""), "prompt": prompt,
                "error": f"The session is open in {sess.get('tty') or 'a tab'}."}
    ppath = _write_prompt(repo, p["number"], prompt, dry_run)
    cmd = (f"cd {shlex.quote(R.repo_dir(repo))} && claude --resume {sid} --effort {o['effort']} "
           f"\"$(cat {shlex.quote(ppath)})\"")
    res = _open_tab(cmd, dry_run)
    res["prompt"] = prompt
    res["prompt_file"] = ppath
    res["passes"] = K.passes("rereview", o)
    if res.get("ok"):
        _plan(repo, number, "rereview", o)
        with R.locked():
            reg = R.load()
            p = R.find(reg, repo, number)
            p["tab"] = res["tty"]
            p["sessions"][-1]["tab"] = res["tty"]
            p.setdefault("log", []).append({"at": R.now_iso(), "action": "re-review", "tty": res["tty"]})
            R.save(reg)
    return res


def _template_path():
    """Your own template in pr-reviews/ wins; otherwise the starter one in templates/."""
    own = os.path.join(R.PR_REVIEWS, "brief-template.md")
    return own if os.path.exists(own) else os.path.join(HERE, "templates", "brief-template.md")


def _crates(files):
    names = sorted({m.group(1) for f in files for m in [re.match(r"crates/([^/]+)/", f["path"])] if m})
    return ", ".join(names) or "—"


STEP_RX = re.compile(r"^(\d+b?)\. ", re.M)


def _security_text():
    """Step 3b of the brief template (the security pass), as one paragraph."""
    try:
        tpl = open(_template_path()).read()
        m = re.search(r"^3b\. (.*?)(?=^\d+b?\. |^## )", tpl, re.M | re.S)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    except OSError:
        pass
    return ("authn/authz, input limits, injection, secrets in logs, SSRF, replay of money actions, rate limits, "
            "PII, new dependencies and CI changes. Mark each security finding **security**.")


def _passes_section(tpl_passes, number, opts, base):
    """The brief's '## Passes' part, built from the template's own steps and the checklist."""
    steps = {}
    marks = list(STEP_RX.finditer(tpl_passes))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(tpl_passes)
        steps[m.group(1)] = tpl_passes[m.end():end].rstrip() + "\n"
    effort = opts["effort"]
    out = []
    for key, opt in (("1", "code_review"), ("2", "pr_reviewer"), ("3", "self_review"), ("3b", "security")):
        if opts[opt] and key in steps:
            out.append(steps[key].replace("**high** effort", f"**{effort}** effort"))
    cmds = K.gate_commands(number, opts, base)
    if cmds:
        tools = [n for n, k in (("crapper", "crapper"), ("mutator", "mutator"), ("cargo-mutants", "cmut")) if opts[k]]
        g = ("**Gates on the devbox:** " + "; then ".join(f"`{c}`" for c in cmds) + f". Tools: {', '.join(tools)}. "
             "Never report fake 0% scores (crapper exit 5 = fix the run).")
        if opts["limit"]:
            g += " Limit the paths to the money and logic files (pass them after `--`) and say which files you left out."
        if not opts["db"]:
            g += " No DB tests were asked for; say so if the code needs them."
        out.append(g + "\n")
    else:
        out.append("**Gates:** no box gates for this review (turned off in the checklist).\n")
    proof = []
    if opts["probe_major"]:
        proof.append("each blocker and major")
    if opts["probe_minor"]:
        proof.append("each minor that claims a wrong result")
    if proof:
        p = ("**Validate findings (confirm they occur).** For " + " and for ".join(proof) + ", write a small probe "
             "test or reproduction and run it on the box at the PR head: show it fails or misbehaves on the head, and "
             "passes with the fix (or with the change reverted). Restore the box copy after. Mark each finding "
             "**proven**, **not proven** (move it down or drop it) or **not testable** (say why). Mark each finding "
             "**new** or **confirms prior**.")
        if opts["kill_tests"] and (opts["mutator"] or opts["cmut"]):
            p += " For each mutation survivor, give the test that kills it and check it fails under the mutant."
        out.append(p + "\n")
    elif opts["kill_tests"] and (opts["mutator"] or opts["cmut"]):
        out.append("**Kill tests:** for each mutation survivor, give the test that kills it and check it fails "
                   "under the mutant.\n")
    else:
        out.append("**No probe tests** were asked for. Mark each finding **not proven** unless it is plain from "
                   "the code.\n")
    body = "".join(f"{i}. {t}" for i, t in enumerate(out, 1))
    return "## Passes (as chosen in the checklist)\n" + body + "\n"


def brief_from_template(repo, number, opts=None):
    opts = K.normalize("new", opts if opts is not None else K.values(repo, number, "new"))
    rc, out, err = C.run(["gh", "pr", "view", str(number), "-R", f"{R.ORG}/{repo}", "--json",
                          "number,title,author,headRefName,baseRefName,additions,deletions,changedFiles,files"],
                         timeout=30)
    if rc != 0:
        raise RuntimeError("gh pr view failed: " + (err.strip().splitlines() or [""])[-1][:200])
    v = json.loads(out)
    tpl = open(_template_path()).read()
    size = f"{v['changedFiles']} files, +{v['additions']}/-{v['deletions']}"
    rep = {
        "<PR>": str(number), "<title>": v["title"], "<author>": (v.get("author") or {}).get("login", ""),
        "<head>": v["headRefName"], "<base>": v["baseRefName"], "<size>": size,
        "<crates>": _crates(v.get("files") or []),
        "<PR-specific focus>": "(No PR-specific focus was given. Pick it from the diff.)",
        "<org>": R.ORG, "<repo>": repo, "<today>": today(),
    }
    for k_, val in rep.items():
        tpl = tpl.replace(k_, val)
    tpl = tpl.replace("2026-11-19-review.md", f"{today()}-review.md").replace("2026-11-19-gates", f"{today()}-gates")
    # Older templates name one repo in full; point those names at this PR's repo.
    for other in R.REPO_DIRS:
        if other != repo:
            tpl = tpl.replace(f"{R.ORG}/{other}", f"{R.ORG}/{repo}").replace(f"pr-reviews/{other}/", f"pr-reviews/{repo}/")
    if not (opts["probe_major"] or opts["probe_minor"]):
        tpl = tpl.replace(", and **every claimed problem proven**", "")
    head, _, rest = tpl.partition("## Passes")
    passes_tpl, _, output = rest.partition("## Output")
    passes_tpl = passes_tpl.split("\n", 1)[1] if "\n" in passes_tpl else passes_tpl
    # Other parts between Passes and Output (such as "Drafts for the user to approve") stay as written.
    cut = re.search(r"^## ", passes_tpl, re.M)
    between = passes_tpl[cut.start():] if cut else ""
    passes_tpl = passes_tpl[:cut.start()] if cut else passes_tpl
    if opts["post"]:
        head = head.replace("Write a report. **Do not post anything on GitHub.**",
                            "Write a report. **Post nothing on GitHub until the user confirms in this session.**")
        head = head.replace("No commits, pushes, GitHub posts or edits to tracked files.",
                            "No commits, pushes or edits to tracked files. GitHub posts only after the user confirms "
                            "in this session.")
    extra = ["- Posting: " + ("show the review draft; post it only after the user confirms in this session."
                              if opts["post"] else "do not post anything; the report is the output."),
             "- Approval: " + ("if the PR is clean, offer to approve; approve only after the user confirms."
                               if opts["approve"] else "do not approve; the user decides.")]
    pl = ",".join(K.passes("new", opts))
    extra.append(f"- When done, record the round: `review-ops round {repo} {number} --head <sha> --verdict "
                 f"<request_changes|approve_with_nits|approved> --kind review --passes {pl}`.")
    known = ""
    if opts.get("knowledge"):
        # `gh pr view --json files` stops at 100 files; the REST list is complete.
        full = C.gh_pr_files(repo, number)
        paths = [f.get("filename", "") for f in (full.get("files") or [])] or [f["path"] for f in v.get("files") or []]
        known = KN.section(repo, paths)
    if "drafts.json" in between and "## Explain each draft" not in between:
        between = between.rstrip() + "\n\n" + D.EXPLAIN_TEXT + "\n"
    depth = opts.get("depth") or "standard"
    head = re.sub(r"^## Review depth.*?(?=^## |\Z)", f"## Review depth\nSet in the checklist: **{depth}**. The map below "
                  "lists the steps for this depth. Do the passes listed under Passes.\n\n", head, flags=re.S | re.M)
    brief = (head + known + D.map_text(depth) + "\n" + _passes_section(passes_tpl, number, opts, v["baseRefName"]) + between + "## Output" + output.rstrip()
             + "\n" + "\n".join(extra) + "\n")
    if opts["linked"]:
        brief += linked_text(repo, number)
    return brief, v


def new_review(repo, number, dry_run=False, opts=None):
    number = int(number)
    if repo not in R.REPO_DIRS:
        return {"ok": False, "error": f"Unknown repo {repo}"}
    reg = R.load()
    p = R.find(reg, repo, number)
    if p and R.current_session(p):
        return {"ok": False, "error": "This PR already has a review session. Use Re-review."}
    try:
        o = K.normalize("new", opts if opts is not None else K.values(repo, number, "new"))
        brief, v = brief_from_template(repo, number, o)
    except (RuntimeError, ValueError, KeyError) as e:
        return {"ok": False, "error": str(e)}
    folder = os.path.join(R.PR_REVIEWS, repo, str(number))
    brief_path = os.path.join(folder, "brief.md")
    brief_exists = os.path.exists(brief_path)
    sid = str(uuid.uuid4())
    rel = f"../pr-reviews/{repo}/{number}/brief.md"
    cmd = (f"cd {shlex.quote(R.repo_dir(repo))} && claude --effort {o['effort']} --session-id {sid} "
           f"{shlex.quote(f'Read {rel} and do the review it describes.')}")
    res = _open_tab(cmd, dry_run) if not dry_run and not GLOBAL_DRY_RUN else _open_tab(cmd, True)
    # The checklist decides the brief, so a new run writes it again (the old one is kept as .prev).
    res.update({"brief": brief, "brief_path": brief_path, "brief_exists": brief_exists, "session": sid,
                "passes": K.passes("new", o)})
    if res.get("dry_run"):
        return res
    if not res.get("ok"):
        return res
    with R.locked():
        os.makedirs(folder, exist_ok=True)
        if brief_exists:
            os.replace(brief_path, brief_path + ".prev")
        with open(brief_path, "w") as f:
            f.write(brief)
        reg = R.load()
        p = R.find(reg, repo, number)
        if not p:
            p = R.new_pr(repo, number)
            reg["prs"].append(p)
        p.update({"title": v["title"], "author": (v.get("author") or {}).get("login", ""), "tab": res["tty"],
                  "verdict": "in_progress", "waiting_on": "Review session"})
        p["sessions"].append({"id": sid, "tab": res["tty"], "started": R.now_iso(), "kind": "review"})
        p.setdefault("log", []).append({"at": R.now_iso(), "action": "new review", "tty": res["tty"]})
        p["planned"] = {"kind": "new", "passes": K.passes("new", o), "options": o, "at": R.now_iso()}
        R.save(reg)
    K.save_defaults(repo, "new", o)
    return res


def focus(repo, number, dry_run=False):
    reg = R.load()
    p = R.find(reg, repo, number)
    if not p:
        return {"ok": False, "error": "Not in the registry"}
    sess = C.session_state(p)
    if not sess.get("tty"):
        return {"ok": False, "error": "The session is not open in any tab."}
    argv = ["perl", "-e", "alarm 15; exec @ARGV", C.IT2PY, C.FOCUS, sess["tty"]]
    if dry_run or GLOBAL_DRY_RUN:
        return {"dry_run": True, "would_run": " ".join(shlex.quote(a) for a in argv)}
    rc, out, err = C.run(argv, timeout=20)
    return {"ok": rc == 0, "tty": sess["tty"], "error": "" if rc == 0 else (err or out).strip()[-200:]}


def remove(repo, number, dry_run=False):
    """Take a merged (or closed) PR off the list. It stays in the registry with archived: true,
    so `review-ops unarchive` brings it back. Nothing changes on GitHub."""
    reg = R.load()
    p = R.find(reg, repo, number)
    if not p:
        return {"ok": False, "error": "Not in the registry"}
    pr = C.gh_pr(repo, p["number"])
    if pr.get("state") not in ("MERGED", "CLOSED"):
        return {"ok": False, "error": "Only merged or closed PRs can be removed from the list."}
    if dry_run or GLOBAL_DRY_RUN:
        return {"dry_run": True, "would_do": f"mark {repo} #{p['number']} as archived in the registry"}
    with R.locked():
        reg = R.load()
        p = R.find(reg, repo, number)
        p["archived"] = True
        p["archived_at"] = R.now_iso()
        p["archived_state"] = pr.get("state")
        R.save(reg)
    return {"ok": True}


def link(repo, number, name, with_repo, with_number):
    """Link two PRs under a group name (registry only; nothing on GitHub)."""
    if with_repo not in R.REPO_DIRS:
        return {"ok": False, "error": "Unknown repo"}
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "Give the group a name"}
    if (repo, int(number)) == (with_repo, int(with_number)):
        return {"ok": False, "error": "A PR cannot be linked to itself"}
    with R.locked():
        reg = R.load()
        g = R.link(reg, name, [(repo, int(number)), (with_repo, int(with_number))])
        R.save(reg)
    return {"ok": True, "group": g}


def unlink(repo, number):
    with R.locked():
        reg = R.load()
        g = R.unlink(reg, repo, int(number))
        R.save(reg)
    return {"ok": True, "was": g["name"] if g else None}


def linked_text(repo, number):
    """A short block about linked PRs for review prompts and briefs."""
    reg = R.load()
    g = R.link_of(reg, repo, number)
    if not g:
        return ""
    others = [x for x in g["prs"] if not (x["repo"] == repo and x["number"] == int(number))]
    if not others:
        return ""
    lines = ["", f"## Linked PRs (feature: {g['name']})",
             "This PR is one part of a change across repos. Read the linked PRs too, and check that"
             " the parts agree (APIs, field names, messages, config, rollout order):"]
    for x in others:
        pr = C.gh_pr(x["repo"], x["number"])
        lines.append(f"- {x['repo']} #{x['number']}: {pr.get('title', '')} "
                     f"(`gh pr view {x['number']} -R {R.ORG}/{x['repo']}`)")
    return "\n".join(lines) + "\n"



# ---------------------------------------------------------------- my PRs: work sessions

def _mine_key(repo, number):
    return f"{repo}#{int(number)}"


def _set_mine(repo, number, sid, cwd):
    with R.locked():
        reg = R.load()
        reg.setdefault("mine", {})[_mine_key(repo, number)] = {"session": sid, "cwd": cwd, "at": R.now_iso()}
        R.save(reg)


def my_prompt(repo, number):
    st = M.my_state(repo, number, force=True)
    pr = C.gh_pr(repo, number)
    lines = [f"Work on my PR {R.ORG}/{repo} #{number} \"{st.get('title', '')}\" "
             f"(`{pr.get('headRefName', '?')}` → `{pr.get('baseRefName', '?')}`).", "",
             "## State now (from the Review Ops dashboard)"]
    for r in st.get("reviewers", []):
        lines.append(f"- {r['by']}: {r['state'].lower().replace('_', ' ')}" + (" (asked again)" if r.get("asked_again") else ""))
    if not st.get("reviewers"):
        lines.append("- No reviewer yet.")
    lines.append(f"- Threads waiting for my answer: {st.get('to_answer', 0)} of {st.get('open_threads', 0)} open.")
    for t in st.get("to_answer_list", [])[:30]:
        body = re.sub(r"\s+", " ", t["body"])[:140]
        lines.append(f"  - {t['by']} on {t['where']}: \"{body}\"")
    ci = st.get("ci") or {}
    if ci.get("failing"):
        lines.append("- CI failing: " + ", ".join(ci["failing"]))
    lines.append(f"- Merge state: {st.get('merge_state') or '?'}.")
    lines += ["", "## What to do",
              f"1. Read every open thread and change request (`gh api repos/{R.ORG}/{repo}/pulls/{number}/comments`, "
              f"`/pulls/{number}/reviews`, `/issues/{number}/comments`) and the failing checks (`gh pr checks {number} -R {R.ORG}/{repo}`).",
              "2. Fix what is right to fix on this PR's branch; for the rest, draft a short reply with the reason.",
              "3. Run the checks this repo's rules ask for (heavy gates only on the box with `quality-remote`).",
              "4. **Do not push, post replies, resolve threads or ask for review until I confirm in this session.** "
              "Show me the diff and the draft replies first."]
    return "\n".join(lines) + "\n" + linked_text(repo, number)


def my_resume(repo, number, sid, dry_run=False):
    live = C.live_sessions().get(sid)
    prompt = my_prompt(repo, number)
    if live:
        return {"ok": False, "session_open": True, "tty": live.get("tty", ""), "prompt": prompt,
                "error": f"The session is open in {live.get('tty') or 'a tab'}."}
    found = {s["session"]: s for s in M.my_sessions(repo, number, C.gh_pr(repo, number).get("headRefName"))}
    cwd = (found.get(sid) or {}).get("cwd") or R.repo_dir(repo)
    if not os.path.isdir(cwd):
        cwd = R.repo_dir(repo)
    ppath = _write_prompt(repo, number, prompt, dry_run)
    cmd = f"cd {shlex.quote(cwd)} && claude --resume {sid} \"$(cat {shlex.quote(ppath)})\""
    res = _open_tab(cmd, dry_run)
    res.update({"prompt": prompt, "session": sid})
    if res.get("ok"):
        _set_mine(repo, number, sid, cwd)
    return res


def my_start(repo, number, dry_run=False):
    sessions = M.my_sessions(repo, number, C.gh_pr(repo, number).get("headRefName"))
    cwd = next((s["cwd"] for s in sessions if s.get("cwd") and os.path.isdir(s["cwd"])), R.repo_dir(repo))
    sid = str(uuid.uuid4())
    prompt = my_prompt(repo, number)
    ppath = _write_prompt(repo, number, prompt, dry_run)
    cmd = f"cd {shlex.quote(cwd)} && claude --session-id {sid} \"$(cat {shlex.quote(ppath)})\""
    res = _open_tab(cmd, dry_run)
    res.update({"prompt": prompt, "session": sid, "cwd": cwd})
    if res.get("ok"):
        _set_mine(repo, number, sid, cwd)
    return res


def my_focus(sid, dry_run=False):
    live = C.live_sessions().get(sid)
    if not live or not live.get("tty"):
        return {"ok": False, "error": "That session is not open in a tab."}
    argv = ["perl", "-e", "alarm 15; exec @ARGV", C.IT2PY, C.FOCUS, live["tty"]]
    if dry_run or GLOBAL_DRY_RUN:
        return {"dry_run": True, "would_run": " ".join(shlex.quote(a) for a in argv)}
    rc, out, err = C.run(argv, timeout=20)
    return {"ok": rc == 0, "tty": live["tty"], "error": "" if rc == 0 else (err or out).strip()[-200:]}


def my_pick(repo, number, sid):
    found = {s["session"]: s for s in M.my_sessions(repo, number, C.gh_pr(repo, number).get("headRefName"))}
    _set_mine(repo, number, sid, (found.get(sid) or {}).get("cwd", ""))
    return {"ok": True}



def send_drafts(repo, number, force=False, dry_run=False):
    """Hand the approved drafts to the review session: same flow as Re-review (copy into an open
    session, or resume it in a new tab). Blocks on a moved head or pending items unless force."""
    import drafts as D
    sp = D.send_prompt(repo, number)
    if not sp.get("ok"):
        return sp
    if sp["blocked"] and not force:
        return dict(sp, ok=False, needs_force=True, error="Check the warnings first.")
    reg = R.load()
    p = R.find(reg, repo, number)
    sid = R.current_session(p) if p else ""
    if p and sid:
        sess = C.session_state(p)
        if sess.get("pid"):
            return dict(sp, ok=False, session_open=True, tty=sess.get("tty", ""))
    else:  # one of my PRs: its work session
        best, _ = _work_session(repo, number)
        if not best:
            return dict(sp, ok=False, error="No session to send to. Start one from the PR page first.")
        sid = best["session"]
        live = C.live_sessions().get(sid)
        if live:
            return dict(sp, ok=False, session_open=True, tty=live.get("tty", ""))
    ppath = _write_prompt(repo, number, sp["prompt"], dry_run)
    cmd = f"cd {shlex.quote(R.repo_dir(repo))} && claude --resume {sid} \"$(cat {shlex.quote(ppath)})\""
    res = _open_tab(cmd, dry_run)
    if res.get("ok"):
        D.mark_sent(repo, number, "new_tab", res.get("tty", ""), sp["counts"])
    res.update({"prompt": sp["prompt"], "warnings": sp["warnings"], "counts": sp["counts"]})
    return res


def drafts_session(repo, number):
    """The session that posts the drafts (same choice as send_drafts): the review session of a
    tracked PR, else my work session. {kind, session, open, tty}"""
    p = R.find(R.load(), repo, number)
    sid = R.current_session(p) if p else ""
    if p and sid:
        st = C.session_state(p)
        return {"kind": "review", "session": sid, "open": bool(st.get("pid")), "tty": st.get("tty", "")}
    best, _ = _work_session(repo, number)
    if not best:
        return {"kind": "work", "session": "", "open": False, "tty": ""}
    live = C.live_sessions().get(best["session"]) or {}
    return {"kind": "work", "session": best["session"], "open": bool(live), "tty": live.get("tty", "")}


def drafts_copied(repo, number, dry_run=False):
    """The user copied the post prompt for the open session: record the send, then focus its tab."""
    import drafts as D
    sp = D.send_prompt(repo, number)
    ss = drafts_session(repo, number)
    if not ss["open"]:
        return {"ok": False, "error": "The session is not open in a tab."}
    mk = D.mark_sent(repo, number, "copied", ss["tty"], sp.get("counts"))
    fr = focus(repo, number, dry_run) if ss["kind"] == "review" else my_focus(ss["session"], dry_run)
    return {"ok": mk.get("ok", False), "sent": mk.get("sent"), "tty": ss["tty"], "focus": fr}



def _work_session(repo, number, sid=""):
    """The session to use for one of my PRs: the given one, else the chosen or best found one."""
    ss = M.my_sessions(repo, number, C.gh_pr(repo, number).get("headRefName"))
    if sid:
        return next((s for s in ss if s["session"] == sid), {"session": sid, "cwd": "", "live": False}), ss
    return (ss[0] if ss else None), ss


def send_triage(repo, number, dry_run=False, copied=False):
    """Phase 2 of triage: the fix prompt from the user's decisions, for my PR's work session."""
    import address as AD
    import triage as TR
    best, ss = _work_session(repo, number)
    cwd = next((s["cwd"] for s in ([best] if best else []) + ss if s.get("cwd") and os.path.isdir(s["cwd"])), R.repo_dir(repo))
    pr = C.gh_pr(repo, number)
    text, err = TR.phase2_prompt(repo, number, cwd, pr.get("baseRefName") or "dev", pr.get("headRefName") or "?", today())
    if err:
        return {"ok": False, "error": err}
    if copied:
        TR.mark_sent(repo, number, "copied")
        return {"ok": True, "recorded": True}
    live = C.live_sessions().get(best["session"]) if best else None
    base = {"prompt": text, "session": (best or {}).get("session", ""), "folder": cwd}
    if live:
        return dict(base, ok=False, session_open=True, tty=live.get("tty", ""))
    if not best:
        return dict(base, ok=False, error="No work session found for this PR. Copy the prompt into one.")
    ppath = _write_prompt(repo, number, text, dry_run)
    cmd = f"cd {shlex.quote(cwd)} && claude --resume {best['session']} \"$(cat {shlex.quote(ppath)})\""
    res = _open_tab(cmd, dry_run)
    res.update(base, how="resumed")
    if res.get("ok"):
        TR.mark_sent(repo, number, "resumed", res.get("tty", ""))
    return res


def address(repo, number, thread_ids, check_ids, opts, sid="", dry_run=False, copied=False):
    """Send my PR's work session the chosen comments (resume it, start one, or hand over the prompt)."""
    import address as AD
    best, ss = _work_session(repo, number, sid)
    cwd = next((s["cwd"] for s in ([best] if best else []) + ss if s.get("cwd") and os.path.isdir(s["cwd"])), R.repo_dir(repo))
    text, picked, checks = AD.prompt(repo, number, thread_ids, check_ids, opts, folder=cwd)
    if not picked and not checks:
        return {"ok": False, "error": "Tick at least one comment or check."}
    ids = [i["id"] for i in picked]
    cids = [c["id"] for c in checks]
    if copied:  # the user copied the prompt into an open session
        AD.record(repo, number, ids, cids, opts, "copied")
        AD.save_defaults(repo, AD.normalize(opts))
        return {"ok": True, "recorded": True}
    live = C.live_sessions().get(best["session"]) if best else None
    base = {"prompt": text, "threads": len(ids), "checks": len(cids), "session": (best or {}).get("session", ""),
            "folder": cwd}
    if live:
        return dict(base, ok=False, session_open=True, tty=live.get("tty", ""))
    ppath = _write_prompt(repo, number, text, dry_run)
    if best:
        sid, how = best["session"], "resumed"
        cmd = f"cd {shlex.quote(cwd)} && claude --resume {sid} \"$(cat {shlex.quote(ppath)})\""
    else:
        sid, how = str(uuid.uuid4()), "started"
        cmd = f"cd {shlex.quote(cwd)} && claude --session-id {sid} \"$(cat {shlex.quote(ppath)})\""
    res = _open_tab(cmd, dry_run)
    res.update(base, session=sid, how=how)
    if res.get("ok"):
        _set_mine(repo, number, sid, cwd)
        AD.record(repo, number, ids, cids, opts, how, res.get("tty", ""))
        AD.save_defaults(repo, AD.normalize(opts))
    return res
