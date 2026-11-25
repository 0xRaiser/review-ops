"""Learner: keeps the reviewer knowledge short and useful.

1. Harvest: after a round, read the report's lessons and add them to knowledge/inbox.jsonl.
   Reports end with a fenced block (asked for by knowledge.section):
       ```lessons
       hit oms-01 M1 proven         a lesson pointed to a real problem
       miss oms-04                  checked, nothing found
       wrong oms-07 reason          the lesson was wrong for this PR
       new oms | text | evidence    a new lesson for an area (file stem; "general" for general.md)
       ```
   Older reports have only a free "## Lessons" section: it is kept as raw text.
   hit/miss/wrong update knowledge/stats.json.
2. Learn: one `claude -p` pass per knowledge file that has new inbox items (or --all). It merges
   duplicates into patterns, keeps the budget, archives the rest and gives each lesson an id.
   The result goes to knowledge/.proposed/ with a diff. Nothing live changes.
3. Apply: move the proposed files in place, append archived lessons to <file>.archive.md,
   fold stats of merged ids, and mark the inbox items as done.
"""
import datetime as dt
import difflib
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess

import collect as C
import knowledge as KN
import registry as R

BUDGET = {"general.md": 15}
AREA_BUDGET = 20
LESSON_RX = re.compile(r"^\s*-\s+(?:\(([a-z0-9-]+)\)\s+)?(.+?)\s*$")
BLOCK_RX = re.compile(r"```lessons\s*\n(.*?)```", re.S)
LESSONS_SECTION_RX = re.compile(r"^#{2,4}\s+Lessons\b.*?$(.*?)(?=^#{1,4}\s|\Z)", re.S | re.M | re.I)


def kdir(*p):
    return os.path.join(KN.folder(), *p)


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _inbox():
    items = []
    try:
        with open(kdir("inbox.jsonl")) as f:
            items = [json.loads(line) for line in f if line.strip()]
    except OSError:
        pass
    return items


