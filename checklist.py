"""Review checklist: what a review session runs. Used by New review and Re-review.

The options change the brief (new review) or the recheck prompt (re-review). Defaults: built-in,
then the user's last choices per repo and kind (registry "checklist_defaults"), then automatic rules
(DB tests when the PR touches DB code, the linked-PR check when the PR is in a link group).
"""
import re

import collect as C
import registry as R

GROUPS = [
    {"key": "passes", "label": "Review passes", "items": [
        {"key": "depth", "label": "Depth (how much of the map: light = scope + rollout, standard = + flow, reuse, "
                                  "triggers, tests, full = + right-sized and practice)", "type": "select",
         "default": "standard", "choices": ["light", "standard", "full"]},
        {"key": "code_review", "label": "/code-review", "type": "bool", "default": True, "pass": "code-review"},
        {"key": "pr_reviewer", "label": "In-depth pr-reviewer review with repo skills", "type": "bool", "default": True,
         "pass": "pr-reviewer"},
        {"key": "self_review", "label": "self-review", "type": "bool", "default": True, "pass": "self-review"},
        {"key": "security", "label": "Security pass", "type": "bool", "default": True, "pass": "security"},
        {"key": "round1", "label": "Recheck earlier findings and the author's replies", "type": "bool", "default": True,
         "only": "rereview", "pass": "recheck-findings"},
    ]},
    {"key": "gates", "label": "Quality gates on the box", "items": [
        {"key": "crapper", "label": "crapper", "type": "bool", "default": True, "pass": "crapper"},
        {"key": "mutator", "label": "mutator", "type": "bool", "default": True, "pass": "mutator"},
        {"key": "cmut", "label": "cargo-mutants", "type": "bool", "default": True, "pass": "cargo-mutants"},
        {"key": "db", "label": "With DB tests (--db)", "type": "bool", "default": False, "pass": "db-tests"},
        {"key": "limit", "label": "Limit to money and logic files", "type": "bool", "default": True},
        {"key": "features", "label": "Cargo features", "type": "text", "default": "", "placeholder": "e.g. common/pick7s"},
        {"key": "workers", "label": "Mutator workers", "type": "number", "default": 2, "min": 1, "max": 8},
    ]},
    {"key": "proof", "label": "Proof", "items": [
        {"key": "probe_major", "label": "Probe tests for each blocker and major", "type": "bool", "default": True,
         "pass": "probes"},
        {"key": "probe_minor", "label": "Also for minors that claim a wrong result", "type": "bool", "default": True,
         "pass": "probes-minor"},
        {"key": "kill_tests", "label": "Kill tests for mutation survivors", "type": "bool", "default": True,
         "pass": "kill-tests"},
    ]},
    {"key": "output", "label": "Output", "items": [
        {"key": "effort", "label": "Effort level", "type": "select", "default": "high", "choices": ["high", "xhigh", "max"]},
        {"key": "post", "label": "Post on GitHub after I confirm in the session", "type": "bool", "default": True},
        {"key": "drafts", "label": "Draft thread replies", "type": "bool", "default": True, "only": "rereview"},
        {"key": "approve", "label": "Approve when clean, after I confirm", "type": "bool", "default": False},
    ]},
    {"key": "links", "label": "Linked PRs", "items": [
        {"key": "linked", "label": "Check that linked PRs agree", "type": "bool", "default": False, "pass": "linked-prs"},
    ]},
    {"key": "knowledge", "label": "Knowledge", "items": [
        {"key": "knowledge", "label": "Add lessons from past reviews for the changed areas", "type": "bool",
         "default": True, "pass": "knowledge"},
    ]},
]
KINDS = ("new", "rereview")
DB_RX = re.compile(r"(^|/)migrations?/|\.sql$|(^|/)db/|(^|/)store/|repository|sqlx", re.I)


def items(kind):
    for g in GROUPS:
        for it in g["items"]:
            if it.get("only") in (None, kind):
                yield g, it


