#!/usr/bin/env python3
"""Review Ops: local dashboard for PR reviews. Serves on 127.0.0.1 only.

Usage: server.py [--port 8765] [--dry-run]
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import actions as A  # noqa: E402
import checklist as K  # noqa: E402
import drafts as D  # noqa: E402
import address as AD  # noqa: E402
import collect as C  # noqa: E402
import model as M  # noqa: E402
import config as CFG  # noqa: E402
import registry as R  # noqa: E402

STATIC = os.path.join(HERE, "static")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
TEXT_EXT = {".md", ".txt", ".json", ".rs", ".sh", ".diff", ".log", ".py", ".edn", ".exit", ""}
MAX_FILE = 1_500_000
PORT = CFG.PORT
_refresh_lock = threading.Lock()


def safe_path(rel):
    """Only files under pr-reviews/ or a repo's .metrics/ may be read."""
    full = os.path.realpath(os.path.join(R.WUP, rel))
    allowed = [os.path.realpath(R.PR_REVIEWS)] + \
        [os.path.realpath(os.path.join(R.repo_dir(r), ".metrics")) for r in R.REPO_DIRS]
    if not any(full == a or full.startswith(a + os.sep) for a in allowed):
        return None
    return full


class H(BaseHTTPRequestHandler):
    server_version = "review-ops"

    def log_message(self, fmt, *args):  # short log, no bodies
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # -------- helpers
    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body, default=str) if ctype == "application/json"
                                                     else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # -------- GET
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html") or u.path.startswith("/pr/"):
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            if u.path == "/api/config.js":
                body = "window.RO_CONFIG = " + json.dumps({"org": R.ORG, "repos": list(R.REPO_DIRS), "me": C.ME, "aws_profile": CFG.AWS_PROFILE}) + ";\n"
                return self._send(200, body.encode(), "application/javascript; charset=utf-8")
            if u.path == "/api/health":
                return self._send(200, {"ok": True, "dry_run": A.GLOBAL_DRY_RUN, "port": PORT})
            if u.path == "/api/myprs":
                return self._send(200, M.my_prs())
            if u.path == "/api/prs":
                return self._send(200, M.all_summaries())
            m = re.match(r"^/api/pr/([\w-]+)/(\d+)$", u.path)
            if m:
                return self._send(200, M.detail(m.group(1), int(m.group(2))))
            m = re.match(r"^/api/prfiles/([\w-]+)/(\d+)$", u.path)
            if m and m.group(1) in R.REPO_DIRS:
                return self._send(200, M.pr_files(m.group(1), int(m.group(2))))
            m = re.match(r"^/api/openprs/([\w-]+)$", u.path)
            if m and m.group(1) in R.REPO_DIRS:
                return self._send(200, M.open_prs(m.group(1)))
            m = re.match(r"^/api/drafts/([\w-]+)/(\d+)$", u.path)
            if m and m.group(1) in R.REPO_DIRS:
                return self._send(200, D.load(m.group(1), int(m.group(2))))
            m = re.match(r"^/api/address/([\w-]+)/(\d+)$", u.path)
            if m and m.group(1) in R.REPO_DIRS:
                return self._send(200, AD.spec(m.group(1), int(m.group(2))))
            if u.path == "/api/checklist":
                repo_, n_ = q.get("repo", ""), q.get("number", "0")
                if repo_ not in R.REPO_DIRS or not n_.isdigit():
                    return self._send(400, {"error": "bad repo or number"})
                return self._send(200, K.spec(repo_, int(n_), q.get("kind", "new")))
            if u.path == "/api/file":
                return self._file(q.get("path", ""))
            if u.path == "/api/registry":
                return self._send(200, R.load())
            return self._send(404, {"error": "not found"})
        except Exception as e:  # keep the server up; short error only
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})

    def _static(self, name):
        full = os.path.realpath(os.path.join(STATIC, name))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as f:
            return self._send(200, f.read(), TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    def _file(self, rel):
        full = safe_path(rel)
        if not full or not os.path.exists(full):
            return self._send(404, {"error": "not found or not allowed"})
        if os.path.isdir(full):
            items = []
            for f in sorted(os.listdir(full)):
                if f.startswith("."):
                    continue
                fp = os.path.join(full, f)
                items.append({"name": f, "dir": os.path.isdir(fp), "size": os.path.getsize(fp),
                              "path": os.path.relpath(fp, R.WUP)})
            return self._send(200, {"dir": True, "path": rel, "items": items, "abs": full})
        if os.path.splitext(full)[1] not in TEXT_EXT:
            return self._send(415, {"error": "not a text file"})
        size = os.path.getsize(full)
        with open(full, "rb") as f:
            data = f.read(MAX_FILE).decode("utf-8", "replace")
        return self._send(200, {"dir": False, "path": rel, "abs": full, "size": size,
                                "truncated": size > MAX_FILE, "text": data})

    # -------- POST
    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        origin = self.headers.get("Origin", "")
        if origin and not re.match(r"^http://(127\.0\.0\.1|localhost):%d$" % PORT, origin):
            return self._send(403, {"error": "bad origin"})
        if self.headers.get("X-Review-Ops") != "1":
            return self._send(403, {"error": "missing header"})
        try:
            body = self._json_body()
            u = urlparse(self.path).path
            repo, number = body.get("repo"), body.get("number")
            dry = bool(body.get("dry_run"))
            if number is not None:
                number = int(number)
            if repo is not None and repo not in R.REPO_DIRS:
                return self._send(400, {"error": "unknown repo"})
            if u == "/api/refresh":
                return self._send(200, self._refresh(repo, number))
            opts = body.get("options")
            if opts is not None and not isinstance(opts, dict):
                return self._send(400, {"error": "options must be an object"})
            if u == "/api/action/rereview":
                return self._send(200, A.re_review(repo, number, dry_run=dry, opts=opts))
            if u == "/api/action/new":
                return self._send(200, A.new_review(repo, number, dry_run=dry, opts=opts))
            if u == "/api/action/address":
                th, ck = body.get("threads") or [], body.get("checks") or []
                if not (isinstance(th, list) and isinstance(ck, list)) or len(th) > 500:
                    return self._send(400, {"error": "bad threads or checks"})
                a_sid, a_opts = body.get("session") or "", body.get("options") or {}
                if a_sid and not re.fullmatch(r"[0-9a-f-]{36}", a_sid):
                    return self._send(400, {"error": "bad session id"})
                if not isinstance(a_opts, dict):
                    return self._send(400, {"error": "options must be an object"})
                return self._send(200, A.address(repo, number, [str(x) for x in th], [str(x) for x in ck],
                                                 a_opts, sid=a_sid, dry_run=dry, copied=bool(body.get("copied"))))
            if u == "/api/drafts/save":
                return self._send(200, D.save(repo, number, body))
            if u == "/api/drafts/ask":
                return self._send(200, D.ask(repo, number, str(body.get("kind") or ""), str(body.get("id") or ""),
                                             str(body.get("text") or "")))
            if u == "/api/action/drafts_copied":
                return self._send(200, A.drafts_copied(repo, number, dry_run=dry))
            if u == "/api/action/send_drafts":
                return self._send(200, A.send_drafts(repo, number, force=bool(body.get("force")), dry_run=dry))
            if u == "/api/checklist/reset":
                K.reset_defaults(repo, body.get("kind") if body.get("kind") in K.KINDS else "new")
                return self._send(200, {"ok": True})
            sid = body.get("session") or ""
            if sid and not re.fullmatch(r"[0-9a-f-]{36}", sid):
                return self._send(400, {"error": "bad session id"})
            if u == "/api/action/my_resume":
                return self._send(200, A.my_resume(repo, number, sid, dry_run=dry))
            if u == "/api/action/my_start":
                return self._send(200, A.my_start(repo, number, dry_run=dry))
            if u == "/api/action/my_focus":
                return self._send(200, A.my_focus(sid, dry_run=dry))
            if u == "/api/action/my_pick":
                return self._send(200, A.my_pick(repo, number, sid))
            if u == "/api/action/link":
                return self._send(200, A.link(repo, number, body.get("name"), body.get("with_repo"),
                                              int(body.get("with_number") or 0)))
            if u == "/api/action/unlink":
                return self._send(200, A.unlink(repo, number))
            if u == "/api/action/remove":
                return self._send(200, A.remove(repo, number, dry_run=dry))
            if u == "/api/action/focus":
                return self._send(200, A.focus(repo, number, dry_run=dry))
            return self._send(404, {"error": "not found"})
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)[:200]})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})

    def _sync_titles(self):
        """Keep title and author in the registry in step with GitHub (cached data only)."""
        with R.locked():
            reg, changed = R.load(), False
            for p in R.active(reg):
                pr = C.gh_pr(p["repo"], p["number"])
                if pr.get("_error"):
                    continue
                t, a = pr.get("title") or "", ((pr.get("author") or {}).get("login") or "")
                if t and (p.get("title") != t or (a and p.get("author") != a)):
                    p["title"], p["author"] = t, a or p.get("author", "")
                    changed = True
            if changed:
                R.save(reg)

    def _refresh(self, repo, number):
        if not _refresh_lock.acquire(timeout=1):
            return {"ok": False, "error": "A refresh is already running"}
        try:
            t = time.time()
            reg = R.load()
            only = (repo, number) if repo and number else None
            prs = [p for p in R.active(reg) if only is None or (p["repo"], p["number"]) == only]
            for p in prs:
                C.gh_pr(p["repo"], p["number"], force=True)
                if only is not None:
                    C.gh_pr_files(p["repo"], p["number"], force=True)
                for n in p.get("previous_numbers", []):
                    C.gh_pr(p["repo"], n, force=True)
            if only is None:
                M.my_prs(force=True)
                for r in R.REPO_DIRS:
                    C.gh_open_prs(r, force=True)
                    C.gh_requested(r, force=True)
            elif not prs:
                C.gh_pr(repo, number, force=True)
            self._sync_titles()
            box = M.refresh_box(only)
            return {"ok": True, "secs": round(time.time() - t, 1), "sso_expired": box.get("sso_expired", False)}
        finally:
            _refresh_lock.release()


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("REVIEW_OPS_PORT", PORT)))
    ap.add_argument("--dry-run", action="store_true", help="actions print the command instead of opening tabs")
    a = ap.parse_args()
    PORT = a.port
    if a.dry_run:
        A.GLOBAL_DRY_RUN = True
    if not os.path.exists(R.REGISTRY):
        os.makedirs(R.PR_REVIEWS, exist_ok=True)
        if os.path.exists(R.STATUS_MD):
            R.import_existing()  # older setups kept the list in STATUS.md only
        else:
            R.save(R.load())  # first run: an empty registry (also writes STATUS.md)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    print(f"review-ops on http://127.0.0.1:{PORT}  dry_run={A.GLOBAL_DRY_RUN}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
