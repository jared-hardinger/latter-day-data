/*
 * markdown.test.js — exact-output tests for the notes renderer.
 *
 * Run with:  node --test topical-guide/static/
 *
 * Uses only the built-in node:test and node:assert. No npm packages, no
 * package.json, no node_modules, no build step. Every expectation below is an
 * exact string match: the renderer is a hand-rolled parser, and "contains a
 * <ul>" would pass on output that is quietly malformed.
 */

const test = require("node:test");
const assert = require("node:assert");
const { renderMarkdown } = require("./markdown.js");

test("empty and whitespace-only input render nothing", () => {
  assert.strictEqual(renderMarkdown(""), "");
  assert.strictEqual(renderMarkdown("   \n\n  "), "");
});

test("null and undefined render nothing rather than throwing", () => {
  assert.strictEqual(renderMarkdown(null), "");
  assert.strictEqual(renderMarkdown(undefined), "");
});

test("paragraphs", () => {
  assert.strictEqual(renderMarkdown("one two"), "<p>one two</p>");
  // A soft wrap joins with a space, the way markdown does it.
  assert.strictEqual(renderMarkdown("one\ntwo"), "<p>one two</p>");
  assert.strictEqual(renderMarkdown("a\n\nb"), "<p>a</p>\n<p>b</p>");
});

test("headings, three levels", () => {
  assert.strictEqual(
    renderMarkdown("# a\n## b\n### c"),
    "<h1>a</h1>\n<h2>b</h2>\n<h3>c</h3>"
  );
});

test("four hashes is not a heading", () => {
  assert.strictEqual(renderMarkdown("#### a"), "<p>#### a</p>");
});

test("bold and italic", () => {
  assert.strictEqual(renderMarkdown("**x**"), "<p><strong>x</strong></p>");
  assert.strictEqual(renderMarkdown("*x*"), "<p><em>x</em></p>");
  assert.strictEqual(
    renderMarkdown("## a **b**"),
    "<h2>a <strong>b</strong></h2>"
  );
});

test("unclosed bold stays literal and does not swallow the document", () => {
  assert.strictEqual(renderMarkdown("**x"), "<p>**x</p>");
});

test("asterisks separated by spaces do not italicise", () => {
  const html = renderMarkdown("a * b * c");
  assert.strictEqual(html, "<p>a * b * c</p>");
  assert.ok(!html.includes("<em>"));
});

test("bulleted lists", () => {
  assert.strictEqual(
    renderMarkdown("- a\n- b"),
    "<ul><li>a</li><li>b</li></ul>"
  );
});

test("one level of nesting sits inside the item above it", () => {
  assert.strictEqual(
    renderMarkdown("- a\n  - b\n- c"),
    "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>"
  );
});

test("deeper indentation collapses to the same one level", () => {
  assert.strictEqual(
    renderMarkdown("- a\n    - b"),
    "<ul><li>a<ul><li>b</li></ul></li></ul>"
  );
});

test("numbered lists", () => {
  assert.strictEqual(
    renderMarkdown("1. a\n2. b"),
    "<ol><li>a</li><li>b</li></ol>"
  );
  // The numbers written are ignored; <ol> supplies its own.
  assert.strictEqual(
    renderMarkdown("4. a\n5. b"),
    "<ol><li>a</li><li>b</li></ol>"
  );
});

test("a numbered item after a bullet starts a separate list", () => {
  assert.strictEqual(
    renderMarkdown("- a\n1. b"),
    "<ul><li>a</li></ul>\n<ol><li>b</li></ol>"
  );
});

test("blockquotes", () => {
  assert.strictEqual(renderMarkdown("> a"), "<blockquote>a</blockquote>");
  assert.strictEqual(renderMarkdown("> a\n> b"), "<blockquote>a b</blockquote>");
  assert.strictEqual(
    renderMarkdown("> a\n\n> b"),
    "<blockquote>a</blockquote>\n<blockquote>b</blockquote>"
  );
});

test("horizontal rules", () => {
  assert.strictEqual(renderMarkdown("---"), "<hr>");
  assert.strictEqual(renderMarkdown("a\n---"), "<p>a</p>\n<hr>");
});

test("links", () => {
  assert.strictEqual(
    renderMarkdown("[a](https://x.com)"),
    '<p><a href="https://x.com" target="_blank" rel="noopener noreferrer">a</a></p>'
  );
  assert.strictEqual(
    renderMarkdown("[a](#b)"),
    '<p><a href="#b" target="_blank" rel="noopener noreferrer">a</a></p>'
  );
});

test("CRLF input renders identically to LF", () => {
  assert.strictEqual(renderMarkdown("a\r\nb"), renderMarkdown("a\nb"));
});

test("entities in prose are escaped", () => {
  assert.strictEqual(renderMarkdown("a & b"), "<p>a &amp; b</p>");
  assert.strictEqual(renderMarkdown('say "hi"'), "<p>say &quot;hi&quot;</p>");
});

// ---------------------------------------------------------------------------
// Injection — these three must never regress. The renderer escapes its entire
// input before any markdown rule runs, and refuses every href scheme but
// http(s) and in-page anchors.
// ---------------------------------------------------------------------------

test("raw HTML in a note renders as visible text, never as markup", () => {
  const html = renderMarkdown("<script>alert(1)</script>");
  assert.strictEqual(html, "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>");
  assert.ok(!html.includes("<script>"));
});

test("a javascript: link never becomes an anchor", () => {
  const html = renderMarkdown("[a](javascript:alert(1))");
  assert.strictEqual(html, "<p>[a](javascript:alert(1))</p>");
  assert.ok(!html.includes("<a"));
  assert.ok(!html.includes("href="));
});

test("a data: link never becomes an anchor", () => {
  const html = renderMarkdown("[a](data:text/html,x)");
  assert.strictEqual(html, "<p>[a](data:text/html,x)</p>");
  assert.ok(!html.includes("<a"));
  assert.ok(!html.includes("href="));
});