def _clean(it, v):
    t = it["type"]
    if t == "bool":
        return bool(v)
    if t == "number":
        try:
            return max(it.get("min", 1), min(it.get("max", 99), int(v)))
        except (TypeError, ValueError):
            return it["default"]
    if t == "select":
        return v if v in it["choices"] else it["default"]
    v = str(v or "").strip()
    return v if re.fullmatch(r"[A-Za-z0-9_/,.-]{0,200}", v) else ""


def normalize(kind, opts):
    opts = opts or {}
    return {it["key"]: _clean(it, opts.get(it["key"], it["default"])) for _, it in items(kind)}


def db_files(repo, number):
    data = C.gh_pr_files(repo, number)
    return [f["filename"] for f in data.get("files", []) if DB_RX.search(f["filename"])] if not data.get("_error") else []


def spec(repo, number, kind):
    """Groups with values for the dialog, plus notes on automatic choices."""
    kind = kind if kind in KINDS else "new"
    reg = R.load()
    saved = ((reg.get("checklist_defaults") or {}).get(repo) or {}).get(kind) or {}
    vals = normalize(kind, saved)
    notes = {}
    dbf = db_files(repo, number)
    if dbf and not vals["db"]:
        vals["db"] = True
        notes["db"] = "On because the PR touches " + ", ".join(dbf[:3]) + (" and more" if len(dbf) > 3 else "")
    in_group = bool(R.link_of(reg, repo, number))
    vals["linked"] = in_group
    notes["linked"] = "The PR is in a link group." if in_group else "The PR is not linked to other PRs."
    groups = []
    for g in GROUPS:
        its = [dict(it, value=vals[it["key"]], note=notes.get(it["key"], ""),
                    disabled=(it["key"] == "linked" and not in_group))
               for it in g["items"] if it.get("only") in (None, kind)]
        if its:
            groups.append({"key": g["key"], "label": g["label"], "items": its})
    return {"kind": kind, "groups": groups, "saved": bool(saved)}


def values(repo, number, kind):
    """The dialog's starting values as a plain dict."""
    return {it["key"]: it["value"] for g in spec(repo, number, kind)["groups"] for it in g["items"]}


def save_defaults(repo, kind, opts):
    with R.locked():
        reg = R.load()
        o = dict(opts)
        o.pop("linked", None)  # this one follows the PR, not the user's habit
        reg.setdefault("checklist_defaults", {}).setdefault(repo, {})[kind] = o
        R.save(reg)


def reset_defaults(repo, kind):
    with R.locked():
        reg = R.load()
        (reg.get("checklist_defaults") or {}).get(repo, {}).pop(kind, None)
        R.save(reg)


def passes(kind, opts):
    out = [it["pass"] for _, it in items(kind) if it.get("pass") and opts.get(it["key"])]
    if opts.get("limit") and any(opts.get(k) for k in ("crapper", "mutator", "cmut")):
        out.append("limited-files")
    return out


def gate_commands(number, opts, base):
    """quality-remote commands for the ticked gates (empty list = no box gates)."""
    tools = [t for t, k in (("crap", "crapper"), ("mutate", "mutator"), ("cmut", "cmut")) if opts.get(k)]
    if not tools or not C.QUALITY_REMOTE:  # no quality_remote in the config: no box gates
        return []
    if tools == ["crap", "mutate", "cmut"]:
        runs = ["all"]
    elif tools == ["crap", "mutate"]:
        runs = ["both"]
    else:
        runs = tools  # one run per tool when the pair has no single --tool value
    flags = ""
    if base == "main":
        flags += " --base main"
    if opts.get("db"):
        flags += " --db"
    if opts.get("features"):
        flags += f" --features {opts['features']}"
    cmds = []
    for t in runs:
        w = f" --workers {opts['workers']}" if t in ("all", "both", "mutate") else ""
        cmds.append(f"quality-remote pr {number} --tool {t}{flags}{w}")
    return cmds