def _write_inbox(items):
    os.makedirs(kdir(), exist_ok=True)
    with open(kdir("inbox.jsonl.tmp"), "w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    os.replace(kdir("inbox.jsonl.tmp"), kdir("inbox.jsonl"))


def files():
    """Knowledge files (relative paths), general.md first."""
    out = ["general.md"] if os.path.exists(kdir("general.md")) else []
    for p in sorted(glob.glob(kdir("*", "*.md"))):
        rel = os.path.relpath(p, kdir())
        if not rel.endswith(".archive.md") and not rel.startswith("."):
            out.append(rel)
    return out


def area_of(rel):
    return "general" if rel == "general.md" else os.path.splitext(os.path.basename(rel))[0]


# ---------- harvest ----------

def parse_report(text):
    """(structured items, raw lessons text or '')."""
    items = []
    for block in BLOCK_RX.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kind, _, rest = line.partition(" ")
            kind = kind.lower()
            if kind in ("hit", "miss", "wrong"):
                lid, _, note = rest.strip().partition(" ")
                items.append({"kind": kind, "id": lid.strip(), "note": note.strip()})
            elif kind == "new":
                parts = [x.strip() for x in rest.split("|")]
                if len(parts) >= 2 and parts[1]:
                    items.append({"kind": "new", "area": parts[0], "text": parts[1],
                                  "evidence": parts[2] if len(parts) > 2 else ""})
    raw = ""
    if not items:
        m = LESSONS_SECTION_RX.search(text)
        if m and m.group(1).strip():
            raw = m.group(1).strip()[:4000]
    return items, raw


def harvest(repo, number, report_rel, head=""):
    """Add one report's lessons to the inbox (once per report and head). Returns a short message."""
    path = os.path.join(R.PR_REVIEWS, report_rel or "")
    if not report_rel or not os.path.exists(path):
        return "no report to harvest"
    text = open(path).read()
    items, raw = parse_report(text)
    key = hashlib.sha1(f"{repo}#{number}#{report_rel}#{head}#{len(text)}".encode()).hexdigest()[:12]
    inbox = _inbox()
    if any(it.get("key") == key for it in inbox) or key in _load_json(kdir("harvested.json"), []):
        return "already harvested"
    base = {"key": key, "repo": repo, "pr": number, "report": report_rel, "head": head[:12],
            "at": R.now_iso(), "done": False}
    new = []
    stats = _load_json(kdir("stats.json"), {})
    today = dt.date.today().isoformat()
    for it in items:
        if it["kind"] in ("hit", "miss", "wrong"):
            s = stats.setdefault(it["id"], {"hits": 0, "misses": 0, "wrong": 0, "last": "", "prs": []})
            s[{"hit": "hits", "miss": "misses", "wrong": "wrong"}[it["kind"]]] += 1
            if it["kind"] == "hit":
                s["last"] = today
            ref = f"{repo} #{number}"
            if ref not in s["prs"]:
                s["prs"] = (s["prs"] + [ref])[-8:]
        if it["kind"] in ("new", "wrong"):
            new.append(dict(base, **it))
    if raw:
        fl = C.gh_pr_files(repo, number)
        areas = [area_of(rel) for rel, _ in KN.select(repo, [f.get("filename", "") for f in fl.get("files") or []])]
        new.append(dict(base, kind="raw", text=raw, areas=areas))
    os.makedirs(kdir(), exist_ok=True)
    _save_json(kdir("stats.json"), stats)
    _write_inbox(inbox + new)
    done = _load_json(kdir("harvested.json"), [])
    _save_json(kdir("harvested.json"), (done + [key])[-500:])
    n_score = sum(1 for it in items if it["kind"] in ("hit", "miss", "wrong"))
    return f"lessons harvested: {len([x for x in new if x['kind'] == 'new'])} new, {n_score} scored" + \
        (", 1 raw section" if raw else "")


def harvest_decisions():
    """Read the user's draft decisions (all decisions.json files) into the inbox.
    Kept: rejected items, edited items, and items with a reason or a note. A changed decision replaces
    the open inbox entry for the same draft. Returns the number of new or changed entries."""
    import drafts as D
    inbox = _inbox()
    done_keys = {it["key"] for it in inbox if it.get("done")}
    open_by_ref = {it.get("ref"): i for i, it in enumerate(inbox) if it.get("kind") == "decision" and not it.get("done")}
    changed = 0
    for dpath in sorted(glob.glob(os.path.join(R.PR_REVIEWS, "*", "*", "*-post", "decisions.json"))):
        rel = os.path.relpath(dpath, R.PR_REVIEWS)
        repo, num = rel.split(os.sep)[:2]
        if not num.isdigit():
            continue
        dec = _load_json(dpath, {})
        drafts = {it.get("id"): it for it in _load_json(os.path.join(os.path.dirname(dpath), "drafts.json"), {}).get("items") or []
                  if isinstance(it, dict)}
        areas = None
        for x in dec.get("items") or []:
            d = drafts.get(x.get("id")) or {}
            edited = "body" in x and x["body"] != d.get("body")
            if not (x.get("decision") == "rejected" or edited or x.get("reasons") or x.get("note")):
                continue
            item = {"kind": "decision", "repo": repo, "pr": int(num), "ref": f"{rel}#{x['id']}",
                    "decision": x.get("decision"),
                    "reasons": [D.REASONS.get(x.get("decision"), {}).get(r, r) for r in x.get("reasons") or []],
                    "note": x.get("note", ""), "severity": d.get("severity"), "proof": d.get("proof"),
                    "finding": d.get("finding"), "path": d.get("path"), "draft": (d.get("body") or "")[:700]}
            if edited:
                item["edited_to"] = (x.get("body") or "")[:700]
            if areas is None:
                fl = C.gh_pr_files(repo, int(num))
                areas = {}
                for f in fl.get("files") or []:
                    areas[f.get("filename", "")] = [area_of(r) for r, _ in KN.select(repo, [f.get("filename", "")])]
            item["areas"] = sorted(set(areas.get(d.get("path") or "", ["general"])) | {"general"})
            item["key"] = "dec-" + hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest()[:12]
            if item["key"] in done_keys or any(it.get("key") == item["key"] for it in inbox):
                continue
            item.update(at=R.now_iso(), done=False)
            if item["ref"] in open_by_ref:
                inbox[open_by_ref[item["ref"]]] = item
            else:
                inbox.append(item)
            changed += 1
    if changed:
        _write_inbox(inbox)
    return changed


def harvest_all():
    """Harvest every round's report in the registry and every decisions file (safe to run again)."""
    out = []
    n = harvest_decisions()
    if n:
        out.append(f"draft decisions: {n} new or changed")
    for p in R.load()["prs"]:
        for r in p.get("rounds") or []:
            if r.get("report"):
                msg = harvest(p["repo"], r.get("pr", p["number"]), r["report"], r.get("head", ""))
                if not msg.startswith("already") and not msg.startswith("no report"):
                    out.append(f"{p['repo']} #{p['number']} r{r.get('n')}: {msg}")
    return out


# ---------- learn ----------

def _items_for(rel, inbox):
    area = area_of(rel)
    out = []
    for it in inbox:
        if it.get("done"):
            continue
        if it["kind"] == "new" and (it.get("area") == area or (area == "general" and
                                                                 not os.path.exists(kdir(it.get("repo", ""), it.get("area", "") + ".md")))):
            out.append(it)
        elif it["kind"] == "wrong" and it.get("id", "").startswith(area + "-"):
            out.append(it)
        elif it["kind"] in ("raw", "decision") and (area in it.get("areas", []) or area == "general"):
            out.append(it)
    return out


def _front(text):
    m = KN.FRONT.match(text)
    return (m.group(0), text[m.end():]) if m else ("", text)


PROMPT = """You curate one knowledge file that Claude PR reviewers read before each review.
Goal: a SHORT list of high-value review checks. Each lesson is a general pattern a reviewer can apply to a new PR,
not a one-off detail of an old PR.

Rules:
- At most {budget} lessons. Fewer is better when quality is higher.
- Merge lessons that say the same thing into one general pattern. List the merged ids in merged_from.
- Keep every kept lesson's id. New lessons get the next free id with the prefix "{prefix}-" (two digits, e.g. {prefix}-07).
- Score with the stats: hits are real problems a lesson led to; misses mean it was checked and found nothing; wrong means
  it misled a reviewer. Prefer lessons with hits and recent use. Drop or rewrite lessons marked wrong.
  Lessons with many misses and no hits go to the archive.
- Lessons seen to be noise (the user rejected such comments) stay only as short "Avoid:" lessons, if they still help.
- Each lesson: one or two short sentences, plain English, then evidence in brackets, e.g. [backend #123 M1, proven].
  Keep at most 3 PR references in the evidence.
- Group lessons under a few short headings (2 to 6 groups).
- No people's names, no author judgements, no secrets.
- Put every lesson you remove in "archived" with a one-line reason. Do not lose information silently.

Return ONLY a JSON object, no prose, no code fence:
{{"title": "<one-line title>", "groups": [{{"heading": "...", "lessons": [{{"id": "{prefix}-01", "text": "...", "evidence": "[...]", "merged_from": []}}]}}],
  "archived": [{{"id": "...", "text": "...", "reason": "..."}}], "changes": ["<short note>", "..."]}}

File: {rel}
Current content:
<<<
{body}
>>>

Stats per lesson id (empty when a lesson has no id yet):
{stats}

How to use "decision" items (the user's approve/reject of drafted PR comments, with optional reasons and notes):
- Repeated reject reasons (for example "Pre-existing, not made worse", "Too minor", "Not proven") become short
  "Avoid:" lessons in general.md, or in the area file when they are about that area's code.
- "Important catch" / "Would have missed it" approvals are hits: keep or add the lesson behind that finding.
- "Wrong severity" tunes how findings in this area should be graded.
- An edit (draft vs edited_to) shows the user's preferred wording, length and tone: style lessons go to general.md.
- One decision alone is weak evidence; prefer patterns seen in two or more PRs, unless the user's note says it is a rule.

New input from recent reviews (new lessons, wrong marks, raw "Lessons" sections and draft decisions):
{inbox}
"""


def _call_claude(prompt, model=None):
    cmd = ["claude", "-p", prompt, "--output-format", "text", "--tools", ""]
    if model:
        cmd += ["--model", model]
    rc, out, err = C.run(cmd, timeout=600)
    if rc != 0:
        raise RuntimeError("claude failed: " + (err or out).strip()[-300:])
    t = out.strip()
    t = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", t)
    s, e = t.find("{"), t.rfind("}")
    return json.loads(t[s:e + 1])


def render(head, data):
    lines = [head.rstrip("\n")] if head else []
    lines.append(data.get("title", "").strip())
    for g in data.get("groups") or []:
        lines += ["", f"## {g['heading'].strip()}"]
        for l in g.get("lessons") or []:
            ev = (" " + l["evidence"].strip()) if l.get("evidence") else ""
            lines.append(f"- ({l['id']}) {l['text'].strip()}{ev}")
    return "\n".join(lines).strip() + "\n"


def count(data):
    return sum(len(g.get("lessons") or []) for g in data.get("groups") or [])


def learn(only=None, all_files=False, model=None):
    """Write proposals to knowledge/.proposed/. Returns lines to print."""
    harvest_decisions()
    inbox = _inbox()
    stats = _load_json(kdir("stats.json"), {})
    prop = kdir(".proposed")
    shutil.rmtree(prop, ignore_errors=True)
    os.makedirs(prop)
    out, plan = [], {}
    for rel in files():
        if only and area_of(rel) != only and rel != only:
            continue
        text = open(kdir(rel)).read()
        head, body = _front(text)
        mine = _items_for(rel, inbox)
        budget = BUDGET.get(rel, AREA_BUDGET)
        n_now = sum(1 for line in body.splitlines() if LESSON_RX.match(line))
        if not (all_files or only or mine or n_now > budget):
            continue
        prefix = "gen" if rel == "general.md" else area_of(rel)
        ids = re.findall(r"^\s*-\s+\(([a-z0-9-]+)\)", body, re.M)
        st = {i: stats[i] for i in ids if i in stats}
        prompt = PROMPT.format(budget=budget, prefix=prefix, rel=rel, body=body.strip(),
                               stats=json.dumps(st, indent=1) if st else "(none yet)",
                               inbox=json.dumps([{k: v for k, v in it.items() if k not in ("key", "done", "at")}
                                                 for it in mine], indent=1) if mine else "(none)")
        try:
            data = _call_claude(prompt, model)
            if count(data) > budget:
                data = _call_claude(prompt + f"\n\nYour last answer had {count(data)} lessons. The limit is {budget}. "
                                    "Merge or archive more.", model)
        except (RuntimeError, ValueError) as e:
            out.append(f"{rel}: failed ({e})")
            continue
        new_text = render(head, data)
        dst = os.path.join(prop, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w") as f:
            f.write(new_text)
        plan[rel] = {"archived": data.get("archived") or [], "changes": data.get("changes") or [],
                     "merged": {l["id"]: l.get("merged_from") or [] for g in data.get("groups") or []
                                for l in g.get("lessons") or [] if l.get("merged_from")},
                     "inbox_keys": sorted({it["key"] for it in mine}), "before": n_now, "after": count(data)}
        out.append(f"{rel}: {n_now} -> {count(data)} lessons, {len(plan[rel]['archived'])} archived")
    _save_json(os.path.join(prop, "plan.json"), plan)
    return out


def diff(rel=None):
    prop = kdir(".proposed")
    plan = _load_json(os.path.join(prop, "plan.json"), {})
    out = []
    for r in plan:
        if rel and r != rel and area_of(r) != rel:
            continue
        a = open(kdir(r)).read().splitlines()
        b = open(os.path.join(prop, r)).read().splitlines()
        out += list(difflib.unified_diff(a, b, f"live/{r}", f"proposed/{r}", lineterm="", n=0))
        for c in plan[r]["changes"]:
            out.append(f"# change: {c}")
    return out


def apply():
    prop = kdir(".proposed")
    plan = _load_json(os.path.join(prop, "plan.json"), {})
    if not plan:
        return ["nothing proposed; run `review-ops learn` first"]
    stats = _load_json(kdir("stats.json"), {})
    keys, out = set(), []
    stamp = dt.date.today().isoformat()
    for rel, p in plan.items():
        shutil.copy2(kdir(rel), kdir(rel + ".bak"))
        shutil.copy2(os.path.join(prop, rel), kdir(rel))
        if p["archived"]:
            arch = kdir(rel.replace(".md", ".archive.md"))
            with open(arch, "a") as f:
                f.write(f"\n## Archived {stamp}\n")
                for a in p["archived"]:
                    f.write(f"- ({a.get('id', '?')}) {a.get('text', '').strip()} — {a.get('reason', '').strip()}\n")
        for keep, gone in p["merged"].items():
            for g in gone:
                if g in stats and g != keep:
                    s, t = stats.pop(g), stats.setdefault(keep, {"hits": 0, "misses": 0, "wrong": 0, "last": "", "prs": []})
                    for k in ("hits", "misses", "wrong"):
                        t[k] += s.get(k, 0)
                    t["last"] = max(t.get("last", ""), s.get("last", ""))
                    t["prs"] = list(dict.fromkeys(t["prs"] + s.get("prs", [])))[-8:]
        keys.update(p["inbox_keys"])
        out.append(f"{rel}: applied ({p['before']} -> {p['after']}); backup {rel}.bak")
    _save_json(kdir("stats.json"), stats)
    inbox = _inbox()
    for it in inbox:
        if it.get("key") in keys:
            it["done"] = True
    _write_inbox(inbox)
    shutil.rmtree(prop, ignore_errors=True)
    return out


def status():
    inbox = [it for it in _inbox() if not it.get("done")]
    out = [f"inbox: {len(inbox)} open item(s)"]
    for rel in files():
        body = _front(open(kdir(rel)).read())[1]
        n = sum(1 for line in body.splitlines() if LESSON_RX.match(line))
        b = BUDGET.get(rel, AREA_BUDGET)
        out.append(f"  {rel:32} {n:3} lessons (budget {b}){'  OVER' if n > b else ''}  "
                   f"{len(_items_for(rel, inbox))} new input")
    if os.path.exists(kdir(".proposed", "plan.json")):
        out.append("a proposal is waiting: `review-ops learn --diff`, then `review-ops learn --apply`")
    return out
