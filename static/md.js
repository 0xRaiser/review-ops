// Small, safe markdown renderer: escapes all HTML first, then adds a limited set of tags.
(function () {
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function inline(s) {
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = esc(s);
    // images become links, so nothing loads from outside on its own
    s = s.replace(/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g, function (_, t, u) {
      return '<a href="' + u + '" target="_blank" rel="noopener">[image' + (t ? ": " + t : "") + "]</a>";
    });
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|[^)\s:]+)\)/g, function (_, t, u) {
      return '<a href="' + u + '" target="_blank" rel="noopener">' + t + "</a>";
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return "<code>" + esc(codes[+i]) + "</code>"; });
    return s;
  }
  function slug(s) { return s.toLowerCase().replace(/[^\w]+/g, "-").replace(/^-|-$/g, ""); }

  function render(src) {
    var lines = String(src || "").replace(/\r/g, "").split("\n");
    var out = [], i = 0, para = [];
    function flush() { if (para.length) { out.push("<p>" + inline(para.join(" ")) + "</p>"); para = []; } }
    while (i < lines.length) {
      var l = lines[i];
      var fence = l.match(/^\s*```(\w*)/);
      if (fence) {
        flush(); var buf = []; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>"); continue;
      }
      var h = l.match(/^(#{1,6})\s+(.*)$/);
      if (h) { flush(); var n = h[1].length; out.push("<h" + n + ' id="' + slug(h[2]) + '">' + inline(h[2]) + "</h" + n + ">"); i++; continue; }
      if (/^\s*(---|\*\*\*)\s*$/.test(l)) { flush(); out.push("<hr>"); i++; continue; }
      if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        flush();
        var row = function (r) { return r.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var hd = row(l); i += 2;
        var t = "<table><thead><tr>" + hd.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>";
        while (i < lines.length && /^\s*\|/.test(lines[i])) {
          t += "<tr>" + row(lines[i]).map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; i++;
        }
        out.push(t + "</tbody></table>"); continue;
      }
      if (/^\s*>/.test(l)) {
        flush(); var q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
        out.push("<blockquote>" + render(q.join("\n")) + "</blockquote>"); continue;
      }
      if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
        flush();
        var ordered = /^\s*\d+\./.test(l), items = [], cur = null;
        var baseIndent = l.match(/^\s*/)[0].length;
        while (i < lines.length) {
          var li = lines[i];
          var m = li.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
          if (m && m[1].length <= baseIndent + 1) { cur = [m[3]]; items.push(cur); i++; continue; }
          if (li.trim() === "") {
            if (i + 1 < lines.length && /^\s+\S/.test(lines[i + 1]) && cur) { cur.push(""); i++; continue; }
            break;
          }
          if (/^\s+/.test(li) && cur && cur.length === 1 && !/^\s*([-*+]|\d+\.)\s+/.test(li) && !/^\s*```/.test(li)) {
            cur[0] += " " + li.trim(); i++; continue;  // the item's text goes on over more lines
          }
          if (/^\s+/.test(li) && cur) { cur.push(li.replace(new RegExp("^\\s{0," + (baseIndent + 4) + "}"), "")); i++; continue; }
          break;
        }
        var tag = ordered ? "ol" : "ul";
        out.push("<" + tag + ">" + items.map(function (it) {
          if (it.length === 1) return "<li>" + inline(it[0]) + "</li>";
          var first = it[0], rest = it.slice(1).join("\n");
          return "<li>" + inline(first) + render(rest) + "</li>";
        }).join("") + "</" + tag + ">");
        continue;
      }
      if (l.trim() === "") { flush(); i++; continue; }
      para.push(l.trim()); i++;
    }
    flush();
    return out.join("\n");
  }
  window.MD = { render: render, esc: esc, inline: inline };
})();
