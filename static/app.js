// Review Ops front end. Plain JS, no build step.
(function () {
  "use strict";
  var esc = MD.esc;
  var CFG = window.RO_CONFIG || { org: "", repos: [], me: "" };
  var state = { list: null, group: localStorage.getItem("ro-scope") === "all" ? "all" : "mine", filter: "", cur: null, detail: null, tab: "conversation" };
  var $ = function (s, el) { return (el || document).querySelector(s); };

  function api(path, body) {
    var opt = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-Review-Ops": "1" }, body: JSON.stringify(body)
    };
    return fetch(path, opt).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.statusText); return j; });
    });
  }
  function ago(t) {
    if (!t) return "";
    var s = (Date.now() - (typeof t === "number" ? t * 1000 : Date.parse(t))) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }
  // t: ISO string, or a number of seconds since 1970
  function when(t) { return t ? '<span title="' + esc(new Date(typeof t === "number" ? t * 1000 : t).toLocaleString()) + '">' + ago(t) + "</span>" : ""; }
  function chip(text, cls, title) { return '<span class="chip ' + (cls || "") + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + text + "</span>"; }
  var VERDICT_CLS = { request_changes: "red", approve_with_nits: "amber", approved: "green", not_reviewed: "", on_hold: "", in_progress: "blue" };
  function verdictChip(code, label) { return chip(esc(label || code), VERDICT_CLS[code] || ""); }
  var BOX_TEXT = { db: "Box: DB setup", crap: "Box: crapper running", mutate: "Box: mutator running", cmut: "Box: cargo-mutants running",
    running: "Box: running", starting: "Box: starting", done: "Box: done", sso_expired: "SSO expired", timeout: "Box: no answer",
    no_run: "", stopped: "Box: stopped early", error: "Box: error", unreachable: "Box: cannot reach" };

  function sessionChip(s) {
    if (!s) return "";
    if (s.state === "working") return chip('<span class="dot pulse"></span>Working' + (s.tty ? " · " + esc(s.tty) : ""), "green", "The review session is open and busy");
    if (s.state === "idle") return chip('<span class="dot"></span>Idle' + (s.tty ? " · " + esc(s.tty) : ""), "blue", "The review session is open and waiting");
    if (s.state === "closed") return chip("Session closed", "", "Re-review resumes it with its full context");
    return chip("No session", "");
  }
  function boxChip(b) {
    if (!b || !BOX_TEXT[b.phase]) return "";
    var cls = /^(sso_expired|error|unreachable)$/.test(b.phase) ? "red" : b.phase === "done" ? "" : "purple";
    return chip(esc(BOX_TEXT[b.phase]), cls, b.line || "");
  }
  // The one most important thing to show for a PR, or null.
  function topNeed(p) {
    var s = p.session || {}, n = p.needs || [];
    var has = function (rx) { return n.some(function (x) { return rx.test(x.text); }); };
    if (s.waiting_for_you && s.tty) return chip("Answer in " + esc(s.tty), "you", "The session asks you something");
    if (p.unposted && p.drafts) return chip(p.drafts.pending + " pending · " + p.drafts.approved + " ok · " + p.drafts.rejected + " no", p.drafts.pending ? "you" : "green", "Drafts: pending / approved / rejected");
    if (p.unposted) return chip("Not posted yet", "you", p.unposted);
    if (p.new_replies) return chip(p.new_replies + " repl" + (p.new_replies > 1 ? "ies" : "y"), "you", "New replies since our last look");
    if (p.new_commits && p.round) return chip("+" + p.new_commits + " commit" + (p.new_commits > 1 ? "s" : ""), "you", "New commits since our review");
    if (p.box && p.box.phase === "sso_expired") return chip("SSO expired", "red");
    if (p.requested_me && !p.round) return chip("Review asked", "you", "GitHub asks you to review this PR");
    if (p.requested_me) return chip("Review asked again", "you");
    if (p.unposted) return chip("Not posted yet", "you", p.unposted);
    if (p.ci && p.ci.state === "FAILURE") return chip("CI failing", "red", (p.ci.failing || []).join(", "));
    if (has(/^Ready to merge/)) return chip("Ready to merge", "green");
    if (s.state === "working") return chip('<span class="dot pulse"></span>Working', "green");
    if (p.box && /^(db|crap|mutate|cmut|running|starting)$/.test(p.box.phase)) return chip(esc(BOX_TEXT[p.box.phase]), "purple");
    if (p.error) return chip("GitHub error", "red", p.error);
    return null;
  }
  function chipsFor(p) {
    var h = [verdictChip(p.verdict, p.verdict_label)];
    if (p.round) h.push(chip("R" + p.round, "", "Round " + p.round));
    var t = topNeed(p);
    if (t) h.push(t);
    return h.join("");
  }
  function liveDot(p) {
    var s = (p.session || {}).state;
    if (s === "working") return '<span class="live working" title="Session working"></span>';
    if (s === "idle") return '<span class="live idle" title="Session open in ' + esc(p.session.tty || "") + '"></span>';
    return "";
  }
  function needsYou(p) { return (p.needs || []).some(function (n) { return n.kind === "you"; }); }

  // ------------------------------------------------------------------ sidebar
  var SECTIONS = [
    ["pending", "Pending", "Not reviewed yet"],
    ["in_progress", "In progress", "In the review process: a session or box run is going, or the session waits for your answer"],
    ["to_post", "To post", "The review is finished but not on GitHub yet: post it from its session"],
    ["reviewed", "Reviewed", "We reviewed it; no reply or fix from the author yet"],
    ["next", "Next review", "We reviewed it, and new commits, replies or a new review request need another review"],
    ["approved", "Approved", "Approved by us (with or without nits), not merged yet, nothing new since"],
    ["done", "Done", "Merged"]  ];
  function card(p) {
    var key = p.repo + "/" + p.number;
    var nums = "#" + p.number + (p.previous_numbers && p.previous_numbers.length ? " (was #" + p.previous_numbers.join(", #") + ")" : "");
    return '<a class="item' + (state.cur === key ? " on" : "") + '" href="#/' + key + '">' +
      '<span class="t" title="' + esc(p.title) + '">' + liveDot(p) + esc(p.title) + "</span>" +
      '<span class="sub">' + esc(p.repo) + " " + esc(nums) + " · " + esc(p.author || "") +
        (p.link ? ' · <span class="lk-tag" title="Linked PRs: ' + esc(p.link.name) + '">🔗 ' + esc(p.link.name) + "</span>" : "") + "</span>" +
      '<div class="chips">' + chipsFor(p) +
      (p.gh_state === "MERGED" || p.gh_state === "CLOSED"
        ? chip(p.gh_state === "MERGED" ? "Merged" : "Closed", p.gh_state === "MERGED" ? "purple" : "") +
          '<button class="btn btn-sm rm" data-remove="' + key + '" title="Take it off the list. Nothing changes on GitHub.">Remove</button>'
        : "") + "</div></a>";
  }
  function untrackedCard(u) {
    var key = u.repo + "/" + u.number;
    return '<a class="item' + (state.cur === key ? " on" : "") + '" href="#/' + key + '"><span class="t" title="' + esc(u.title) + '">' + esc(u.title) + "</span>" +
      '<span class="sub">' + esc(u.repo) + " #" + u.number + " · " + esc(u.author || "") + (u.draft ? " · draft" : "") +
        (u.link ? ' · <span class="lk-tag">🔗 ' + esc(u.link.name) + "</span>" : "") + "</span>" +
      '<div class="chips">' + (u.requested_me ? chip("Review asked", "you") : chip("Not tracked", "")) + "</div></a>";
  }
  // Open or closed state of each sidebar section, kept across reloads.
  function isOpen(key, dflt) {
    var v = localStorage.getItem("ro-sec-" + key);
    return v === null ? dflt : v === "1";
  }
  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (d.matches && d.matches("details.sec[data-sec]") && !state.filter && !state.fRepo && !state.fAuthor && !state.fLink) localStorage.setItem("ro-sec-" + d.dataset.sec, d.open ? "1" : "0");
  }, true);
  $("#f-repo").insertAdjacentHTML("beforeend", CFG.repos.map(function (r) { return "<option>" + esc(r) + "</option>"; }).join(""));
  state.fRepo = localStorage.getItem("ro-f-repo") || "";
  state.fAuthor = localStorage.getItem("ro-f-author") || "";
  state.fLink = localStorage.getItem("ro-f-link") || "";
  function fillFilters() {
    var all = state.list.prs.concat(state.list.untracked.filter(function (u) { return u.requested_me || state.group === "all"; }));
    var authors = {}, links = {};
    all.forEach(function (p) { if (p.author) authors[p.author] = 1; if (p.link) links[p.link.id] = p.link.name; });
    var put = function (id, first, items, cur) {
      var el = $(id);
      el.innerHTML = '<option value="">' + first + "</option>" + items.map(function (it) {
        return '<option value="' + esc(it[0]) + '"' + (it[0] === cur ? " selected" : "") + ">" + esc(it[1]) + "</option>";
      }).join("");
    };
    put("#f-author", "All authors", Object.keys(authors).sort().map(function (a) { return [a, a]; }), state.fAuthor);
    put("#f-link", "All features", Object.keys(links).sort().map(function (k) { return [k, "🔗 " + links[k]]; }), state.fLink);
    $("#f-link").hidden = !Object.keys(links).length && !state.fLink;
    $("#f-repo").value = state.fRepo;
    $("#f-clear").hidden = !(state.fRepo || state.fAuthor || state.fLink || state.filter);
  }
  ["repo", "author", "link"].forEach(function (k) {
    $("#f-" + k).addEventListener("change", function (e) {
      var key = "f" + k[0].toUpperCase() + k.slice(1);
      state[key] = e.target.value; localStorage.setItem("ro-f-" + k, e.target.value); renderSide();
    });
  });
  $("#f-clear").addEventListener("click", function () {
    ["repo", "author", "link"].forEach(function (k) { state["f" + k[0].toUpperCase() + k.slice(1)] = ""; localStorage.removeItem("ro-f-" + k); });
    state.filter = ""; $("#filter").value = ""; renderSide();
  });
  // One sidebar section: a fold with a count. Empty sections stay as a dim one-line heading.
  function secHtml(key, label, help, items, open) {
    return '<details class="sec' + (items.length ? "" : " empty-sec") + '" data-sec="' + key + '"' + (open && items.length ? " open" : "") + '>' +
      '<summary class="group-h" title="' + esc(help) + '"><span>' + label + '</span><span class="n">' + items.length + "</span></summary>" + items.join("") + "</details>";
  }
  function markCurrent() {  // keep the open PR visible in the sidebar
    var on = document.querySelector("#side-list .item.on");
    if (on && on.scrollIntoViewIfNeeded) on.scrollIntoViewIfNeeded(false);
    else if (on) on.scrollIntoView({ block: "nearest" });
  }
  function renderSide() {
    var el = $("#side-list");
    if (state.mode === "mine") { if (state.mine) renderMine(); return; }
    $("#f-author").hidden = false;
    if (!state.list) return;
    var f = state.filter.toLowerCase();
    var fr = state.fRepo, fa = state.fAuthor, fl = state.fLink;
    var match = function (x) {
      if (fr && x.repo !== fr) return false;
      if (fa && x.author !== fa) return false;
      if (fl && !(x.link && x.link.id === fl)) return false;
      return !f || (x.title + " " + x.number + " " + (x.author || "") + " " + x.repo).toLowerCase().indexOf(f) >= 0;
    };
    fillFilters();
    if (fr || fa || fl) f = f || " ";  // any filter: open every section, like a search
    var prs = state.list.prs.filter(match);
    var asked = state.list.untracked.filter(function (u) { return u.requested_me && match(u); });
    var h = "";
    SECTIONS.forEach(function (sec) {
      var items = prs.filter(function (p) { return p.section === sec[0]; }).map(card);
      if (sec[0] === "pending") items = items.concat(asked.map(untrackedCard));
      var open = f || isOpen(sec[0], sec[0] !== "done");  // a filter opens every section
      h += secHtml(sec[0], sec[1], sec[2], items, open);
    });
    if (state.group === "all") {
      var cutoff = Date.now() - 14 * 86400000;
      var other = state.list.untracked.filter(function (u) { return !u.requested_me && (f ? match(u) : Date.parse(u.updated) > cutoff); });
      if (other.length) h += secHtml("other", "Other open, active in 14 d", "Open PRs you do not track, updated in the last 14 days", other.map(untrackedCard), f || isOpen("other", false));
    }
    el.innerHTML = h;
    var sso = state.list.prs.some(function (p) { return p.box && p.box.phase === "sso_expired"; });
    var b = $("#banner");
    if (sso) { b.hidden = false; b.innerHTML = "AWS SSO has expired, so box status is unknown. Run <code>aws sso login" + (CFG.aws_profile ? " --profile " + esc(CFG.aws_profile) : "") + "</code>, then Refresh."; }
    else b.hidden = true;
    $("#updated").textContent = state.list.box_checked ? "Box checked " + ago(state.list.box_checked) : "Box not checked yet (press Refresh)";
  }
  function loadList() {
    if (state.mode === "mine") return api("/api/myprs").then(function (j) { state.mine = j; renderSide(); });
    return api("/api/prs").then(function (j) { state.list = j; renderSide(); });
  }
  var qmode = (location.search.match(/[?&]mode=(mine|review)/) || [])[1];  // a link can open a view
  state.mode = qmode || (localStorage.getItem("ro-mode") === "mine" ? "mine" : "review");
  document.querySelectorAll(".mode button").forEach(function (b) {
    b.classList.toggle("on", b.dataset.mode === state.mode);
    b.addEventListener("click", function () {
      state.mode = b.dataset.mode; localStorage.setItem("ro-mode", state.mode);
      document.querySelectorAll(".mode button").forEach(function (x) { x.classList.toggle("on", x === b); });
      $(".seg").style.display = state.mode === "mine" ? "none" : "";
      $("#side-list").innerHTML = '<p class="muted pad"><span class="spin"></span> Loading…</p>';
      loadList();
    });
  });
  $(".seg").style.display = state.mode === "mine" ? "none" : "";

  // ---------------- My PRs: sections and cards
  var MY_SECTIONS = [
    ["to_fix", "To fix or answer", "A reviewer asked for changes, or a thread waits for your answer"],
    ["rereview", "Waiting for re-review", "You pushed after a change request; the reviewer has to look again"],
    ["waiting_review", "Waiting for review", "No decision yet from the reviewers"],
    ["draft", "Draft", "Still a draft"],
    ["approved", "Approved", "Approved, not merged yet"],
    ["merged", "Merged (last 14 days)", "Merged"]
  ];
  function reviewersChip(p) {
    var parts = [];
    if (p.approvals) parts.push("✓" + p.approvals);
    if (p.changes) parts.push("✗" + p.changes);
    if (p.pending) parts.push("⏳" + p.pending);
    if (!parts.length) return chip("No reviewer yet", "");
    var title = (p.reviewers || []).map(function (r) { return r.by + ": " + r.state.toLowerCase().replace("_", " "); }).join(", ");
    return chip(parts.join(" "), p.changes ? "red" : p.approvals ? "green" : "", title);
  }
  function myCard(p) {
    var key = p.repo + "/" + p.number;
    var top = p.to_answer ? chip(p.to_answer + " to answer", "you", "Threads where a reviewer spoke last")
      : p.ci && p.ci.state === "FAILURE" ? chip("CI failing", "red", (p.ci.failing || []).join(", "))
      : p.open_threads ? chip(p.open_threads + " open threads", "") : null;
    return '<a class="item' + (state.cur === key ? " on" : "") + '" href="#/' + key + '">' +
      '<span class="t" title="' + esc(p.title) + '">' + (p.session_live ? '<span class="live ' + (p.session_live.status === "busy" ? "working" : "idle") + '" title="Session open in ' + esc(p.session_live.tty) + '"></span>' : "") + esc(p.title) + "</span>" +
      '<span class="sub">' + esc(p.repo) + " #" + p.number + " · into " + esc(p.base || "") +
      (p.link ? ' · <span class="lk-tag">🔗 ' + esc(p.link.name) + "</span>" : "") + "</span>" +
      '<div class="chips">' + reviewersChip(p) + (top || "") + "</div></a>";
  }
  function renderMine() {
    var el = $("#side-list"), f = state.filter.toLowerCase(), fr = state.fRepo;
    var prs = (state.mine.prs || []).filter(function (p) {
      if (p.section === "closed") return false;
      if (fr && p.repo !== fr) return false;
      return !f || (p.title + " " + p.number + " " + p.repo).toLowerCase().indexOf(f) >= 0;
    });
    var h = "";
    MY_SECTIONS.forEach(function (sec) {
      var items = prs.filter(function (p) { return p.section === sec[0]; }).map(myCard);
      var open = f || fr || isOpen("my-" + sec[0], sec[0] !== "merged");
      h += secHtml("my-" + sec[0], sec[1], sec[2], items, open);
    });
    el.innerHTML = h;
    $("#f-author").hidden = true; $("#f-link").hidden = true;
    $("#f-repo").value = fr;
    $("#f-clear").hidden = !(fr || state.filter);
  }


  // ------------------------------------------------------------------ PR page
  function ciText(ci) {
    if (!ci || !ci.state) return chip("No CI", "");
    if (ci.state === "SUCCESS") return chip("CI green", "green");
    if (ci.state === "FAILURE" || ci.state === "ERROR") return chip("CI failing: " + esc((ci.failing || []).join(", ")), "red");
    return chip("CI running" + (ci.pending && ci.pending.length ? ": " + esc(ci.pending.join(", ")) : ""), "amber");
  }
  var MERGE_TEXT = { CLEAN: ["Ready to merge", "green"], BEHIND: ["Behind base", "amber"], BLOCKED: ["Blocked", "amber"],
    DIRTY: ["Has conflicts", "red"], UNSTABLE: ["Checks failing", "red"], DRAFT: ["Draft", ""], HAS_HOOKS: ["Ready (hooks)", "green"], UNKNOWN: ["Unknown", ""] };

  // ---------------- My PRs: Address comments
  function addrGroupState(root) {
    root.querySelectorAll("input[data-grp]").forEach(function (g) {
      var scope = g.dataset.grp === "file" ? g.closest(".ad-file") : g.closest(".ad-rev");
      var boxes = [].slice.call(scope.querySelectorAll("input[data-th]"));
      var on = boxes.filter(function (b) { return b.checked; }).length;
      g.checked = boxes.length > 0 && on === boxes.length;
      g.indeterminate = on > 0 && on < boxes.length;
    });
  }
  function addressHtml(S) {
    var opts = '<fieldset class="ck-g"><legend>Options</legend>' + S.options.map(function (o) {
      return '<label class="ck-i"><input type="checkbox" data-opt="' + o.key + '"' + (o.value ? " checked" : "") + (o.locked ? " disabled" : "") + "> " + esc(o.label) +
        (o.locked ? ' <span class="muted small">(always on)</span>' : "") + "</label>";
    }).join("") + "</fieldset>";
    var byRev = {};
    S.items.forEach(function (it) { (byRev[it.reviewer] = byRev[it.reviewer] || []).push(it); });
    var list = Object.keys(byRev).sort().map(function (rev) {
      var byFile = {};
      byRev[rev].forEach(function (it) { var f = it.path || "(review summary)"; (byFile[f] = byFile[f] || []).push(it); });
      return '<fieldset class="ck-g ad-rev"><legend><label><input type="checkbox" data-grp="rev"> ' + esc(rev) + " (" + byRev[rev].length + ")</label></legend>" +
        Object.keys(byFile).sort().map(function (f) {
          return '<div class="ad-file"><label class="small"><input type="checkbox" data-grp="file"> <span class="path">' + esc(f) + "</span></label>" +
            byFile[f].map(function (it) {
              var tags = (it.waiting ? chip("waiting for you", "you") : chip("you replied last", "")) + (it.outdated ? chip("outdated", "") : "") +
                (it.kind === "review" ? chip("review " + esc((it.state || "").toLowerCase().replace("_", " ")), "") : "") +
                (it.new_since_send ? chip("new since last send", "amber") : it.sent_at ? chip("sent " + ago(it.sent_at), "") : "");
              return '<div class="ad-it"><label><input type="checkbox" data-th="' + esc(it.id) + '"' + (it.ticked ? " checked" : "") + "> " +
                (it.line ? '<span class="mono">:' + esc(it.line) + "</span> " : "") + tags + "</label>" +
                '<div class="ad-txt">' + esc(it.text.replace(/\s+/g, " ").slice(0, 260)) + "</div>" +
                (it.last && it.last.by !== it.first_by ? '<div class="muted small">Last reply by <b>' + esc(it.last.by) + "</b>: " + esc(it.last.body.replace(/\s+/g, " ").slice(0, 160)) + "</div>" : "") +
                (it.url ? '<a class="small" href="' + esc(it.url) + '" target="_blank" rel="noopener">Open on GitHub</a>' : "") + "</div>";
            }).join("") + "</div>";
        }).join("") + "</fieldset>";
    }).join("");
    var ci = S.checks.length ? '<fieldset class="ck-g"><legend>Failing CI checks</legend>' + S.checks.map(function (c) {
      return '<label class="ck-i"><input type="checkbox" data-ck="' + esc(c.id) + '"' + (c.ticked ? " checked" : "") + "> " + esc(c.name) + "</label>";
    }).join("") + "</fieldset>" : "";
    return '<p class="small">Tick the comments the session should go through. Nothing is pushed or posted from here.' +
      (S.last_sent ? " Last sent " + ago(S.last_sent) + "." : "") + "</p>" + opts +
      '<div class="actions"><button type="button" class="btn btn-sm" data-ad-all="1">Tick all</button><button type="button" class="btn btn-sm" data-ad-all="0">Untick all</button>' +
      '<button type="button" class="btn btn-sm" data-ad-all="waiting">Only waiting for you</button></div>' +
      '<div class="ad-list">' + (list || '<p class="muted">No open comments from others.</p>') + ci + '</div><div id="ad-preview"></div>';
  }
  function readAddress() {
    var o = {}, th = [], ck = [];
    document.querySelectorAll("[data-opt]").forEach(function (b) { o[b.dataset.opt] = b.checked; });
    document.querySelectorAll("input[data-th]:checked").forEach(function (b) { th.push(b.dataset.th); });
    document.querySelectorAll("input[data-ck]:checked").forEach(function (b) { ck.push(b.dataset.ck); });
    return { options: o, threads: th, checks: ck };
  }
  function addressDialog() {
    var s = state.detail.summary, my = state.detail.mine, best = (my.sessions || [])[0];
    var sid = best ? best.session : "";
    dialog("Address comments on #" + s.number, '<p><span class="spin"></span> Loading comments…</p>', null);
    api("/api/address/" + s.repo + "/" + s.number).then(function (S) {
      var last = null, timer = null;
      var preview = function () {
        var out = $("#ad-preview");
        if (!out) return;
        out.innerHTML = '<p class="muted"><span class="spin"></span> Building the prompt…</p>';
        var b = readAddress();
        api("/api/action/address", Object.assign({ repo: s.repo, number: s.number, session: sid, dry_run: true }, b)).then(function (r) {
          last = r;
          var ok = $("#dlg-ok");
          if (r.ok === false && !r.session_open) { out.innerHTML = '<div class="err">' + esc(r.error) + "</div>"; ok.disabled = true; return; }
          ok.disabled = false;
          var head = r.session_open ? '<div class="note">The work session is open in <b>' + esc(r.tty) + "</b>. The button copies the prompt; paste it there.</div>"
            : "<label>Command (" + (r.how === "started" ? "starts a new session" : "resumes the session") + ")</label><pre>" + esc(r.tab_command) + "</pre>";
          ok.textContent = r.session_open ? "Copy prompt" : "Open tab and run";
          out.innerHTML = head + "<label>Prompt (exactly what is sent: " + r.threads + " comments, " + r.checks + " checks)</label><pre>" + esc(r.prompt) + "</pre>";
        }).catch(function (e) { out.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; });
      };
      dialog("Address comments on #" + s.number, addressHtml(S), "Open tab and run", function () {
        var b = readAddress();
        if (last && last.session_open) {
          if (navigator.clipboard) navigator.clipboard.writeText(last.prompt);
          api("/api/action/address", Object.assign({ repo: s.repo, number: s.number, session: sid, copied: true }, b)).then(function () { loadPR(state.cur, true); });
          info("Copied", "<p>The prompt is copied. Paste it in <b>" + esc(last.tty) + "</b>.</p>" +
            '<div class="actions"><button type="button" class="btn" data-my="focus" data-sid="' + esc(last.session) + '">Focus tab</button></div>');
          return;
        }
        api("/api/action/address", Object.assign({ repo: s.repo, number: s.number, session: sid }, b)).then(function (res) {
          if (res.ok) { info("Sent", "<p>Opened in <b>" + esc(res.tty) + "</b> (" + esc(res.how) + ").</p>"); loadPR(state.cur, true); loadList(); }
          else if (res.dry_run) info("Dry run", "<pre>" + esc(res.would_run) + "</pre>");
          else info("Failed", '<div class="err">' + esc(res.error || "unknown error") + "</div>");
        });
      });
      var root = $(".ad-list").parentNode;
      addrGroupState(root);
      root.addEventListener("change", function (ev) {
        var t = ev.target;
        if (t.dataset.grp === "rev") t.closest(".ad-rev").querySelectorAll("input[data-th]").forEach(function (b) { b.checked = t.checked; });
        if (t.dataset.grp === "file") t.closest(".ad-file").querySelectorAll("input[data-th]").forEach(function (b) { b.checked = t.checked; });
        addrGroupState(root);
        clearTimeout(timer); timer = setTimeout(preview, 300);
      });
      root.addEventListener("click", function (ev) {
        var t = ev.target.closest("[data-ad-all]");
        if (!t) return;
        ev.preventDefault();
        var by = {};
        S.items.forEach(function (it) { by[it.id] = it; });
        root.querySelectorAll("input[data-th]").forEach(function (b) {
          b.checked = t.dataset.adAll === "1" || (t.dataset.adAll === "waiting" && !!by[b.dataset.th] && by[b.dataset.th].waiting);
        });
        addrGroupState(root);
        clearTimeout(timer); timer = setTimeout(preview, 100);
      });
      preview();
    }).catch(function (e) { info("Failed", '<div class="err">' + esc(e.message) + "</div>"); });
  }

  function myNext(d) {
    var my = d.mine, hd = d.header, ci = hd.ci || {};
    var addr = function (primary) { return '<button class="btn' + (primary ? " btn-primary" : "") + '" data-address>Address comments</button>'; };
    var waitingOn = (my.reviewers || []).filter(function (r) { return r.state === "REQUESTED"; }).map(function (r) { return r.by; });
    if (hd.state === "MERGED") return { tone: "ok", text: "Merged. Nothing to do.", uses: [] };
    if (hd.state === "CLOSED") return { tone: "ok", text: "Closed without a merge.", uses: [] };
    if (my.to_answer) return { tone: "you", text: "Answer " + plural2(my.to_answer, "thread", "threads"), sub: "A reviewer spoke last in " + (my.to_answer === 1 ? "it" : "each") + ".", btns: addr(true), uses: ["address"] };
    if (my.section === "to_fix" || (my.changes && my.section !== "rereview")) return { tone: "you", text: "Fix the changes asked", sub: plural2(my.changes || 0, "reviewer asked", "reviewers asked") + " for changes, and you have not pushed since.", btns: addr(true), uses: ["address"] };
    if (ci.state === "FAILURE" || ci.state === "ERROR") return { tone: "bad", text: "CI is failing", sub: esc((ci.failing || []).join(", ")), btns: addr(true), uses: ["address"] };
    if (hd.draft) return { tone: "wait", text: "Still a draft", sub: "Mark it ready on GitHub when it is done.", uses: [] };
    if (my.section === "approved") return { tone: "ok", text: hd.merge_state === "CLEAN" ? "Approved and ready to merge" : "Approved", sub: "Merge state: " + esc((MERGE_TEXT[hd.merge_state] || [hd.merge_state || "unknown"])[0]) + ".", uses: [] };
    if (my.section === "rereview") return { tone: "wait", text: "Waiting for re-review", sub: "You pushed after the change request." + (waitingOn.length ? " Asked: " + esc(waitingOn.join(", ")) + "." : ""), uses: [] };
    if (waitingOn.length) return { tone: "wait", text: "Waiting on " + esc(waitingOn.join(", ")), sub: "No decision yet.", uses: [] };
    return { tone: "wait", text: "No reviewer yet", sub: "Ask someone on GitHub.", uses: [] };
  }
  function renderMyPR() {
    var d = state.detail, s = d.summary, hd = d.header, my = d.mine;
    var gh = hd.url || "https://github.com/" + CFG.org + "/" + s.repo + "/pull/" + s.number;
    state.tab = TAB_ALIAS[state.tab] || state.tab;
    var stage = (MY_SECTIONS.filter(function (x) { return x[0] === my.section; })[0] || ["", my.section])[1];
    var n = myNext(d);
    n.label = "Next step · " + esc(stage || "");
    var kpis = kpi("Approvals", my.approvals ? '<span class="ok">' + my.approvals + "</span>" : "0") +
      kpi("Changes asked", my.changes ? '<span class="bad">' + my.changes + "</span>" : "0") +
      kpi("Waiting on", my.pending ? plural2(my.pending, "reviewer", "reviewers") : "—") +
      kpi("To answer", my.to_answer ? '<span class="warn">' + my.to_answer + '</span> <span class="faint">of ' + my.open_threads + " open</span>" : (my.open_threads ? my.open_threads + ' open <span class="faint">· none on you</span>' : "None"));
    var h = prHeader(d, "Your PR") + nowPanel(n, kpis, []);
    var ss = my.sessions || [], best = ss[0];
    var sec = [];
    if (best && best.live) sec.push('<button class="btn btn-sm" data-my="focus" data-sid="' + esc(best.session) + '">Focus tab (' + esc(best.tty) + ")</button>");
    else if (best) sec.push('<button class="btn btn-sm" data-my="resume" data-sid="' + esc(best.session) + '">Resume session</button>');
    if (n.uses.indexOf("address") < 0) sec.push('<button class="btn btn-sm" data-address>Address comments</button>');
    sec.push('<button class="btn btn-sm" data-my="start">Start new session</button>');
    sec.push('<button class="btn btn-sm" data-act="refresh-one" title="Key r">Refresh</button>');
    sec.push('<a class="btn btn-sm" href="' + esc(gh) + '" target="_blank" rel="noopener">GitHub ↗</a><a class="btn btn-sm" href="' + esc(gh) + '/files" target="_blank" rel="noopener">Diff ↗</a>');
    var sessTxt = best && best.live ? (best.status === "busy" ? '<span class="ok">Working</span>' : "Open") + " · " + esc(best.tty) : best ? "Closed" : "None";
    h += '<div class="bar2">' + fact("Session", sessTxt) + fact("CI", ciFact(hd.ci)) + fact("Merge", mergeFact(hd)) + '<span class="acts">' + sec.join("") + "</span></div>";
    if (my.last_address) {
      var la = my.last_address;
      h += '<div class="note small">Sent to session ' + when(la.at) + " (" + la.threads.length + " comment" + (la.threads.length === 1 ? "" : "s") +
        (la.checks && la.checks.length ? ", " + la.checks.length + " CI check" + (la.checks.length === 1 ? "" : "s") : "") + ", " + esc(la.how) + (la.tty ? " in " + esc(la.tty) : "") + ").</div>";
    }
    var dc = s.drafts;
    h += tabsHtml([["conversation", "Overview"], ["drafts", "Drafts", dc ? dc.pending + " pending" : null, dc && dc.pending],
      ["comments", "Comments", d.reviews.reduce(function (a, r) { return a + r.threads.length; }, 0)], ["timeline", "Timeline"]]);
    h += '<div id="tab-body"></div>';
    $("#main").innerHTML = h;
    renderTab();
  }
  function mySessionsBox(my) {
    var ss = my.sessions || [];
    var row = function (x) {
      var st = x.live ? chip('<span class="dot' + (x.status === "busy" ? " pulse" : "") + '"></span>' + (x.status === "busy" ? "Working" : "Open") + " · " + esc(x.tty), x.status === "busy" ? "green" : "blue") : chip("Closed", "");
      var btn = x.live ? '<button class="btn btn-sm" data-my="focus" data-sid="' + esc(x.session) + '">Focus tab</button>'
        : '<button class="btn btn-sm" data-my="resume" data-sid="' + esc(x.session) + '">Resume</button>';
      var pick = x.chosen ? chip("This PR's session", "blue") : '<button class="btn btn-sm" data-my="pick" data-sid="' + esc(x.session) + '">Use this one</button>';
      return "<tr><td>" + st + '</td><td class="mono">' + esc(x.session.slice(0, 8)) + '</td><td class="muted small">' + esc((x.cwd || "").split("/").slice(-1)[0]) +
        "</td><td class=\"muted small\">" + esc(x.why || "") + (x.mtime ? " · " + ago(x.mtime) : "") + "</td><td>" + btn + " " + pick + "</td></tr>";
    };
    var rows = ss.slice(0, 3).map(row).join("");
    if (ss.length > 3) rows += '<tr><td colspan="5"><details><summary class="muted small" style="cursor:pointer">' + (ss.length - 3) +
      ' more</summary><table class="t rv">' + ss.slice(3).map(row).join("") + "</table></details></td></tr>";
    return '<div class="box"><div class="box-h"><b>Work sessions</b> <span class="muted small">Claude sessions that worked on this PR (newest transcripts that name its branch or link)</span></div><div class="box-b">' +
      (rows ? '<table class="t rv">' + rows + "</table>" : '<span class="muted">None found. Start a new session to work on it.</span>') + "</div></div>";
  }
  function tabMyOverview(d) {
    var my = d.mine, main = "", side = "";
    main += '<div class="box' + (my.to_answer ? " needs" : "") + '"><div class="box-h"><b>Threads waiting for your answer</b>' + (my.to_answer ? '<span class="chip you">' + my.to_answer + "</span>" : "") + '</div><div class="box-b">' +
      (my.to_answer_list.length ? my.to_answer_list.map(function (t) {
        return '<div class="cmt flat"><span class="who">' + esc(t.by) + '</span> on <a href="' + esc(t.url) + '" target="_blank" rel="noopener" class="path">' + esc(t.where) + "</a> · " + when(t.at) +
          '<div class="md">' + MD.render(t.body) + "</div></div>";
      }).join("") : '<span class="muted">None. ' + (my.open_threads ? my.open_threads + " threads are open, and you spoke last in each." : "") + "</span>") + "</div></div>";
    main += mySessionsBox(my);
    side += '<div class="box"><div class="box-h"><b>Reviewers</b></div><div class="box-b">' +
      (my.reviewers.length ? '<table class="t rv">' + my.reviewers.map(function (r) {
        var cls = { APPROVED: "green", CHANGES_REQUESTED: "red", REQUESTED: "amber", COMMENTED: "", DISMISSED: "" }[r.state] || "";
        var label = { APPROVED: "Approved", CHANGES_REQUESTED: "Changes asked", REQUESTED: "Waiting", COMMENTED: "Commented", DISMISSED: "Dismissed" }[r.state] || r.state;
        return "<tr><td><b>" + esc(r.by) + "</b>" + (r.at ? '<div class="muted small">' + (r.url ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + ago(r.at) + "</a>" : ago(r.at)) + "</div>" : "") +
          '</td><td class="acts">' + chip(label, cls) + (r.asked_again ? " " + chip("Asked again", "amber") : "") + "</td></tr>";
      }).join("") + "</table>" : '<span class="muted">No reviewer yet. Ask someone on GitHub.</span>') + "</div></div>";
    var ci = d.header.ci || {};
    side += '<div class="box"><div class="box-h"><b>Checks</b>' + (ci.state === "SUCCESS" ? chip("All green", "green") : !ci.state ? chip("No checks", "") : "") + "</div>" +
      (ci.failing && ci.failing.length || ci.pending && ci.pending.length ? '<div class="box-b">' +
      (ci.failing && ci.failing.length ? '<b class="small bad">Failing</b><ul class="list-small">' + ci.failing.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : "") +
      (ci.pending && ci.pending.length ? '<b class="small warn">Running</b><ul class="list-small">' + ci.pending.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : "") + "</div>" : "") + "</div>";
    side += linksHtml(d) + descBox(d);
    return '<div class="ov"><div class="ov-main">' + main + '</div><aside class="ov-side">' + side + "</aside></div>";
  }
  // ---------------- PR page building blocks
  function kpi(k, v, title) { return '<div class="kpi"' + (title ? ' title="' + esc(title) + '"' : "") + '><div class="k">' + k + '</div><div class="v">' + v + "</div></div>"; }
  function fact(k, v, title) { return '<span class="fact"' + (title ? ' title="' + esc(title) + '"' : "") + '><span class="k">' + k + "</span> <b>" + v + "</b></span>"; }
  function plural2(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function ciFact(ci) {
    ci = ci || {};
    if (ci.state === "SUCCESS") return '<span class="ok">Green</span>';
    if (ci.state === "FAILURE" || ci.state === "ERROR") return '<span class="bad" title="' + esc((ci.failing || []).join(", ")) + '">Failing (' + (ci.failing || []).length + ")</span>";
    return ci.state ? '<span class="warn">Running</span>' : "—";
  }
  function mergeFact(hd) {
    var ms = MERGE_TEXT[hd.merge_state] || [hd.merge_state || "—", ""];
    var cls = { green: "ok", red: "bad", amber: "warn" }[ms[1]] || "";
    return '<span class="' + cls + '">' + esc(ms[0]) + "</span>";
  }
  function prHeader(d, who) {
    var s = d.summary, hd = d.header;
    var st = hd.draft ? "DRAFT" : (hd.state || "OPEN");
    var nums = s.previous_numbers && s.previous_numbers.length ? ' <span class="faint small">(was #' + s.previous_numbers.join(", #") + ")</span>" : "";
    return '<div class="pr-h"><h1>' + esc(hd.title || s.title) + ' <span class="num">#' + s.number + "</span>" + nums + "</h1>" +
      '<div class="meta"><span class="state ' + st + '">' + esc(st[0] + st.slice(1).toLowerCase()) + "</span>" +
      "<span>" + who + " into " + '<span class="branch">' + esc(hd.base || "?") + '</span> from <span class="branch">' + esc(hd.branch || "?") + "</span></span>" +
      '<span class="mono" title="Head commit">' + esc((hd.head || "").slice(0, 8)) + "</span>" +
      (hd.additions != null ? '<span><span class="add">+' + hd.additions + '</span> <span class="del">−' + hd.deletions + "</span> in " + hd.files + " files</span>" : "") +
      '<span class="faint">' + esc(s.repo) + "</span></div></div>";
  }
  function nowPanel(n, kpis, warns) {
    return '<section class="now t-' + n.tone + '" aria-label="What needs you now"><div><div class="now-k">' + (n.label || "Next step") + '</div><div class="now-text">' + n.text + "</div>" +
      (n.sub ? '<div class="now-sub">' + n.sub + "</div>" : "") + '</div><div class="now-btns">' + (n.btns || "") + "</div>" +
      (warns && warns.length ? '<div class="now-warn">' + warns.map(function (w) { return "<div>" + w + "</div>"; }).join("") + "</div>" : "") +
      '<div class="kpis">' + kpis + "</div></section>";
  }
  function tabsHtml(tabs) {
    if (!tabs.some(function (t) { return t[0] === state.tab; })) state.tab = "conversation";
    return '<div class="tabs" role="tablist">' + tabs.map(function (t, i) {
      return '<button data-tab="' + t[0] + '" role="tab" title="Key ' + (i + 1) + '" class="' + (state.tab === t[0] ? "on" : "") + '">' + t[1] +
        (t[2] != null && t[2] !== 0 ? '<span class="count' + (t[3] ? " you" : "") + '">' + t[2] + "</span>" : "") + "</button>";
    }).join("") + '<span class="kb-hint"><kbd>1</kbd>–<kbd>' + tabs.length + "</kbd> tabs · <kbd>j</kbd>/<kbd>k</kbd> PRs</span></div>";
  }
  var TAB_ALIAS = { gates: "report", description: "conversation", files: "conversation" };
  function latestFindings(d) {
    var f = (d.findings || [])[(d.findings || []).length - 1];
    if (!f) return null;
    var all = [];
    f.sections.forEach(function (x) { x.items.forEach(function (it) { all.push(it); }); });
    return { open: all.filter(function (it) { return !itemDone(it); }).length, total: all.length, round: f.round };
  }
  // The one next step for a PR we review: what it is, why, and the button for it.
  function reviewNext(d) {
    var s = d.summary, hd = d.header, sess = s.session || {}, tty = esc(sess.tty || "");
    var hasSess = !!(d.entry.sessions && d.entry.sessions.length);
    var rrBtn = function (primary) {
      return '<button class="btn' + (primary ? " btn-primary" : "") + '" data-act="' + (d.tracked ? "rereview" : "new") + '">' + (d.tracked && hasSess ? "Re-review" : "Start review") + "</button>";
    };
    var focusBtn = function (primary) { return sess.tty ? '<button class="btn' + (primary ? " btn-primary" : "") + '" data-act="focus">Focus tab (' + tty + ")</button>" : ""; };
    var key = s.repo + "/" + s.number;
    var n = null;
    if (hd.state === "MERGED" || hd.state === "CLOSED") {
      n = { tone: "ok", text: hd.state === "MERGED" ? "Merged. Nothing to do." : "Closed without a merge.", sub: d.tracked ? "You can take it off the list. Nothing changes on GitHub." : "",
        btns: d.tracked ? '<button class="btn btn-primary" data-remove="' + key + '">Remove from list</button>' : "", uses: [] };
    } else if (sess.waiting_for_you && sess.tty) {
      n = { tone: "you", text: "The session asks you something", sub: "Its last message in " + tty + " ends with a question.", btns: focusBtn(true), uses: ["focus"] };
    } else if (s.unposted && s.drafts && s.drafts.posted) {
      n = { tone: "ok", text: "The session posted the drafts", sub: "See the Drafts tab for the link. No review of ours shows at the latest round yet, so the list keeps it under To post.",
        btns: '<button class="btn btn-primary" data-tab="drafts">Open drafts</button>', uses: [] };
    } else if (s.unposted && s.drafts && s.drafts.sent) {
      n = { tone: "you", text: "Confirm in the session tab", sub: "The approved drafts went to the session. It posts after you say yes there.",
        btns: sess.tty ? focusBtn(true) : '<button class="btn btn-primary" data-tab="drafts">Open drafts</button>', uses: sess.tty ? ["focus"] : [] };
    } else if (s.unposted && s.drafts && s.drafts.pending) {
      n = { tone: "you", text: "Approve " + plural2(s.drafts.pending, "draft comment", "draft comments"),
        sub: "The review is finished but not on GitHub. " + s.drafts.approved + " approved, " + s.drafts.rejected + " rejected so far.",
        btns: '<button class="btn btn-primary" data-tab="drafts">Open drafts</button>', uses: [] };
    } else if (s.unposted && s.drafts) {
      n = { tone: "you", text: "Send the approved drafts to the session", sub: "All drafts are decided (" + s.drafts.approved + " approved, " + s.drafts.rejected + " rejected). The session posts after you confirm there.",
        btns: '<button class="btn btn-primary" data-tab="drafts">Open drafts</button>', uses: [] };
    } else if (s.unposted) {
      n = { tone: "you", text: "Post the review", sub: "Not on GitHub yet: " + esc(s.unposted) + ". Post it from its session.",
        btns: sess.tty ? focusBtn(true) : '<button class="btn btn-primary" data-tab="drafts">Open drafts</button>', uses: sess.tty ? ["focus"] : [] };
    } else if (sess.state === "working") {
      n = { tone: "wait", text: "The session is working", sub: "Nothing to do until it finishes.", btns: focusBtn(true), uses: ["focus"] };
    } else if (!d.tracked || !s.round) {
      n = { tone: "you", text: s.requested_me ? "Review asked: start the review" : "Not reviewed yet", sub: "The checklist and the brief show before anything runs.", btns: rrBtn(true), uses: ["rereview"] };
    } else {
      var nw = [];
      if (s.new_replies) nw.push(plural2(s.new_replies, "new reply", "new replies"));
      if (s.new_commits) nw.push(plural2(s.new_commits, "new commit", "new commits"));
      if (s.requested_me) nw.push("a new review request");
      if (nw.length) n = { tone: "you", text: "Re-review: " + nw.join(", "), sub: "Since our last look " + (d.since ? when(d.since) : "") + ".", btns: rrBtn(true), uses: ["rereview"] };
      else if (s.box && /^(db|crap|mutate|cmut|running|starting)$/.test(s.box.phase))
        n = { tone: "wait", text: esc(BOX_TEXT[s.box.phase]), sub: "Gates run on the box. Refresh to check again.", btns: "", uses: [] };
      else if (s.verdict === "approved" || s.verdict === "approve_with_nits")
        n = { tone: "ok", text: "Approved. Nothing new since.", sub: "Waiting for the merge.", btns: "", uses: [] };
      else n = { tone: "wait", text: "Waiting on " + esc(s.waiting_on || d.entry.waiting_on || "the author"), sub: "No new commits or replies since our last look.", btns: "", uses: [] };
    }
    n.rrBtn = rrBtn; n.focusBtn = focusBtn;
    return n;
  }
  function renderPR() {
    if (state.detail.mine) return renderMyPR();
    var d = state.detail, s = d.summary, hd = d.header, sess = s.session || {};
    var gh = hd.url || "https://github.com/" + CFG.org + "/" + s.repo + "/pull/" + s.number;
    state.tab = TAB_ALIAS[state.tab] || state.tab;
    var n = reviewNext(d), lf = latestFindings(d);
    var act = [];
    if (s.new_commits) act.push("+" + s.new_commits + " commits");
    if (s.new_replies) act.push(s.new_replies + " replies");
    var kpis = kpi("Verdict", '<span class="v-' + esc(s.verdict) + '">' + esc(s.verdict_label || "—") + "</span>" + (s.round ? ' <span class="faint">· R' + s.round + "</span>" : "")) +
      kpi("Open findings", lf ? '<span class="' + (lf.open ? "warn" : "ok") + '">' + lf.open + '</span> <span class="faint">of ' + lf.total + "</span>" : "—", lf ? "Latest findings list (round " + lf.round + ")" : "") +
      kpi("Waiting on", esc(s.waiting_on || d.entry.waiting_on || "—")) +
      kpi("New since our look", act.length ? '<span class="warn">' + act.join(", ") + "</span>" : "None");
    var warns = [];
    {
      if (d.tracked && (s.verdict === "approved" || s.verdict === "approve_with_nits") && hd.state === "OPEN")
        warns.push("We already approved this PR. Re-review only if something changed; a new round can change the verdict.");
      if (d.tracked && sess.tty && d.entry.sessions && d.entry.sessions.length)
        warns.push("The review session is open in " + esc(sess.tty) + ". Re-review gives you the prompt to paste there; it does not open a second copy.");
    }
    var h = prHeader(d, "<b>" + esc(hd.author || "") + "</b> wants to merge " + (hd.commits || "?") + " commits") + nowPanel(n, kpis, warns);
    if (s.error) h += '<div class="err">' + esc(s.error) + "</div>";
    var sessTxt = sess.state === "working" ? '<span class="ok">Working</span> · ' + esc(sess.tty) : sess.state === "idle" ? "Idle · " + esc(sess.tty) : sess.state === "closed" ? "Closed" : "None";
    var box = s.box && BOX_TEXT[s.box.phase] ? esc(BOX_TEXT[s.box.phase].replace(/^Box: /, "")) : "—";
    var sec = [];
    if (n.uses.indexOf("rereview") < 0 && hd.state !== "MERGED" && hd.state !== "CLOSED") sec.push(n.rrBtn(false));
    if (n.uses.indexOf("focus") < 0) sec.push('<button class="btn btn-sm" data-act="focus"' + (sess.tty ? "" : " disabled") + ">Focus tab" + (sess.tty ? " (" + esc(sess.tty) + ")" : "") + "</button>");
    sec.push('<button class="btn btn-sm" data-act="refresh-one" title="Key r">Refresh</button>');
    if (d.tracked && (hd.state === "MERGED" || hd.state === "CLOSED") && n.btns.indexOf("data-remove") < 0) sec.push('<button class="btn btn-sm" data-remove="' + s.repo + "/" + s.number + '">Remove from list</button>');
    sec.push('<a class="btn btn-sm" href="' + esc(gh) + '" target="_blank" rel="noopener">GitHub ↗</a><a class="btn btn-sm" href="' + esc(gh) + '/files" target="_blank" rel="noopener">Diff ↗</a>');
    h += '<div class="bar2">' + fact("Session", sessTxt) + fact("Box", box, s.box ? s.box.line : "") + fact("CI", ciFact(hd.ci)) + fact("Merge", mergeFact(hd)) +
      '<span class="acts">' + sec.map(function (b) { return b.replace('class="btn"', 'class="btn btn-sm"'); }).join("") + "</span></div>";
    var dc = s.drafts;
    h += tabsHtml([["conversation", "Overview"], ["drafts", "Drafts", dc ? dc.pending + " pending" : null, dc && dc.pending],
      ["comments", "Comments", d.reviews.reduce(function (a, r) { return a + r.threads.length; }, 0)], ["timeline", "Timeline"], ["report", "Report & gates", d.rounds.length || null]]);
    h += '<div id="tab-body"></div>';
    $("#main").innerHTML = h;
    renderTab();
  }

  function renderTab() {
    var d = state.detail, el = $("#tab-body");
    var fn = { conversation: d.mine ? tabMyOverview : tabOverview, drafts: tabDrafts, timeline: tabTimeline, comments: tabComments, report: tabReport }[state.tab] || (d.mine ? tabMyOverview : tabOverview);
    el.innerHTML = fn(d);
    if (state.tab === "report") wireReport();
    if (state.tab === "drafts") wireDrafts();
  }

  // The PR body from GitHub, folded (GitHub has the full view).
  function descBox(d) {
    var hd = d.header, body = (hd.body || "").trim();
    return '<details class="box fold"><summary class="box-h"><b>Description</b> <span class="muted small">' + esc(hd.author || "") + (hd.created ? " · opened " + ago(hd.created) : "") + "</span>" +
      (hd.url ? '<a class="small" style="margin-left:auto;white-space:nowrap" href="' + esc(hd.url) + '" target="_blank" rel="noopener" title="Open on GitHub">GitHub ↗</a>' : "") + "</summary>" +
      '<div class="box-b md desc-body">' + (body ? MD.render(body) : '<p class="muted">No description.</p>') + "</div></details>";
  }
  function tabOverview(d) {
    var s = d.summary, e = d.entry, main = "", side = "";
    var needs = s.needs || [];
    if (needs.length) main += '<div class="box needs"><div class="box-h"><b>Needs you</b></div><div class="box-b"><ul class="need-list">' +
      needs.map(function (n) { return '<li class="' + (n.kind === "you" ? "" : n.kind === "warn" ? "warn" : "ok") + '">' + esc(n.text) + "</li>"; }).join("") + "</ul></div></div>";
    main += findingsBoxes(d);
    if (d.replies.length) main += '<div class="box"><div class="box-h"><b>New replies since our last look</b><span class="chip amber">' + d.replies.length + '</span></div><div class="box-b">' +
      d.replies.map(function (r) {
        return '<div class="cmt flat"><span class="who">' + esc(r.by) + "</span>" + (r.is_author ? " " + chip("author", "blue") : "") +
          ' on <a href="' + esc(r.url) + '" target="_blank" rel="noopener" class="path">' + esc(r.where) + "</a> · " + when(r.at) +
          '<div class="md">' + MD.render(r.body) + "</div></div>";
      }).join("") + "</div></div>";
    if (d.new_commits.length) main += '<div class="box"><div class="box-h"><b>New commits since our review</b><span class="chip amber">' + d.new_commits.length + '</span></div><div class="box-b"><ul class="list-small">' +
      d.new_commits.map(function (c) { return '<li><a href="' + esc(c.url) + '" target="_blank" rel="noopener" class="mono">' + esc(c.sha) + "</a> " + esc(c.text) + " · " + when(c.at) + "</li>"; }).join("") + "</ul></div></div>";
    if (!main) main = '<div class="box"><div class="box-b muted">No findings yet, and nothing new since our last look.</div></div>';
    side += '<div class="box"><div class="box-h"><b>Review state</b></div><div class="box-b"><dl class="kv">' +
      (e.verdict_note ? "<dt>Verdict note</dt><dd>" + esc(e.verdict_note) + "</dd>" : "") +
      "<dt>Head reviewed</dt><dd class=\"mono\">" + esc(s.head_reviewed || "—") + (s.head_now && s.head_reviewed && s.head_now !== s.head_reviewed ? " → now " + esc(s.head_now) : "") +
      (s.head_found === false ? ' <span class="chip red">not in PR any more</span>' : "") + "</dd>" +
      "<dt>Posted</dt><dd>" + esc(e.posted_note || (e.posted && e.posted.length ? e.posted.length + " review(s)" : "—")) + "</dd>" +
      "<dt>Last look</dt><dd>" + (d.since ? when(d.since) : "—") + "</dd>" +
      (e.notes ? "<dt>Notes</dt><dd>" + esc(e.notes) + "</dd>" : "") + "</dl></div></div>";
    var sess = s.session || {};
    side += '<div class="box"><div class="box-h"><b>Session</b>' + sessionChip(sess) + '</div><div class="box-b"><dl class="kv">' +
      "<dt>Id</dt><dd class=\"mono\">" + esc(sess.session || "—") + "</dd>" +
      "<dt>Tab</dt><dd>" + esc(sess.tty || (e.tab ? e.tab + " (last known, not open)" : "—")) + (sess.tab_title ? " · " + esc(sess.tab_title) : "") + "</dd>" +
      "<dt>Box</dt><dd>" + (s.box ? '<span class="muted small mono">' + esc(s.box.line || "") + "</span>" : "Not checked (Refresh checks it)") + "</dd>" +
      "</dl></div></div>";
    side += linksHtml(d) + descBox(d);
    return '<div class="ov"><div class="ov-main">' + main + '</div><aside class="ov-side">' + side + "</aside></div>";
  }
  // Findings with their state: the latest recheck table in the report, and our GitHub thread.
  var DONE_STATUS = { fixed: 1, answered: 1, kept: 1, dropped: 1 };
  var STATUS_CLS = { fixed: "green", answered: "green", kept: "", dropped: "", open: "red", partial: "amber" };
  function itemDone(it) { return (it.thread && it.thread.resolved) || (it.report_status && DONE_STATUS[it.report_status.status]); }
  function findingRow(it) {
    var rs = it.report_status, th = it.thread;
    var rsChip = rs ? chip(esc(rs.label.slice(0, 40)), STATUS_CLS[rs.status] || "", rs.label + " — " + (rs.section ? rs.section + ": " : "") + rs.note) : '<span class="muted">—</span>';
    var thChip = th
      ? '<a href="' + esc(th.url) + '" target="_blank" rel="noopener">' + chip(th.resolved ? "Resolved" : "Open", th.resolved ? "green" : "amber", th.where) + "</a>" +
        (th.others_last && !th.resolved ? '<div class="muted small">' + esc(th.last_by) + " replied</div>" : "")
      : '<span class="muted" title="No thread of ours on this file and line">—</span>';
    return '<tr class="' + (itemDone(it) ? "f-done" : "f-open") + '"><td class="mono">' + esc(it.id || "") + "</td><td>" + MD.inline(it.title) +
      (it.refs && it.refs.length ? '<span class="f-ref" title="' + esc(it.refs.join(", ")) + '">' + esc(it.refs[0].split("/").slice(-2).join("/")) +
        (it.refs.length > 1 ? " +" + (it.refs.length - 1) : "") + "</span>" : "") +
      "</td><td>" + rsChip + "</td><td>" + thChip + "</td></tr>";
  }
  function findingsBoxes(d) {
    var list = (d.findings || []).slice().reverse();
    if (!list.length) return "";
    return list.map(function (f, i) {
      var t = f.tally, all = [];
      f.sections.forEach(function (s) { s.items.forEach(function (it) { all.push(it); }); });
      var openN = all.filter(function (it) { return !itemDone(it); }).length;
      var head = "<b>Findings</b> · round " + f.round + ", PR #" + f.pr + " " +
        chip(openN + " of " + t.items + " still open", openN ? "amber" : "green") +
        (t.gh_open + t.gh_resolved ? chip("GitHub: " + t.gh_resolved + " resolved, " + t.gh_open + " open", "") : chip("Not on GitHub", "")) +
        (t.fixed + t.open ? chip("Recheck: " + t.fixed + " done, " + t.open + " open", "") : "");
      var body = '<label class="small muted f-toggle"><input type="checkbox" data-only-open> Show only open</label><table class="t findings"><colgroup><col class="c-id"><col><col class="c-rc"><col class="c-gh"></colgroup>' +
        '<tr><th>Id</th><th>Finding</th><th title="Status in the latest recheck table of the report">Recheck</th><th title="Our GitHub thread on this file and line">GitHub</th></tr>' +
        f.sections.filter(function (s) { return s.items.length; }).map(function (s) {
          return '<tr class="f-sev"><td colspan="4">' + esc(s.severity) + " (" + s.items.length + ")</td></tr>" + s.items.map(findingRow).join("");
        }).join("") + "</table>";
      return '<details class="box fbox"' + (i === 0 ? " open" : "") + '><summary class="box-h">' + head + '</summary><div class="box-b">' + body + "</div></details>";
    }).join("");
  }
  document.addEventListener("change", function (ev) {
    if (ev.target.matches("[data-only-open]")) ev.target.closest(".box-b").classList.toggle("only-open", ev.target.checked);
  });

  function evHtml(e) {
    var who = "<b>" + esc(e.by || "") + "</b>";
    var link = function (t) { return e.url ? '<a href="' + esc(e.url) + '" target="_blank" rel="noopener">' + t + "</a>" : t; };
    var b = "";
    switch (e.kind) {
      case "our_review": b = who + " " + link("posted our review") + " (" + esc((e.state || "").toLowerCase().replace("_", " ")) + ", " + e.comments + " inline comments) at <span class=\"mono\">" + esc(e.sha) + "</span>";
        if (e.body) b += '<div class="body md">' + MD.render(e.body) + "</div>"; break;
      case "review": b = who + " " + link("reviewed") + " (" + esc((e.state || "").toLowerCase().replace("_", " ")) + ", " + e.comments + " comments)";
        if (e.body) b += '<div class="body md">' + MD.render(e.body) + "</div>"; break;
      case "commit": b = who + " pushed " + link('<span class="mono">' + esc(e.sha) + "</span>") + " " + esc(e.text || ""); break;
      case "comment": b = who + " " + link("commented") + '<div class="body md">' + MD.render(e.text) + "</div>"; break;
      case "reply": b = who + " " + link("replied") + ' on <span class="path">' + esc(e.where) + '</span><div class="body md">' + MD.render(e.text) + "</div>"; break;
      case "round": b = "<b>Round " + e.n + "</b> (" + esc(e.round_kind) + ") recorded at <span class=\"mono\">" + esc(e.sha) + "</span>: " + esc(e.verdict || "") + (e.approx ? ' <span class="muted small">(date only)</span>' : "") +
        (e.passes && e.passes.length ? '<div class="muted small">Ran: ' + e.passes.map(esc).join(", ") + "</div>" : ""); break;
    }
    return '<div class="ev ' + e.kind + '">' + b + ' <span class="when">· ' + when(e.t) + "</span></div>";
  }
  // Timeline: one fold per checkpoint (each review of ours). The newest is open; the open state
  // is kept while the page stays open.
  var tlOpen = {};
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function checkpoint(g) {
    var c = { commit: 0, reply: 0, comment: 0, review: 0, round: 0 };
    g.events.forEach(function (e) { if (c[e.kind] != null) c[e.kind]++; });
    var after = [];
    if (c.commit) after.push(plural(c.commit, "commit", "commits"));
    if (c.reply + c.comment) after.push(plural(c.reply + c.comment, "reply", "replies"));
    if (c.review) after.push(plural(c.review, "other review", "other reviews"));
    if (c.round) after.push(plural(c.round, "round", "rounds"));
    var r = g.events[0] && (g.events[0].kind === "our_review" || (g.events[0].kind === "review" && /^Review \d+ by/.test(g.title))) ? g.events[0] : null;
    var head = r
      ? '<span class="cp-dot ours"></span><b>' + esc(g.title) + "</b> · " + esc((r.state || "").toLowerCase().replace("_", " ")) +
        ' · <span class="mono">' + esc(r.sha) + "</span> · " + plural(r.comments, "inline comment", "inline comments") + " · " + when(r.t)
      : '<span class="cp-dot"></span><b>' + esc(g.title) + "</b>";
    return head + '<span class="cp-after">' + (after.length ? (r ? "After it: " : "") + after.join(" · ") : (r ? "Nothing after it yet" : "")) + "</span>";
  }
  function timelineHtml(groups, key) {
    return '<div class="tl">' + groups.map(function (g, i) {
      var k = key + "|" + i;
      var open = tlOpen[k] != null ? tlOpen[k] : i === groups.length - 1;
      return '<details class="tl-group" data-tl="' + esc(k) + '"' + (open ? " open" : "") + "><summary>" + checkpoint(g) + "</summary>" +
        '<div class="tl-body">' + g.events.map(evHtml).join("") + "</div></details>";
    }).join("") + "</div>";
  }
  function tabTimeline(d) {
    var base = d.summary.repo + "/" + d.summary.number;
    var h = '<div class="actions"><button class="btn btn-sm" data-tl-all="1">Expand all</button><button class="btn btn-sm" data-tl-all="0">Collapse all</button></div>';
    h += d.timeline.length ? timelineHtml(d.timeline, base) : '<p class="muted">No events.</p>';
    d.older.forEach(function (o) {
      h += '<h3 style="margin-top:28px">Earlier PR <a href="' + esc(o.url || "") + '" target="_blank" rel="noopener">#' + o.number + "</a> " + esc(o.title || "") + " " + chip(esc(o.state || ""), "") + "</h3>" + timelineHtml(o.timeline, base + "#" + o.number);
    });
    return h;
  }
  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (d.matches && d.matches("details[data-tl]")) tlOpen[d.dataset.tl] = d.open;
  }, true);
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-tl-all]");
    if (!t) return;
    var open = t.dataset.tlAll === "1";
    document.querySelectorAll("details[data-tl]").forEach(function (d) { d.open = open; tlOpen[d.dataset.tl] = open; });
  });

  function tabComments(d) {
    if (!d.reviews.length) return '<p class="muted">No reviews with comments yet.</p>';
    return d.reviews.map(function (r) {
      var threads = r.threads.slice().sort(function (a, b) { return (a.resolved - b.resolved) || (a.path || "").localeCompare(b.path || "") || (a.line - b.line); });
      return '<div class="box"><div class="box-h"><b>' + esc(r.by) + "</b>" + (r.ours ? chip("ours", "blue") : "") + " " +
        '<a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc((r.state || "").toLowerCase().replace("_", " ")) + "</a> · " + when(r.at) +
        ' · <span class="mono">' + esc(r.sha) + "</span> · " + r.threads.length + " threads: " + chip(r.open + " open", r.open ? "amber" : "") + chip(r.resolved + " resolved", "green") + "</div>" +
        (r.body ? '<details class="thread"><summary>Review summary</summary><div class="cmt md">' + MD.render(r.body) + "</div></details>" : "") +
        threads.map(function (t) {
          var last = t.comments[t.comments.length - 1];
          return '<details class="thread"><summary>' + chip(t.resolved ? "Resolved" : "Open", t.resolved ? "green" : "amber") + (t.outdated ? chip("Outdated", "") : "") +
            '<span class="path">' + esc(t.path) + ":" + esc(t.line == null ? "?" : t.line) + "</span>" +
            '<span class="muted small">' + t.comments.length + " comment" + (t.comments.length > 1 ? "s" : "") + " · last by <b>" + esc(t.last_by) + "</b> " + when(t.last_at) + "</span>" +
            '<span class="muted small" style="flex-basis:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc((t.comments[0].body || "").replace(/\s+/g, " ").slice(0, 160)) + "</span>" +
            "</summary>" + t.comments.map(function (c) {
              return '<div class="cmt"><span class="who">' + esc(c.by) + "</span> · " + when(c.at) + ' · <a href="' + esc(c.url) + '" target="_blank" rel="noopener">link</a><div class="md">' + MD.render(c.body) + "</div></div>";
            }).join("") + "</details>";
        }).join("") + "</div>";
    }).join("");
  }

  function gatesHtml(g) {
    if (!g || (!g.crap && !g.mutate && !(g.cmut && g.cmut.length))) return '<p class="muted">No gate results found in this round\'s folder.</p>';
    var h = '<div class="grid2">';
    if (g.crap) {
      var o = g.crap.over10;
      h += '<div class="box"><div class="box-h"><b>Crapper</b>' + chip(o.length + " above CRAP 10", o.length ? "amber" : "green") + '<span class="muted small">' + g.crap.functions + " functions</span></div><div class=\"box-b\">" +
        (o.length ? '<table class="t"><tr><th>Function</th><th class="num-r">CC</th><th class="num-r">Cov</th><th class="num-r">CRAP</th></tr>' + o.slice(0, 40).map(function (r) {
          return '<tr><td><span class="mono">' + esc(r.fn) + '</span><div class="muted small mono">' + esc(r.ns) + '</div></td><td class="num-r">' + r.cc + '</td><td class="num-r">' + r.cov + '%</td><td class="num-r"><b>' + r.crap + "</b></td></tr>";
        }).join("") + "</table>" + (o.length > 40 ? '<p class="muted small">and ' + (o.length - 40) + " more</p>" : "") : '<span class="muted">None above 10</span>') +
        '<p class="muted small mono">' + esc(g.crap.file) + "</p></div></div>";
    }
    if (g.mutate) {
      var c = g.mutate.counts, tot = (c.killed || 0) + (c.survived || 0) + (c.uncovered || 0) || 1;
      h += '<div class="box"><div class="box-h"><b>Mutator</b>' + chip("killed " + (c.killed || 0), "green") + chip("survived " + (c.survived || 0), c.survived ? "red" : "") + chip("uncovered " + (c.uncovered || 0), "") +
        (c.score != null ? chip("score " + c.score + "%", "") : "") + '</div><div class="box-b">' +
        '<div class="bar"><span style="width:' + (100 * (c.killed || 0) / tot) + '%;background:var(--green)"></span><span style="width:' + (100 * (c.survived || 0) / tot) + '%;background:var(--red)"></span></div>' +
        (g.mutate.survivors.length ? "<b class=\"small\">Survivors</b><ul class=\"list-small\">" + g.mutate.survivors.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ul>" : "") +
        '<p class="muted small mono">' + esc(g.mutate.file) + "</p>" +
        (g.mutate_more ? '<p class="muted small">Other mutator runs: ' + g.mutate_more.map(function (m) { return esc(m.file.split("/").pop()) + " (killed " + (m.counts.killed || 0) + ", survived " + (m.counts.survived || 0) + ")"; }).join("; ") + "</p>" : "") +
        "</div></div>";
    }
    (g.cmut || []).forEach(function (m) {
      h += '<div class="box"><div class="box-h"><b>cargo-mutants</b>' + chip("caught " + m.caught, "green") + chip("missed " + m.missed, m.missed ? "red" : "") +
        chip("timeout " + m.timeout, m.timeout ? "amber" : "") + chip("unviable " + m.unviable, "") + '</div><div class="box-b">' +
        (m.missed_list.length ? "<b class=\"small\">Missed</b><ul class=\"list-small\">" + m.missed_list.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ul>" : "") +
        '<p class="muted small mono">' + esc(m.file) + "</p></div></div>";
    });
    if (!(g.cmut && g.cmut.length)) h += '<div class="box"><div class="box-h"><b>cargo-mutants</b>' + chip("not run", "") + '</div><div class="box-b muted small">No cargo-mutants results in this round. Re-review asks for <code>--tool all</code>.</div></div>';
    h += "</div>";
    if (g.times) h += '<p class="muted small mono">' + g.times.map(esc).join(" · ") + "</p>";
    return h;
  }
  // Report & gates: one fold per round with its gate results (newest open), then the report itself.
  function gatesFolds(d) {
    return d.rounds.slice().reverse().map(function (r, i) {
      return '<details class="gfold"' + (i === 0 ? " open" : "") + "><summary><b>Round " + r.n + "</b> " + chip(esc(r.kind), "") + verdictChip(r.verdict, r.verdict_label) +
        ' <span class="mono small">PR #' + r.pr + " · " + esc((r.head || "").slice(0, 8)) + '</span> <span class="muted small">' + esc(r.date || "") + "</span>" +
        (r.passes && r.passes.length ? ' <span class="muted small ell" title="' + esc(r.passes.join(", ")) + '">Ran: ' + r.passes.map(esc).join(", ") + "</span>" : "") +
        (r.section ? ' <span class="muted small">(' + esc(r.section) + ")</span>" : "") + '</summary><div class="gbody">' + gatesHtml(r.gates_data) +
        '<p class="small">Folder: <a href="#" data-file="' + esc("pr-reviews/" + (r.gates || "")) + '" class="mono">' + esc(r.gates || "—") + "</a></p></div></details>";
    }).join("");
  }
  function tabReport(d) {
    var h = '<div class="sub-h">Gates by round</div>' + (d.rounds.length ? gatesFolds(d) : '<p class="muted">No rounds yet.</p>');
    var reps = [];
    d.rounds.forEach(function (r) { var rp = (r.report || "").replace(/^pr-reviews\//, ""); if (rp && reps.indexOf(rp) < 0) reps.push(rp); });
    h += '<div class="sub-h">Report</div>';
    if (!reps.length) return h + '<p class="muted">No report yet.</p>';
    state.report = state.report && reps.indexOf(state.report) >= 0 ? state.report : reps[reps.length - 1];
    return h + '<div class="actions">' + (reps.length > 1 ? '<select id="rep-pick" class="btn btn-sm" aria-label="Report">' + reps.map(function (r) {
      return '<option value="' + esc(r) + '"' + (r === state.report ? " selected" : "") + ">" + esc(r) + "</option>";
    }).join("") + "</select>" : '<span class="mono small muted">' + esc(state.report) + "</span>") +
      (d.files.length ? '<details class="small"><summary class="muted" style="cursor:pointer">Local review files (' + d.files.length + ")</summary>" +
      '<div class="files">' + d.files.map(function (f) { return '<a href="#" data-file="' + esc(f) + '">' + esc(f) + "</a>"; }).join("") + "</div></details>" : "") +
      '</div><div id="report" class="md report"><span class="spin"></span></div>';
  }
  document.addEventListener("change", function (ev) { if (ev.target.id === "rep-pick") { state.report = ev.target.value; renderTab(); } });
  function wireReport() {
    var el = $("#report");
    if (!el) return;
    api("/api/file?path=" + encodeURIComponent("pr-reviews/" + state.report)).then(function (j) {
      el.innerHTML = '<p class="muted small mono">' + esc(j.abs) + "</p>" + MD.render(j.text);
    }).catch(function (e) { el.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; });
  }

  // ---------------- Drafts tab: approve, reject or edit each planned comment (writes decisions.json)
  var SEV_CLS = { blocker: "red", major: "amber", minor: "blue", nit: "", security: "purple" };
  var DEC_CLS = { approved: "green", rejected: "red", pending: "amber" };
  var VERDICT_OPTS = [["request_changes", "Request changes"], ["approve_with_nits", "Approve with nits"], ["approve", "Approve"], ["comment", "Comment only"]];
  function tabDrafts() { return '<div id="drafts"><p class="muted"><span class="spin"></span> Loading drafts…</p></div>'; }
  function wireDrafts() {
    var s = state.detail.summary, el = $("#drafts");
    state.drafts = null;
    api("/api/drafts/" + s.repo + "/" + s.number).then(function (D) {
      if (!D.exists) {
        el.innerHTML = '<div class="d-title"><h2>' + (state.detail.mine ? "Planned replies" : "Planned review comments") + '</h2></div><div class="note">There is no drafts file yet. Ask the session to write one, then open this tab again.</div>' +
          '<div class="actions"><button class="btn" data-copy-text="ask">Copy prompt</button>' +
          (s.session && s.session.tty ? '<button class="btn" data-focus-tty="' + esc(s.session.tty) + '">Focus tab (' + esc(s.session.tty) + ")</button>" : "") +
          '<span class="muted small" id="copied"></span></div><pre id="copy-src">' + esc(D.ask_prompt) + "</pre>";
        return;
      }
      if (D.error) { el.innerHTML = '<div class="err">' + esc(D.error) + " (" + esc(D.path) + ")</div>"; return; }
      state.drafts = D;
      state.dFilter = state.dFilter || { sev: "", dec: "" };
      drawDrafts();
    }).catch(function (e) { el.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; });
  }
  function dCounts() {
    var c = { approved: 0, rejected: 0, pending: 0 };
    state.drafts.items.forEach(function (it) { c[it.decision]++; });
    return c;
  }
  // Step bar: Review → Sent to session → Confirm in its tab → Posted. One main button per step.
  function draftFlow(D, c) {
    var ss = D.session || {}, tty = esc(ss.tty || ""), sent = D.sent, posted = D.posted;
    var where = ss.open && ss.tty ? tty : "its tab";
    var f = { cur: 0, btn: "", extra: "", note: "" };
    if (posted) {
      f.cur = 4;
      f.btn = '<a class="btn btn-primary" href="' + esc(posted.url || "") + '" target="_blank" rel="noopener">Posted ✓ view on GitHub</a>';
      f.note = "Posted " + when(posted.at) + (posted.source === "file" ? " (the session recorded it)" : "") + ".";
      f.extra = '<button class="btn btn-sm btn-ghost" data-send-drafts title="Post the current decisions again through the session">Send again…</button>';
    } else if (sent) {
      f.cur = 2;
      if (ss.open) {
        f.btn = '<button class="btn btn-primary" data-d-focus>Waiting for you to confirm in ' + tty + " · Focus</button>";
        f.note = "Sent " + when(sent.at) + (sent.how === "copied" ? " (prompt copied)" : " (new tab " + esc(sent.tty || "") + ")") + ". Check the list in the tab, then say yes there.";
      } else {
        f.btn = '<button class="btn btn-primary" data-send-drafts>Session tab closed · Send again</button>';
        f.note = "Sent " + when(sent.at) + ", but the session tab is not open now. Send again resumes it.";
      }
      f.extra = '<button class="btn btn-sm" data-act="refresh-one" title="Look for the posted review on GitHub">Check again</button>';
    } else if (c.pending) {
      f.cur = 0;
      f.btn = '<button class="btn btn-primary" disabled>Approve the ' + c.pending + " pending first</button>";
      f.extra = '<button class="btn btn-sm btn-ghost" data-send-drafts title="Pending items are not posted">Send anyway…</button>';
      f.note = c.approved + " approved, " + c.rejected + " rejected so far. Pending items are never posted.";
    } else {
      f.cur = 1;
      f.btn = ss.open
        ? '<button class="btn btn-primary" data-d-copygo>Copy prompt and go to ' + tty + "</button>"
        : '<button class="btn btn-primary" data-send-drafts>Send ' + c.approved + " approved to the session</button>";
      f.note = ss.open ? "The session is open in " + tty + ". One click copies the prompt and brings the tab to the front; paste it there."
        : "This resumes the session in a new tab. You see the command first.";
    }
    var steps = [
      ["Review", c.pending ? c.pending + " pending" : c.approved + " approved"],
      ["Sent to session", sent ? ago(sent.at) : ""],
      ["Confirm in " + where, ""],
      ["Posted", posted ? ago(posted.at) : ""]];
    var bar = '<ol class="steps">' + steps.map(function (st, i) {
      var cls = i < f.cur ? "done" : i === f.cur ? "cur" : "";
      return '<li class="' + cls + '"><span class="n">' + (i < f.cur ? "✓" : i + 1) + '</span><span class="l">' + st[0] +
        (st[1] ? '<span class="d">' + st[1] + "</span>" : "") + "</span></li>";
    }).join("") + "</ol>";
    return '<div class="flow">' + bar + '<div class="flow-row"><div class="flow-note"><b>Nothing posts from here.</b> The session posts after you confirm in its tab. ' + f.note + "</div>" +
      '<div class="flow-act">' + f.extra + f.btn + "</div></div></div>";
  }
  function copyAndGo() {
    var s = state.detail.summary;
    api("/api/action/send_drafts", { repo: s.repo, number: s.number, dry_run: true }).then(function (r) {
      if (r.needs_force || !r.session_open) return sendDrafts(false);  // warnings, or the tab closed meanwhile
      var done = function () {
        api("/api/action/drafts_copied", { repo: s.repo, number: s.number }).then(function (x) {
          if (!x.ok) info("Could not record the send", '<div class="err">' + esc(x.error || "") + "</div>");
          wireDrafts();
        });
      };
      var fallback = function () {
        dialog("Copy the prompt", "<p>The browser did not let me copy. Select the text, copy it, then press Done. It brings <b>" + esc(r.tty) + "</b> to the front.</p>" +
          '<pre id="rr-prompt">' + esc(r.prompt) + "</pre>", "Done", done);
      };
      if (navigator.clipboard) navigator.clipboard.writeText(r.prompt).then(done, fallback); else fallback();
    }).catch(function (e) { info("Failed", '<div class="err">' + esc(e.message) + "</div>"); });
  }
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-d-copygo],[data-d-focus]");
    if (!t || !state.drafts) return;
    ev.preventDefault();
    if (t.hasAttribute("data-d-copygo")) return copyAndGo();
    var ss = state.drafts.session || {}, s = state.detail.summary;
    if (ss.kind === "review") api("/api/action/focus", { repo: s.repo, number: s.number });
    else api("/api/action/my_focus", { repo: s.repo, number: s.number, session: ss.session });
  });
  function drawDrafts() {
    var D = state.drafts, el = $("#drafts"), f = state.dFilter, c = dCounts();
    if (!el) return;
    var mine = !!state.detail.mine;
    var h = '<div class="d-title"><h2>' + (D.job === "replies" || mine ? "Planned replies" : "Planned review comments") + "</h2>" +
      '<span class="muted small">' + (D.job === "replies" || mine ? "Answers to reviewers on your PR, written by the work session." : "Comments the review session plans to post on GitHub.") +
      " Approve, reject or edit each one.</span></div>" + draftFlow(D, c) + walkthroughHtml(D);
    if (D.head_moved) h += '<div class="err">⚠ The PR head moved since the drafts were written (' + esc(D.head) + " → " + esc(D.head_now) + "). Line numbers may be out of date.</div>";
    h += '<div class="box"><div class="box-h"><b>' + (D.job === "replies" || mine ? "Summary" : "Verdict and summary") + '</b> · drafts at <span class="mono">' + esc(D.head) + "</span> " +
      chip(c.pending + " pending", c.pending ? "amber" : "") + chip(c.approved + " approved", "green") + chip(c.rejected + " rejected", "red") +
      '<span class="muted small" id="d-saved" style="margin-left:auto">' + (D.decided_at ? "Saved " + ago(D.decided_at) : "Not saved yet") + "</span></div>" +
      '<div class="box-b"><label class="small"><b>Verdict</b> <select id="d-verdict">' + VERDICT_OPTS.map(function (v) {
        return '<option value="' + v[0] + '"' + (v[0] === D.verdict ? " selected" : "") + ">" + v[1] + "</option>";
      }).join("") + "</select></label>" + (D.verdict !== D.verdict_drafted ? ' <span class="muted small">(drafted: ' + esc(D.verdict_drafted) + ")</span>" : "") +
      '<div class="d-sum"><div class="muted small">Summary (the review body)</div><textarea id="d-summary" rows="5">' + esc(D.summary || "") + "</textarea>" +
      '<details><summary class="muted small" style="cursor:pointer">Preview</summary><div class="md" id="d-sum-prev">' + MD.render(D.summary || "") + "</div></details></div>" +
      '<p class="muted small mono d-path" title="' + esc(D.path + " → " + D.decisions_path) + '">' + esc(D.path.split("/").slice(-2).join("/")) + " → " + esc(D.decisions_path.split("/").slice(-1)[0]) + "</p></div></div>";
    h += '<div class="actions d-tools"><select id="d-f-sev" aria-label="Severity"><option value="">All severities</option>' +
      ["blocker", "major", "minor", "nit", "security"].map(function (x) { return "<option" + (f.sev === x ? " selected" : "") + ">" + x + "</option>"; }).join("") + "</select>" +
      '<select id="d-f-dec"><option value="">All decisions</option>' + ["pending", "approved", "rejected"].map(function (x) { return "<option" + (f.dec === x ? " selected" : "") + ">" + x + "</option>"; }).join("") + "</select>" +
      '<button class="btn btn-sm" data-bulk="approve-proven">Approve all proven</button>' +
      '<button class="btn btn-sm" data-bulk="approve-shown">Approve all shown</button>' +
      '<button class="btn btn-sm" data-bulk="reject-nits">Reject all nits</button>' +
      '<button class="btn btn-sm" data-bulk="reset">Set all to pending</button>' +
      "</div>";
    var shown = D.items.filter(function (it) { return (!f.sev || it.severity === f.sev) && (!f.dec || it.decision === f.dec); });
    h += shown.length ? shown.map(draftCard).join("") : '<p class="muted">No items match the filters.</p>';
    el.innerHTML = h;
  }
  function draftCard(it) {
    var body = it.edited_body != null ? it.edited_body : it.body;
    var ctx = it.context && it.context.length ? '<div class="codewrap"><table class="diff">' + it.context.map(function (r) {
      return '<tr class="' + r.kind + (r.n === it.line ? " hl" : "") + '"><td class="ln">' + r.o + '</td><td class="ln">' + r.n + "</td><td>" + esc(r.text) + "</td></tr>";
    }).join("") + "</table></div>" : (it.path ? '<p class="muted small">No diff context (the line is not in the PR diff).</p>' : "");
    var th = it.thread ? '<details class="d-th"><summary class="small">Answers the thread on <span class="path">' + esc(it.thread.where) + "</span> " +
      chip(it.thread.resolved ? "Resolved" : "Open", it.thread.resolved ? "green" : "amber") + "</summary>" +
      it.thread.comments.map(function (c) { return '<div class="cmt"><span class="who">' + esc(c.by) + "</span> · " + when(c.at) + '<div class="md">' + MD.render(c.body) + "</div></div>"; }).join("") +
      '<a class="small" href="' + esc(it.thread.url) + '" target="_blank" rel="noopener">Open on GitHub</a></details>'
      : (it.in_reply_to ? '<p class="muted small">Replies to comment ' + esc(it.in_reply_to) + " (thread not found in the PR data).</p>" : "");
    return '<div class="box d-card d-' + it.decision + '" data-id="' + esc(it.id) + '"><div class="box-h">' +
      '<b class="mono">' + esc(it.id) + "</b>" + chip(esc(it.severity || "?"), SEV_CLS[it.severity] || "") + chip(esc(it.kind || "?"), "") +
      (it.finding ? chip("finding " + esc(it.finding), "") : "") +
      (it.step ? chip("found by: " + esc(String(it.step).replace("_", " ")), "") : "") +
      (it.proof ? chip(esc(it.proof), it.proof === "proven" ? "green" : it.proof === "not proven" ? "amber" : "") : "") +
      (it.path ? '<span class="path">' + esc(it.path) + (it.line ? ":" + esc(it.line) : "") + "</span>" : "") +
      (it.edited_body != null ? chip("edited", "blue") : "") +
      '<span style="margin-left:auto"></span>' + chip(esc(it.decision), DEC_CLS[it.decision]) +
      '<button class="btn btn-sm' + (it.decision === "approved" ? " btn-primary" : "") + '" data-dec="approved">Approve</button>' +
      '<button class="btn btn-sm' + (it.decision === "rejected" ? " d-rej" : "") + '" data-dec="rejected">Reject</button>' +
      '<button class="btn btn-sm" data-edit>Edit</button></div><div class="box-b">' + explainHtml(it) + ctx + th +
      (it.explain ? '<div class="muted small d-lbl">The comment for the author (this is what gets posted)</div>' : "") +
      '<div class="md d-body">' + MD.render(body || "") + "</div>" +
      '<div class="d-edit" hidden><textarea rows="8">' + esc(body || "") + '</textarea><div class="muted small">Preview</div><div class="md d-prev">' + MD.render(body || "") + "</div>" +
      '<div class="actions"><button class="btn btn-sm" data-edit-done>Done</button><button class="btn btn-sm" data-edit-undo>Undo my edit</button></div></div>' +
      reasonRow(it) +
      '<input class="d-note" placeholder="Reason or context (optional; the session and the learner read it)" value="' + esc(it.note || "") + '"></div></div>';
  }
  // Plain-language notes from the review session (never posted). See drafts.EXPLAIN_TEXT.
  var EXPLAIN_ROWS = [["does", "What this code does"], ["breaks", "What goes wrong"], ["impact", "Why it matters"],
    ["why_severity", "Why this severity"], ["confidence", "How sure we are"], ["decide", "Your call"]];
  function explainHtml(it) {
    var x = it.explain;
    var ask = '<button class="btn btn-sm" data-ask="' + esc(it.id) + '">Ask about this</button>';
    if (!x || typeof x !== "object") {
      return '<div class="d-explain d-explain-none"><span class="muted small">No plain-language note for this draft.</span>' +
        '<button class="btn btn-sm" data-ask-explain>Ask the session to explain all drafts</button>' + ask + "</div>";
    }
    return '<div class="d-explain"><div class="d-explain-h"><b>In plain words</b>' + ask + "</div><dl>" +
      EXPLAIN_ROWS.filter(function (r) { return x[r[0]]; }).map(function (r) {
        return "<dt>" + r[1] + '</dt><dd class="md">' + MD.render(String(x[r[0]])) + "</dd>";
      }).join("") + "</dl></div>";
  }
  function walkthroughHtml(D) {
    var w = D.walkthrough;
    if (!w || typeof w !== "object") return "";
    // Exceptions first: rows that need a look are shown; the rest fold under "show N more".
    function table(title, rows, isFlag, cols, row) {
      rows = rows || [];
      if (!rows.length) return "";
      var flag = rows.filter(isFlag), rest = rows.filter(function (r) { return !isFlag(r); });
      var head = "<thead><tr>" + cols.map(function (c) { return "<th>" + c + "</th>"; }).join("") + "</tr></thead>";
      var h = "<h4>" + title + " " + (flag.length ? chip(flag.length + " to look at", "amber") : chip("nothing flagged", "green")) + "</h4>";
      if (flag.length) h += '<table class="d-tbl">' + head + "<tbody>" + flag.map(row).join("") + "</tbody></table>";
      if (rest.length) h += '<details class="d-more"><summary class="small muted">' + (flag.length ? "Show " + rest.length + " more" : "Show all " + rest.length) +
        '</summary><table class="d-tbl">' + head + "<tbody>" + rest.map(row).join("") + "</tbody></table></details>";
      return h;
    }
    var VCLS = { expected: "green", surprising: "amber", unrelated: "red" };
    var cell = function (a, b) { return "<td><b>" + esc(a || "") + "</b>" + (b ? '<div class="path small">' + esc(b) + "</div>" : "") + "</td>"; };
    var td = function (x) { return "<td>" + esc(x || "") + "</td>"; };
    var risky = (w.risky || []).map(function (r) { return "<li><span class=\"path\">" + esc(r.path || "") + "</span> — " + esc(r.why || "") + "</li>"; }).join("");
    var gl = (w.glossary || []).map(function (g) { return "<dt>" + esc(g.term || "") + "</dt><dd>" + esc(g.meaning || "") + "</dd>"; }).join("");
    var ECLS = { small: "green", medium: "amber", large: "red" };
    return '<details class="box d-walk" open><summary class="box-h"><b>PR walkthrough</b> <span class="muted small">a map before the details (not posted)</span></summary><div class="box-b">' +
      (w.summary ? '<div class="md">' + MD.render(String(w.summary)) + "</div>" : "") +
      table("Scope: should this PR touch these files?", w.scope, function (r) { return r.verdict !== "expected"; }, ["", "File", "Why"], function (r) {
        return "<tr><td>" + chip(esc(r.verdict || "?"), VCLS[r.verdict] || "") + '</td><td class="path">' + esc(r.path || "") + "</td>" + td(r.why) + "</tr>"; }) +
      (w.flow ? '<h4>How it flows</h4><div class="md">' + MD.render(String(w.flow)) + "</div>" : "") +
      table("New components: could an existing one be used?", w.new_components, function (r) { return r.reuse !== "no"; }, ["New", "Existing", "Reuse", "Why"], function (c) {
        return "<tr>" + cell(c.name, c.path) + td(c.similar || "none") + "<td>" + chip(esc(c.reuse || "?"), c.reuse === "no" ? "green" : "amber") + "</td>" + td(c.why) + "</tr>"; }) +
      table("What triggers the new logic", w.triggers, function (t) { return /dead|nothing|twice|order|none/i.test((t.trigger || "") + " " + (t.how || "")); }, ["Logic", "Trigger", "How it is reached"], function (t) {
        return "<tr>" + cell(t.what, t.path) + td(t.trigger) + td(t.how) + "</tr>"; }) +
      table("Tests as the spec", w.tests, function (t) { return t.verdict !== "spec"; }, ["Test", "", "Why"], function (t) {
        return "<tr>" + cell(t.test) + "<td>" + chip(esc(t.verdict || "?"), t.verdict === "spec" ? "green" : t.verdict === "missing" ? "red" : "amber") + "</td>" + td(t.why) + "</tr>"; }) +
      table("Rollout and ops", w.rollout, function (r) { return r.status !== "ok"; }, ["Item", "", "Why"], function (r) {
        return "<tr>" + cell(r.item) + "<td>" + chip(esc(r.status || "?"), r.status === "ok" ? "green" : r.status === "missing" ? "red" : "amber") + "</td>" + td(r.why) + "</tr>"; }) +
      table("Right-sized? What could be better", w.improvements, function (x) { return x.when === "this PR"; }, ["Change", "Why it helps", "Kind · effort · when"], function (x) {
        return "<tr>" + cell(x.what, x.where) + td(x.why) + "<td>" + (x.kind ? chip(esc(x.kind), "") + " " : "") + chip(esc(x.effort || "?"), ECLS[x.effort] || "") + " " +
          chip(esc(x.when || "?"), x.when === "this PR" ? "blue" : "") + "</td></tr>"; }) +
      table("Industry practice, judged on fit", w.practices, function (x) { return x.verdict === "finding"; }, ["Problem", "Common practice", "This PR", "Verdict"], function (x) {
        return "<tr>" + cell(x.problem) + td(x.practice) + "<td>" + chip(esc(x.pr || "?"), x.pr === "follows" ? "green" : "amber") + '<div class="small">' + esc(x.reason || "") + "</div></td><td>" +
          chip(esc(x.verdict || "?"), { "fine": "green", "finding": "red", "practice does not fit here": "blue" }[x.verdict] || "") + "</td></tr>"; }) +
      (risky ? "<h4>Where the risk is</h4><ul>" + risky + "</ul>" : "") +
      (gl ? '<h4>Words used here</h4><dl class="d-gloss">' + gl + "</dl>" : "") + "</div></details>";
  }
  // Questions go to the review session (copied, then its tab comes to the front). They are also logged for the learner.
  function askSession(text, kind, id) {
    var s = state.detail.summary;
    api("/api/drafts/ask", { repo: s.repo, number: s.number, kind: kind, id: id || "", text: text }).then(function (r) {
      if (!r.ok) return info("Not sent", '<div class="err">' + esc(r.error || "failed") + "</div>");
      var go = function () { api("/api/action/focus", { repo: s.repo, number: s.number }); };
      var shown = '<p class="small">Paste this in the review session' + (r.tty ? " (<b>" + esc(r.tty) + "</b>)" : "") + ". It was copied.</p>" +
        '<pre class="mono small" id="copy-src" style="white-space:pre-wrap">' + esc(r.prompt) + "</pre>" +
        '<div class="actions"><button class="btn btn-sm" data-copy-text>Copy again</button> <span id="copied" class="muted small"></span></div>';
      if (navigator.clipboard) navigator.clipboard.writeText(r.prompt).then(function () { go(); info("Question copied", shown); }, function () { info("Copy the question", shown); });
      else info("Copy the question", shown);
    });
  }

  // One-click reasons for a decision. They help the learner (see learner.py); all optional.
  function reasonRow(it) {
    var R = (state.drafts.reasons || {})[it.decision];
    if (!R) return "";
    var on = it.reasons || [];
    return '<div class="d-reasons"><span class="muted small">' + (it.decision === "rejected" ? "Why reject?" : "Why approve?") + "</span>" +
      Object.keys(R).map(function (k) {
        return '<button class="d-reason' + (on.indexOf(k) >= 0 ? " on" : "") + '" data-reason="' + esc(k) + '" aria-pressed="' + (on.indexOf(k) >= 0) + '">' + esc(R[k]) + "</button>";
      }).join("") + "</div>";
  }
  var saveTimer = null;
  function saveDrafts(now) {
    clearTimeout(saveTimer);
    var go = function () {
      var D = state.drafts, s = state.detail.summary, sv = $("#d-saved");
      if (sv) sv.textContent = "Saving…";
      api("/api/drafts/save", { repo: s.repo, number: s.number, verdict: D.verdict, summary: D.summary,
        items: D.items.map(function (it) { return { id: it.id, decision: it.decision, body: it.edited_body != null ? it.edited_body : it.body, note: it.note || "", reasons: it.reasons || [] }; }) })
        .then(function (r) {
          var sv2 = $("#d-saved");
          if (r.ok) { D.decided_at = r.decided_at; if (sv2) sv2.textContent = "Saved"; loadList(); }
          else if (sv2) sv2.textContent = "Not saved: " + r.error;
        }).catch(function (e) { var sv3 = $("#d-saved"); if (sv3) sv3.textContent = "Not saved: " + e.message; });
    };
    if (now) go(); else saveTimer = setTimeout(go, 500);
  }
  function itemOf(el) { var id = el.closest(".d-card").dataset.id; return state.drafts.items.filter(function (x) { return x.id === id; })[0]; }
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-dec],[data-reason],[data-ask],[data-ask-explain],[data-edit],[data-edit-done],[data-edit-undo],[data-bulk],[data-send-drafts],[data-copy-text]");
    if (!t) return;
    ev.preventDefault();
    if (t.dataset.copyText) {
      var txt = ($("#copy-src") || {}).textContent || "";
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { $("#copied").textContent = "Copied"; });
      return;
    }
    if (!state.drafts) return;
    if (t.hasAttribute("data-send-drafts")) return sendDrafts(false);
    if (t.hasAttribute("data-ask-explain")) return askSession("", "explain_all");
    if (t.dataset.ask) {
      var qid = t.dataset.ask;
      dialog("Ask about " + qid, '<p class="small">Ask the review session anything about this draft. It answers in plain words, in its tab.</p>' +
        '<textarea id="ask-q" rows="4" style="width:100%" placeholder="For example: why can the lock not stop this? What would a user see?"></textarea>', "Copy and go to the session", function () {
        var q = ($("#ask-q") || {}).value || "";
        if (q.trim()) askSession(q.trim(), "question", qid);
      });
      return;
    }
    if (t.dataset.bulk) {
      var f = state.dFilter, b = t.dataset.bulk;
      state.drafts.items.forEach(function (it) {
        var shown = (!f.sev || it.severity === f.sev) && (!f.dec || it.decision === f.dec);
        if (b === "approve-proven" && it.proof === "proven") it.decision = "approved";
        if (b === "approve-shown" && shown) it.decision = "approved";
        if (b === "reject-nits" && it.severity === "nit") it.decision = "rejected";
        if (b === "reset") it.decision = "pending";
      });
      drawDrafts(); saveDrafts(true); return;
    }
    var it = itemOf(t), card = t.closest(".d-card");
    if (t.dataset.dec) { it.decision = it.decision === t.dataset.dec ? "pending" : t.dataset.dec; it.reasons = []; drawDrafts(); saveDrafts(true); return; }
    if (t.dataset.reason) {
      var rs = it.reasons || [], k = t.dataset.reason, i = rs.indexOf(k);
      if (i >= 0) rs.splice(i, 1); else rs.push(k);
      it.reasons = rs; drawDrafts(); saveDrafts(true); return;
    }
    if (t.hasAttribute("data-edit")) { card.querySelector(".d-edit").hidden = false; card.querySelector(".d-body").hidden = true; card.querySelector("textarea").focus(); return; }
    if (t.hasAttribute("data-edit-done")) { drawDrafts(); return; }
    if (t.hasAttribute("data-edit-undo")) { it.edited_body = null; drawDrafts(); saveDrafts(true); }
  });
  document.addEventListener("input", function (ev) {
    if (!state.drafts || !ev.target.closest("#drafts")) return;
    var t = ev.target;
    if (t.id === "d-summary") { state.drafts.summary = t.value; $("#d-sum-prev").innerHTML = MD.render(t.value); return saveDrafts(); }
    if (t.closest(".d-edit")) {
      var it = itemOf(t);
      it.edited_body = t.value === it.body ? null : t.value;
      t.closest(".d-edit").querySelector(".d-prev").innerHTML = MD.render(t.value);
      return saveDrafts();
    }
    if (t.classList.contains("d-note")) { itemOf(t).note = t.value; return saveDrafts(); }
  });
  document.addEventListener("change", function (ev) {
    if (!state.drafts || !ev.target.closest("#drafts")) return;
    var t = ev.target;
    if (t.id === "d-verdict") { state.drafts.verdict = t.value; saveDrafts(true); drawDrafts(); }
    if (t.id === "d-f-sev") { state.dFilter.sev = t.value; drawDrafts(); }
    if (t.id === "d-f-dec") { state.dFilter.dec = t.value; drawDrafts(); }
  });
  function sendDrafts(force) {
    var s = state.detail.summary;
    dialog("Send to session", '<p><span class="spin"></span> Building the prompt…</p>', null);
    api("/api/action/send_drafts", { repo: s.repo, number: s.number, dry_run: true, force: force }).then(function (r) {
      var warn = (r.warnings || []).map(function (w) { return '<div class="note">⚠ ' + esc(w) + "</div>"; }).join("");
      if (r.needs_force) {
        dialog("Check before sending", warn + '<label class="small"><input type="checkbox" id="d-force"> Send anyway</label><label>Prompt</label><pre>' + esc(r.prompt) + "</pre>", "Next", function () {
          if ($("#d-force").checked) setTimeout(function () { sendDrafts(true); }, 0);
        });
        return;
      }
      if (r.session_open) {
        dialog("Send to " + esc(r.tty), warn + "<p>The session is open in <b>" + esc(r.tty) + "</b>. The button copies the prompt and brings that tab to the front. Paste it there.</p>" +
          '<pre id="rr-prompt">' + esc(r.prompt) + "</pre>", "Copy prompt and go to " + r.tty, function () {
            var s2 = state.detail.summary, txt = r.prompt;
            var done = function () { api("/api/action/drafts_copied", { repo: s2.repo, number: s2.number }).then(function () { wireDrafts(); }); };
            if (navigator.clipboard) navigator.clipboard.writeText(txt).then(done, done); else done();
          });
        return;
      }
      if (r.ok === false) { info("Cannot send", warn + '<div class="err">' + esc(r.error) + "</div>" + (r.prompt ? '<pre id="rr-prompt">' + esc(r.prompt) + "</pre>" : "")); return; }
      dialog("Send to session", warn + "<p>This resumes the review session in a new tab with the prompt below. The session shows you what it will post and posts only after you confirm there.</p>" +
        "<label>Command</label><pre>" + esc(r.tab_command) + "</pre><label>Prompt</label><pre>" + esc(r.prompt) + "</pre>", "Open tab and run", function () {
          api("/api/action/send_drafts", { repo: s.repo, number: s.number, force: force }).then(function (res) {
            if (res.ok) { info("Sent", "<p>Opened in <b>" + esc(res.tty) + "</b>. Confirm there when the session shows what it will post.</p>"); wireDrafts(); }
            else if (res.dry_run) info("Dry run", "<pre>" + esc(res.would_run) + "</pre>");
            else info("Failed", '<div class="err">' + esc(res.error || "unknown error") + "</div>");
          });
        });
    }).catch(function (e) { info("Failed", '<div class="err">' + esc(e.message) + "</div>"); });
  }

  // Local review files (reports, gate output) open in a dialog.
  function openFile(path) {
    dialog(path, '<div id="fv"><span class="spin"></span></div>', null);
    var fv = $("#fv");
    api("/api/file?path=" + encodeURIComponent(path)).then(function (j) {
      if (j.dir) {
        fv.innerHTML = '<p class="muted small mono">' + esc(j.abs) + '</p><div class="files">' + j.items.map(function (i) {
          return '<a href="#" data-file="' + esc(i.path) + '">' + esc(i.name) + (i.dir ? "/" : "") + ' <span class="muted">' + (i.dir ? "" : Math.ceil(i.size / 1024) + " KB") + "</span></a>";
        }).join("") + "</div>";
      } else if (/\.md$/.test(path)) {
        fv.innerHTML = '<p class="muted small mono">' + esc(j.abs) + '</p><div class="md">' + MD.render(j.text) + "</div>";
      } else {
        fv.innerHTML = '<p class="muted small mono">' + esc(j.abs) + (j.truncated ? " (cut at 1.5 MB)" : "") + "</p><pre>" + esc(j.text) + "</pre>";
      }
    }).catch(function (e) { fv.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; });
  }

  function loadPR(key, force) {
    var parts = key.split("/");
    state.cur = key;
    renderSide();
    markCurrent();
    if (!force) $("#main").innerHTML = '<p class="muted"><span class="spin"></span> Loading ' + esc(key) + "…</p>";
    return api("/api/pr/" + parts[0] + "/" + parts[1]).then(function (d) {
      if (state.cur !== key) return;
      state.detail = d; renderPR();
    }).catch(function (e) { $("#main").innerHTML = '<div class="err">' + esc(e.message) + "</div>"; });
  }

  // ------------------------------------------------------------------ dialogs and actions
  function dialog(title, bodyHtml, okText, onOk) {
    var dlg = $("#dlg");
    $("#dlg-title").textContent = title;
    $("#dlg-body").innerHTML = bodyHtml;
    var ok = $("#dlg-ok");
    ok.textContent = okText || "OK";
    ok.hidden = !okText;
    ok.disabled = false;
    dlg.onclose = function () { if (dlg.returnValue === "ok" && onOk) onOk(); };
    dlg.returnValue = "";
    if (!dlg.open) dlg.showModal();  // already open: just swap the content
  }
  function info(title, html) { dialog(title, html, null); }

  // ---------------- review checklist (New review and Re-review)
  function checklistHtml(spec) {
    return '<div class="ck">' + spec.groups.map(function (g) {
      var bools = g.items.filter(function (it) { return it.type === "bool" && !it.disabled; });
      return '<fieldset class="ck-g" data-g="' + g.key + '"><legend><label><input type="checkbox" class="ck-all"' + (bools.length ? "" : " disabled") + "> " + esc(g.label) + "</label></legend>" +
        g.items.map(function (it) {
          var id = "ck-" + it.key, inp;
          if (it.type === "bool") inp = '<label class="ck-i"><input type="checkbox" id="' + id + '" data-k="' + it.key + '"' + (it.value ? " checked" : "") + (it.disabled ? " disabled" : "") + "> " + esc(it.label) + "</label>";
          else if (it.type === "select") inp = '<label class="ck-i">' + esc(it.label) + ' <select id="' + id + '" data-k="' + it.key + '">' + it.choices.map(function (c) { return "<option" + (c === it.value ? " selected" : "") + ">" + c + "</option>"; }).join("") + "</select></label>";
          else if (it.type === "number") inp = '<label class="ck-i">' + esc(it.label) + ' <input type="number" id="' + id + '" data-k="' + it.key + '" value="' + esc(it.value) + '" min="' + (it.min || 1) + '" max="' + (it.max || 99) + '" style="width:60px"></label>';
          else inp = '<label class="ck-i">' + esc(it.label) + ' <input type="text" id="' + id + '" data-k="' + it.key + '" value="' + esc(it.value) + '" placeholder="' + esc(it.placeholder || "") + '" style="width:200px"></label>';
          return inp + (it.note ? '<div class="muted small ck-note">' + esc(it.note) + "</div>" : "");
        }).join("") + "</fieldset>";
    }).join("") + "</div>";
  }
  function readChecklist() {
    var o = {};
    document.querySelectorAll(".ck [data-k]").forEach(function (el) {
      o[el.dataset.k] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
    });
    return o;
  }
  function syncGroups() {
    document.querySelectorAll(".ck-g").forEach(function (fs) {
      var boxes = [].slice.call(fs.querySelectorAll("input[type=checkbox][data-k]:not(:disabled)"));
      var all = fs.querySelector(".ck-all"), on = boxes.filter(function (b) { return b.checked; }).length;
      all.checked = boxes.length > 0 && on === boxes.length;
      all.indeterminate = on > 0 && on < boxes.length;  // mixed state
    });
  }

  function previewAndRun(kind, repo, number) {
    var path = kind === "new" ? "/api/action/new" : "/api/action/rereview";
    var ck = kind === "new" ? "new" : "rereview";
    var title = kind === "new" ? "Start a review of #" + number : "Re-review #" + number;
    dialog(title, '<p><span class="spin"></span> Loading the checklist…</p>', null);
    var timer = null, last = null;
    var preview = function () {
      var out = $("#ck-preview");
      if (!out) return;
      out.innerHTML = '<p class="muted"><span class="spin"></span> Building the preview…</p>';
      var opts = readChecklist();
      api(path, { repo: repo, number: number, dry_run: true, options: opts }).then(function (r) {
        last = r;
        if (r.session_open) {
          out.innerHTML = '<div class="note">The session is open in <b>' + esc(r.tty) + "</b>. Running gives you this prompt to paste there; no second copy starts.</div>" +
            "<label>Recheck prompt (exactly what you paste)</label><pre id=\"rr-prompt\">" + esc(r.prompt) + "</pre>";
          $("#dlg-ok").textContent = "Copy prompt";
          return;
        }
        if (r.ok === false) { out.innerHTML = '<div class="err">' + esc(r.error) + "</div>"; $("#dlg-ok").disabled = true; return; }
        $("#dlg-ok").disabled = false; $("#dlg-ok").textContent = "Open tab and run";
        var h = "<label>Command for the new tab</label><pre>" + esc(r.tab_command) + "</pre>";
        if (r.prompt) h += "<label>Recheck prompt (exactly what is sent)</label><pre>" + esc(r.prompt) + "</pre>";
        if (r.brief) h += "<label>Brief (exactly what is written to " + esc(r.brief_path) + (r.brief_exists ? "; the old one is kept as brief.md.prev" : "") + ")</label><pre>" + esc(r.brief) + "</pre>";
        if (r.passes) h += '<p class="small">Recorded on the round as: <span class="mono">' + esc(r.passes.join(", ")) + "</span></p>";
        out.innerHTML = h;
      }).catch(function (e) { out.innerHTML = '<div class="err">' + esc(e.message) + "</div>"; });
    };
    var load = function () {
      api("/api/checklist?repo=" + encodeURIComponent(repo) + "&number=" + number + "&kind=" + ck).then(function (spec) {
        dialog(title,
          "<p class=\"small\">Pick what the session runs. Nothing is posted from here; the session asks you before it posts." +
          (spec.saved ? ' Your last choices for this repo are loaded. <a href="#" id="ck-reset">Reset to defaults</a>' : "") + "</p>" +
          checklistHtml(spec) + '<div id="ck-preview"></div>', "Open tab and run", function () {
            var opts = readChecklist();
            if (last && last.session_open) {  // copy the prompt instead of a second copy of the session
              if (navigator.clipboard) navigator.clipboard.writeText(last.prompt);
              info("Copied", "<p>The prompt is copied. Paste it in <b>" + esc(last.tty) + "</b>.</p>" +
                '<div class="actions"><button type="button" class="btn" data-focus-tty="' + esc(last.tty) + '">Focus tab</button></div>');
              return;
            }
            api(path, { repo: repo, number: number, options: opts }).then(function (res) {
              if (res.ok) { info("Started", "<p>Opened in <b>" + esc(res.tty) + "</b>.</p>"); refreshAll(false); }
              else if (res.dry_run) info("Dry run", "<p>The server runs in dry-run mode, so no tab was opened.</p><pre>" + esc(res.would_run) + "</pre>");
              else info("Failed", '<div class="err">' + esc(res.error || "unknown error") + "</div>");
            }).catch(function (e) { info("Failed", '<div class="err">' + esc(e.message) + "</div>"); });
          });
        syncGroups();
        var box = $(".ck");
        box.addEventListener("change", function (ev) {
          if (ev.target.classList.contains("ck-all")) {
            ev.target.closest(".ck-g").querySelectorAll("input[type=checkbox][data-k]:not(:disabled)").forEach(function (b) { b.checked = ev.target.checked; });
          }
          syncGroups();
          clearTimeout(timer); timer = setTimeout(preview, 300);
        });
        box.addEventListener("input", function (ev) { if (ev.target.type === "text" || ev.target.type === "number") { clearTimeout(timer); timer = setTimeout(preview, 500); } });
        var rs = $("#ck-reset");
        if (rs) rs.addEventListener("click", function (ev) {
          ev.preventDefault();
          api("/api/checklist/reset", { repo: repo, number: number, kind: ck }).then(load);
        });
        preview();
      }).catch(function (e) { info("Failed", '<div class="err">' + esc(e.message) + "</div>"); });
    };
    load();
  }

  // A repo + PR picker inside a dialog. Search matches number, title and author; a number that is
  // not in the open list can still be used. onPick(repo, number) runs on double-click.
  function pickerHtml(idp) {
    var opts = CFG.repos.map(function (r) { return "<option>" + r + "</option>"; }).join("");
    return '<label>Repo</label><select id="' + idp + '-repo">' + opts + "</select>" +
      '<label>PR</label><input id="' + idp + '-q" type="search" placeholder="Search number, title or author" style="width:100%">' +
      '<div id="' + idp + '-list" class="nr-list"><span class="spin"></span></div>';
  }
  function makePicker(idp, opt) {
    opt = opt || {};
    var data = [], picked = null;
    var draw = function () {
      var q = ($("#" + idp + "-q").value || "").trim().toLowerCase();
      var rows = data.filter(function (p) { return !q || (p.number + " " + p.title + " " + p.author).toLowerCase().indexOf(q) >= 0; });
      var h = rows.map(function (p) {
        var off = opt.blockSession && p.has_session;
        var note = p.has_session ? chip(opt.blockSession ? "Has a session: use Re-review" : "Tracked", "") : p.tracked ? chip("Tracked", "") : "";
        return '<div class="nr-row' + (off ? " nr-off" : "") + (picked === p.number ? " on" : "") + '" data-nr="' + p.number + '">' +
          '<span class="mono">#' + p.number + "</span> <b>" + esc(p.title) + "</b>" +
          '<div class="muted small">' + esc(p.author) + " · into " + esc(p.base) + " · updated " + ago(p.updated) + (p.draft ? " · draft" : "") + " " +
          (p.requested_me ? chip("Review asked", "you") : "") + note + "</div></div>";
      }).join("");
      if (/^\d+$/.test(q) && !data.some(function (p) { return String(p.number) === q; }))
        h = '<div class="nr-row' + (picked === +q ? " on" : "") + '" data-nr="' + q + '"><span class="mono">#' + q + "</span> <b>Use this number</b>" +
          '<div class="muted small">Not in the open list (closed, or older than the newest 100)</div></div>' + h;
      $("#" + idp + "-list").innerHTML = h || '<p class="muted small pad">No open PRs match. Type a number to use any PR.</p>';
      if (opt.onChange) opt.onChange(picked);
    };
    var load = function () {
      $("#" + idp + "-list").innerHTML = '<span class="spin"></span>';
      api("/api/openprs/" + $("#" + idp + "-repo").value).then(function (j) { data = j.prs; draw(); })
        .catch(function (e) { $("#" + idp + "-list").innerHTML = '<div class="err">' + esc(e.message) + "</div>"; });
    };
    if (opt.repo) $("#" + idp + "-repo").value = opt.repo;
    $("#" + idp + "-repo").addEventListener("change", function () { picked = null; load(); });
    $("#" + idp + "-q").addEventListener("input", draw);
    var list = $("#" + idp + "-list");
    list.addEventListener("click", function (ev) {
      var r = ev.target.closest("[data-nr]");
      if (!r || r.classList.contains("nr-off")) return;
      picked = +r.dataset.nr; draw();
    });
    list.addEventListener("dblclick", function (ev) {
      var r = ev.target.closest("[data-nr]");
      if (!r || r.classList.contains("nr-off")) return;
      picked = +r.dataset.nr; draw();
      $("#dlg-ok").click();
    });
    load();
    return { repo: function () { return $("#" + idp + "-repo").value; }, number: function () { return picked; } };
  }

  function newReviewDialog() {
    var pk;
    dialog("New review", pickerHtml("nr") + '<p class="muted small">Next you see the brief and the command before anything runs.</p>', "Next", function () {
      var repo = pk.repo(), num = pk.number();
      if (!num) return;
      setTimeout(function () { previewAndRun("new", repo, num); }, 0);
    });
    pk = makePicker("nr", { blockSession: true, onChange: function (n) { $("#dlg-ok").disabled = !n; } });
  }

  // Linked PRs: one named group per feature, across repos. Stored in the registry only.
  function linkDialog(repo, number, preset) {
    var d = state.detail, g = d.links && d.links.group;
    var defName = (g && g.name) || (d.header.branch || "").replace(/^(feat|fix|chore|epic)\//, "");
    var pk;
    dialog("Link a PR to #" + number,
      '<label>Feature name</label><input id="lk-name" style="width:100%" value="' + esc(defName) + '">' +
      '<p class="muted small">PRs with the same feature name form one group. A PR is in one group at most.</p>' +
      (preset ? "<p>Link <b>" + esc(preset.repo) + " #" + preset.number + "</b> " + esc(preset.title || "") + "</p>" : pickerHtml("lk")),
      "Link", function () {
        var name = $("#lk-name").value;
        var wr = preset ? preset.repo : pk.repo(), wn = preset ? preset.number : pk.number();
        if (!wn) return;
        api("/api/action/link", { repo: repo, number: number, name: name, with_repo: wr, with_number: wn }).then(function (r) {
          if (!r.ok) { info("Cannot link", '<div class="err">' + esc(r.error) + "</div>"); return; }
          loadList(); loadPR(state.cur, true);
        });
      });
    if (!preset) pk = makePicker("lk", { repo: CFG.repos.filter(function (r) { return r !== repo; })[0] || repo, onChange: function (n) { $("#dlg-ok").disabled = !n; } });
  }
  function linksHtml(d) {
    var L = d.links || {}, g = L.group, s = d.summary, h = "";
    if (!d.tracked && !g && !(L.suggested || []).length) return "";
    var rows = g ? g.prs.map(function (x) {
      var st = x.state === "MERGED" ? chip("Merged", "purple") : x.state === "CLOSED" ? chip("Closed", "") : "";
      return '<a class="lk-row" href="#/' + esc(x.repo) + "/" + x.number + '"><span class="mono">' + esc(x.repo) + " #" + x.number + "</span> " + esc(x.title) +
        " " + (x.tracked ? verdictChip(x.verdict, x.verdict_label) : chip("Not tracked", "")) + st + "</a>";
    }).join("") : "";
    var sugg = (L.suggested || []).map(function (x) {
      return '<div class="lk-row"><span class="mono">' + esc(x.repo) + " #" + x.number + "</span> " + esc(x.title) +
        ' <span class="muted small">(' + esc(x.why) + ')</span> <button class="btn btn-sm" data-link-sugg="' + esc(x.repo) + "/" + x.number + '" data-title="' + esc(x.title) + '">Link</button></div>';
    }).join("");
    h += '<div class="box lk"><div class="box-h"><b>Linked PRs</b>' + (g ? chip(esc(g.name), "blue") : '<span class="muted small">none yet</span>') +
      '<span style="margin-left:auto"></span><button class="btn btn-sm" data-link-add>Link a PR</button>' +
      (g ? '<button class="btn btn-sm" data-unlink>Unlink this PR</button>' : "") + "</div>" +
      (rows || sugg ? '<div class="box-b">' + rows + (sugg ? '<div class="muted small" style="margin-top:' + (rows ? "8px" : "0") + '">Suggested</div>' + sugg : "") + "</div>" : "") +
      "</div>";
    return h;
  }
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-link-add],[data-unlink],[data-link-sugg]");
    if (!t) return;
    ev.preventDefault();
    var s = state.detail.summary;
    if (t.dataset.linkSugg) {
      var p = t.dataset.linkSugg.split("/");
      linkDialog(s.repo, s.number, { repo: p[0], number: +p[1], title: t.dataset.title });
    } else if (t.hasAttribute("data-link-add")) linkDialog(s.repo, s.number);
    else dialog("Unlink", "<p>Take #" + s.number + " out of its linked group?</p>", "Unlink", function () {
      api("/api/action/unlink", { repo: s.repo, number: s.number }).then(function () { loadList(); loadPR(state.cur, true); });
    });
  });

  function refreshAll(force) {
    var btn = $("#btn-refresh");
    if (force) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Refreshing'; }
    var p = force ? api("/api/refresh", {}) : Promise.resolve({});
    return p.then(function (r) {
      if (r.ok === false) info("Refresh", "<p>" + esc(r.error) + "</p>");
      return loadList();
    }).then(function () { if (state.cur) return loadPR(state.cur, true); })
      .catch(function (e) { info("Refresh failed", '<div class="err">' + esc(e.message) + "</div>"); })
      .then(function () { btn.disabled = false; btn.textContent = "Refresh"; });
  }

  document.addEventListener("click", function (ev) {
    if (ev.target.closest("[data-address]")) { ev.preventDefault(); addressDialog(); }
  });
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-my]");
    if (!t) return;
    ev.preventDefault();
    var s = state.detail.summary, act = t.dataset.my, sid = t.dataset.sid || "";
    var body = { repo: s.repo, number: s.number, session: sid };
    if (act === "focus") { api("/api/action/my_focus", body).then(function (r) { if (!r.ok && !r.dry_run) info("Focus failed", '<div class="err">' + esc(r.error) + "</div>"); }); return; }
    if (act === "pick") { api("/api/action/my_pick", body).then(function () { loadPR(state.cur, true); loadList(); }); return; }
    var path = act === "start" ? "/api/action/my_start" : "/api/action/my_resume";
    dialog(act === "start" ? "Start a session" : "Resume session", '<p><span class="spin"></span> Building the command…</p>', null);
    api(path, Object.assign({ dry_run: true }, body)).then(function (r) {
      if (r.session_open) {
        dialog("Session is open in " + esc(r.tty), "<p>Copy the prompt, then paste it in that tab.</p>" +
          '<div class="actions"><button type="button" class="btn" data-copy-prompt>Copy prompt</button>' +
          '<button type="button" class="btn" data-my="focus" data-sid="' + esc(sid) + '">Focus tab</button><span class="muted small" id="copied"></span></div>' +
          '<pre id="rr-prompt">' + esc(r.prompt) + "</pre>", null);
        return;
      }
      if (r.ok === false) { info("Cannot do that", '<div class="err">' + esc(r.error) + "</div>"); return; }
      dialog(act === "start" ? "Start a session on #" + s.number : "Resume session on #" + s.number,
        "<p>This opens a new iTerm tab. The session must ask you before it pushes, posts or resolves anything.</p>" +
        "<label>Command</label><pre>" + esc(r.tab_command) + "</pre><label>Prompt</label><pre>" + esc(r.prompt) + "</pre>", "Open tab and run", function () {
          api(path, body).then(function (res) {
            if (res.ok) { info("Started", "<p>Opened in <b>" + esc(res.tty) + "</b>.</p>"); loadPR(state.cur, true); loadList(); }
            else if (res.dry_run) info("Dry run", "<pre>" + esc(res.would_run) + "</pre>");
            else info("Failed", '<div class="err">' + esc(res.error || "unknown error") + "</div>");
          });
        });
    }).catch(function (e) { info("Failed", '<div class="err">' + esc(e.message) + "</div>"); });
  });
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-copy-prompt],[data-focus-tty]");
    if (!t) return;
    ev.preventDefault();
    if (t.dataset.focusTty !== undefined) {
      var s = state.detail && state.detail.summary;
      if (s) api("/api/action/focus", { repo: s.repo, number: s.number });
      return;
    }
    var text = $("#rr-prompt").textContent;
    var done = function () { $("#copied").textContent = "Copied"; };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () { $("#copied").textContent = "Select the text and copy it"; });
    else { $("#copied").textContent = "Select the text and copy it"; }
  });
  document.addEventListener("click", function (ev) {
    var rm = ev.target.closest("[data-remove]");
    if (!rm) return;
    ev.preventDefault(); ev.stopPropagation();
    var parts = rm.dataset.remove.split("/");
    dialog("Remove from list", "<p>Take <b>" + esc(parts[0]) + " #" + esc(parts[1]) + "</b> off the list?</p>" +
      '<p class="muted small">It stays in the registry, and nothing changes on GitHub. <code>review-ops unarchive ' + esc(parts[0]) + " " + esc(parts[1]) + "</code> brings it back.</p>", "Remove", function () {
      api("/api/action/remove", { repo: parts[0], number: +parts[1] }).then(function (r) {
        if (r.ok) {
          if (state.cur === rm.dataset.remove) { state.cur = null; state.detail = null; location.hash = ""; $("#main").innerHTML = '<div class="empty"><h2>Removed from the list</h2></div>'; }
          loadList();
        } else if (r.dry_run) info("Dry run", "<p>" + esc(r.would_do) + "</p>");
        else info("Cannot remove", '<div class="err">' + esc(r.error) + "</div>");
      }).catch(function (e) { info("Failed", '<div class="err">' + esc(e.message) + "</div>"); });
    });
  }, true);
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-tab],[data-act],[data-group],[data-file],[data-report]");
    if (!t) return;
    if (t.dataset.tab) { state.tab = t.dataset.tab; history.replaceState(null, "", "#/" + state.cur + "/" + state.tab); renderPR(); return; }
    if (t.dataset.group) {
      state.group = t.dataset.group; localStorage.setItem("ro-scope", state.group);
      document.querySelectorAll(".seg button").forEach(function (b) { b.classList.toggle("on", b === t); });
      renderSide(); return;
    }
    if (t.dataset.file) { ev.preventDefault(); openFile(t.dataset.file); return; }
    if (t.dataset.report) { state.report = t.dataset.report; renderTab(); return; }
    var s = state.detail && state.detail.summary;
    if (!s) return;
    var act = t.dataset.act;
    if (act === "rereview") previewAndRun("rereview", s.repo, s.number);
    else if (act === "new") previewAndRun("new", s.repo, s.number);
    else if (act === "focus") {
      dialog("Focus tab", "<p>Bring tab <b>" + esc(s.session.tty) + "</b> to the front?</p>", "Focus", function () {
        api("/api/action/focus", { repo: s.repo, number: s.number }).then(function (r) {
          if (!r.ok && !r.dry_run) info("Focus failed", '<div class="err">' + esc(r.error) + "</div>");
        });
      });
    } else if (act === "refresh-one") {
      t.disabled = true; t.innerHTML = '<span class="spin"></span> Refreshing';
      api("/api/refresh", { repo: s.repo, number: s.number }).then(function () { return loadList(); })
        .then(function () { return loadPR(state.cur, true); });
    }
  });
  $("#btn-refresh").addEventListener("click", function () { refreshAll(true); });
  (function () {  // "Wrap lines" toggle, kept in localStorage
    var on = localStorage.getItem("ro-wrap") === "1";
    document.body.classList.toggle("wrap-code", on);
    var lab = document.createElement("label");
    lab.className = "wrap-toggle";
    lab.innerHTML = '<input type="checkbox" id="wrap-code"' + (on ? " checked" : "") + "> Wrap lines";
    $(".top-actions").insertBefore(lab, $("#btn-new"));
    $("#wrap-code").addEventListener("change", function (e) {
      document.body.classList.toggle("wrap-code", e.target.checked);
      localStorage.setItem("ro-wrap", e.target.checked ? "1" : "0");
    });
  })();
  if (/[?&]checkwidth=1/.test(location.search)) {  // test hook: page width in the title
    setInterval(function () {
      var d = document.documentElement, m = $("#main");
      document.title = "W " + d.scrollWidth + "/" + d.clientWidth + " M " + (m ? m.scrollWidth + "/" + m.clientWidth : "");
      document.querySelectorAll("details.pf").forEach(function (x, i) { if (i < 3 && !x.open) x.open = true; });
    }, 1000);
  }
  $("#btn-new").addEventListener("click", newReviewDialog);
  $("#filter").addEventListener("input", function (e) { state.filter = e.target.value; renderSide(); });
  document.querySelectorAll(".seg button").forEach(function (b) { b.classList.toggle("on", b.dataset.group === state.group); });

  // Keyboard: / search, Esc leave search, Enter open first match, j/k next or previous PR, 1-5 tabs, r refresh this PR.
  function sideItems() { return [].slice.call(document.querySelectorAll("#side-list details[open] .item")); }
  document.addEventListener("keydown", function (ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var t = ev.target, typing = /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable;
    if ($("#dlg").open) return;
    if (t.id === "filter") {
      if (ev.key === "Escape") { t.value = ""; state.filter = ""; renderSide(); t.blur(); }
      else if (ev.key === "Enter" || ev.key === "ArrowDown") { var first = sideItems()[0]; if (first) { ev.preventDefault(); if (ev.key === "Enter") location.hash = first.getAttribute("href"); t.blur(); } }
      return;
    }
    if (typing) return;
    if (ev.key === "/") { ev.preventDefault(); $("#filter").focus(); $("#filter").select(); return; }
    if (ev.key === "j" || ev.key === "k") {
      var items = sideItems();
      if (!items.length) return;
      var i = items.findIndex(function (x) { return x.classList.contains("on"); });
      var nx = i < 0 ? 0 : Math.max(0, Math.min(items.length - 1, i + (ev.key === "j" ? 1 : -1)));
      location.hash = items[nx].getAttribute("href");
      return;
    }
    if (/^[1-9]$/.test(ev.key) && state.detail) {
      var tb = document.querySelectorAll(".tabs button[data-tab]")[+ev.key - 1];
      if (tb) tb.click();
      return;
    }
    if (ev.key === "r" && state.detail) { var rb = $('[data-act="refresh-one"]'); if (rb && !rb.disabled) rb.click(); }
  });

  function route() {
    var m = location.hash.match(/^#\/([\w-]+)\/(\d+)(?:\/(\w+))?/);
    if (m) { state.tab = m[3] || state.tab; loadPR(m[1] + "/" + m[2]); }
  }
  window.addEventListener("hashchange", route);
  loadList().then(route).catch(function (e) { $("#side-list").innerHTML = '<div class="err pad">' + esc(e.message) + "</div>"; });
  // Light auto-update: list every 60 s (GitHub stays cached for 3 min on the server).
  setInterval(function () { if (!document.hidden) loadList(); }, 60000);
})();
