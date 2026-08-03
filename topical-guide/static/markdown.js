/*
 * markdown.js — the notes renderer.
 *
 * Loaded two ways, which is why the export guard at the bottom exists:
 *   - by index.html as a plain <script>, defining window.renderMarkdown
 *   - by markdown.test.js under `node --test`, as a CommonJS module
 *
 * It must therefore stay free of DOM access and of index.html's globals —
 * including escapeHtml, which is duplicated here as esc() rather than shared,
 * since a second global of that name would collide with index.html's own
 * function declaration.
 *
 * The security property this file rests on: the ENTIRE source is HTML-escaped
 * up front, before a single markdown rule runs. Raw HTML typed into a note can
 * never reach the DOM because by then there are no '<' characters left. The
 * consequence is that block rules must match the ESCAPED text — '&gt;' for a
 * blockquote marker, never '>'.
 */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const BULLET_RE = /^(\s*)-\s+(.*)$/;
  const NUMBER_RE = /^(\s*)\d+\.\s+(.*)$/;
  const QUOTE_RE = /^\s*&gt;\s?/;          // '>' post-escape
  const HEADING_RE = /^(#{1,3})\s+(.*)$/;
  const RULE_RE = /^\s*---+\s*$/;

  // Only http(s) and in-page anchors become links. javascript: and data: URLs
  // execute; everything else is rendered as literal text instead. The '<'
  // check catches an href that an earlier inline rule injected a tag into —
  // see the ordering note in inline().
  function safeHref(href) {
    const trimmed = href.trim();
    if (/[<>]/.test(trimmed)) return null;
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    if (/^#/.test(trimmed)) return trimmed;
    return null;
  }

  function inline(text) {
    // Order matters: bold before italic. Run the other way, the italic rule
    // consumes the inner asterisks of **bold** and leaves a stray pair behind.
    let out = text;
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // The [^\s*] guard stops a lone asterisk pair separated by spaces
    // ("a * b * c") from italicising the text between them.
    out = out.replace(/(^|[^*])\*([^\s*][^*]*?)\*/g, "$1<em>$2</em>");
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, label, href) {
      const safe = safeHref(href);
      if (!safe) return match;
      return '<a href="' + safe + '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
    });
    return out;
  }

  function parseItem(line) {
    let m = line.match(BULLET_RE);
    if (m) return { ordered: false, indent: m[1].length, text: m[2] };
    m = line.match(NUMBER_RE);
    if (m) return { ordered: true, indent: m[1].length, text: m[2] };
    return null;
  }

  function isBlockStart(line) {
    return RULE_RE.test(line)
      || HEADING_RE.test(line)
      || QUOTE_RE.test(line)
      || parseItem(line) !== null;
  }

  // One level of nesting, by decision. Two or more leading spaces nests;
  // anything deeper collapses to that same one level rather than being
  // dropped. Returns [html, indexOfFirstLineAfterTheList].
  function renderList(lines, start) {
    const first = parseItem(lines[start]);
    const tag = first.ordered ? "ol" : "ul";
    const parts = ["<" + tag + ">"];
    let liOpen = false;
    let nestedTag = null;
    let i = start;

    while (i < lines.length) {
      const item = parseItem(lines[i]);
      if (!item) break;
      // A top-level item of the other kind ends this list and starts a new
      // one, rather than silently joining a numbered item to a bulleted list.
      if (item.indent < 2 && item.ordered !== first.ordered) break;

      if (item.indent >= 2) {
        // A nested list belongs INSIDE the <li> above it, which is why that
        // <li> is left open until the nesting closes.
        if (!liOpen) { parts.push("<li>"); liOpen = true; }
        if (!nestedTag) {
          nestedTag = item.ordered ? "ol" : "ul";
          parts.push("<" + nestedTag + ">");
        }
        parts.push("<li>" + inline(item.text.trim()) + "</li>");
      } else {
        if (nestedTag) { parts.push("</" + nestedTag + ">"); nestedTag = null; }
        if (liOpen) { parts.push("</li>"); liOpen = false; }
        parts.push("<li>" + inline(item.text.trim()));
        liOpen = true;
      }
      i++;
    }

    if (nestedTag) parts.push("</" + nestedTag + ">");
    if (liOpen) parts.push("</li>");
    parts.push("</" + tag + ">");
    return [parts.join(""), i];
  }

  function renderMarkdown(src) {
    const normalized = String(src == null ? "" : src).replace(/\r\n?/g, "\n");
    const lines = esc(normalized).split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (/^\s*$/.test(line)) { i++; continue; }

      if (RULE_RE.test(line)) { out.push("<hr>"); i++; continue; }

      const h = line.match(HEADING_RE);
      if (h) {
        const level = h[1].length;
        out.push("<h" + level + ">" + inline(h[2].trim()) + "</h" + level + ">");
        i++;
        continue;
      }

      if (QUOTE_RE.test(line)) {
        const buf = [];
        while (i < lines.length && QUOTE_RE.test(lines[i])) {
          buf.push(lines[i].replace(QUOTE_RE, ""));
          i++;
        }
        out.push("<blockquote>" + inline(buf.join(" ").trim()) + "</blockquote>");
        continue;
      }

      if (parseItem(line) !== null) {
        const result = renderList(lines, i);
        out.push(result[0]);
        i = result[1];
        continue;
      }

      // Paragraph: consecutive lines until a blank line or a block start.
      // Soft line breaks join with a space, the way markdown does it.
      const buf = [];
      while (i < lines.length && !/^\s*$/.test(lines[i]) && !isBlockStart(lines[i])) {
        buf.push(lines[i].trim());
        i++;
      }
      out.push("<p>" + inline(buf.join(" ")) + "</p>");
    }

    return out.join("\n");
  }

  global.renderMarkdown = renderMarkdown;
  // Inert in the browser, where `module` is undefined.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { renderMarkdown: renderMarkdown };
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
