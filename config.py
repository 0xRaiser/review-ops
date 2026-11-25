"""Settings for Review Ops, read once from a TOML file.

Search order: $REVIEW_OPS_CONFIG, then config.toml next to this file, then
~/.config/review-ops/config.toml. See config.example.toml for every key.
"""
import os
import subprocess
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))


def _find():
    for p in (os.environ.get("REVIEW_OPS_CONFIG"), os.path.join(HERE, "config.toml"),
              os.path.expanduser("~/.config/review-ops/config.toml")):
        if p and os.path.exists(p):
            return p
    raise SystemExit("review-ops: no config found. Copy config.example.toml to config.toml and edit it.")


def _path(v):
    return os.path.abspath(os.path.expanduser(v)) if v else ""


PATH = _find()
with open(PATH, "rb") as f:
    _c = tomllib.load(f)

WORKSPACE = _path(_c.get("workspace", ""))
if not WORKSPACE or not os.path.isdir(WORKSPACE):
    raise SystemExit(f"review-ops: 'workspace' in {PATH} must be an existing folder")
PR_REVIEWS = os.environ.get("REVIEW_OPS_PR_REVIEWS") or _path(_c.get("pr_reviews", "")) \
    or os.path.join(WORKSPACE, "pr-reviews")
ORG = _c.get("github_org", "")
if not ORG:
    raise SystemExit(f"review-ops: 'github_org' is missing in {PATH}")
# GitHub repo name -> local folder under the workspace
REPOS = dict(_c.get("repos") or {})
if not REPOS:
    raise SystemExit(f"review-ops: add at least one repo under [repos] in {PATH}")
EXCLUDE_AUTHORS = set(_c.get("exclude_authors") or [])
PORT = int(_c.get("port", 8765))

# Optional: quality gates on a remote box. Empty = the box status is not shown.
QUALITY_REMOTE = _path(_c.get("quality_remote", ""))
AWS_PROFILE = _c.get("aws_profile", "")

# Lessons from past reviews. Empty = <pr_reviews>/knowledge.
KNOWLEDGE_DIR = _path(_c.get("knowledge_dir", ""))

# iTerm2: a Python that has the `iterm2` package, and the two helper scripts.
ITERM_PYTHON = _path(_c.get("iterm_python", "~/.local/share/it2venv/bin/python"))
TOOLS = _path(_c.get("iterm_tools", "")) or os.path.join(HERE, "tools")


def _gh_login():
    try:
        p = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, timeout=15,
                           stdin=subprocess.DEVNULL)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# Your GitHub login: set it in the config, or it is read from `gh api user`.
GITHUB_USER = _c.get("github_user", "") or _gh_login()
if not GITHUB_USER:
    raise SystemExit(f"review-ops: set 'github_user' in {PATH}, or log in with `gh auth login`")
