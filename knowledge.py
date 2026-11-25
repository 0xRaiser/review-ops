"""Reviewer knowledge: lessons from past reviews, added to each new brief and recheck prompt.

Files live in <pr_reviews>/knowledge/ (or `knowledge_dir` in the config):
  general.md              always used
  <repo>/<area>.md        used when the PR changes a path listed in its front matter:
                          ---
                          paths: [crates/oms/, crates/common/src/orderbook.rs]
                          ---
An area file with no `paths` line is used for every PR in that repo.
"""
import glob
import os
import re

import config as CFG
import registry as R

MAX_CHARS = 20000          # cap for the whole section, so briefs stay small
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def folder():
    return CFG.KNOWLEDGE_DIR or os.path.join(R.PR_REVIEWS, "knowledge")


def _read(path):
    """(paths or None, body). paths None = applies to every PR of the repo."""
    try:
        text = open(path).read()
    except OSError:
        return [], ""
    m = FRONT.match(text)
    if not m:
        return None, text.strip()
    paths = None
    pm = re.search(r"^paths:\s*\[(.*?)\]\s*$", m.group(1), re.M)
    if pm:
        paths = [x.strip().strip("'\"") for x in pm.group(1).split(",") if x.strip().strip("'\"")]
    return paths, text[m.end():].strip()


def select(repo, files):
    """Knowledge files that fit this PR: [(relative path, body)], general first."""
    base = folder()
    out = []
    g = os.path.join(base, "general.md")
    if os.path.exists(g):
        out.append(("general.md", _read(g)[1]))
    for path in sorted(glob.glob(os.path.join(base, repo, "*.md"))):
        if path.endswith(".archive.md"):  # archived lessons are kept for reference only
            continue
        paths, body = _read(path)
        if not body:
            continue
        if paths is None or any(f.startswith(p) for f in files for p in paths):
            out.append((os.path.relpath(path, base), body))
    return out


def section(repo, files):
    """The '## Known risks' block for a brief or prompt ('' when there is nothing)."""
    picked = select(repo, files)
    if not picked:
        return ""
    parts, used = [], 0
    for rel, body in picked:
        body = re.sub(r"^#{1,4} ", "#### ", body, flags=re.M)  # keep the brief's own headings on top
        block = f"### {rel}\n{body}\n"
        if used + len(block) > MAX_CHARS:
            parts.append(f"### {rel}\n(left out: the section is full; read `{os.path.join(folder(), rel)}`)\n")
            continue
        parts.append(block)
        used += len(block)
    return ("## Known risks in this area (lessons from past reviews)\n"
            "Check each lesson that fits this PR. Do not edit the knowledge files yourself. End the report with a "
            "`## Lessons` section that holds one fenced block, one line per item (the learner reads it):\n"
            "```lessons\n"
            "hit <id> <finding id> <proven|fixed|read>   a lesson led you to a real problem\n"
            "miss <id>                                   you checked it and found nothing\n"
            "wrong <id> <why>                            the lesson was wrong or misleading here\n"
            "new <area> | <lesson, one or two sentences> | [<repo> #<PR> <finding id>, <proven|fixed|read>]\n"
            "```\n"
            "<id> is the (id) at the start of a lesson. <area> is the file name without .md (for example oms), "
            "or general. Add a `new` line only for a pattern that would help on other PRs.\n\n"
            + "\n".join(parts) + "\n")
