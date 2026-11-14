/* Contract tests for the markdown layer.
 *
 * The answer text comes from the corpus and from the formatter, so the renderer
 * has to be safe (everything escaped) and faithful to what the manuals actually
 * contain — including the awkward cases, e.g. `84*60*49 cm`, where a naive
 * `*italic*` rule would mangle a real dimension.
 *
 *   node --test ui/tests/markdown.test.js
 */

import assert from "node:assert/strict";
import test, { describe } from "node:test";

import { esc, escText, inline, plainText, renderMarkdown, highlightJson } from "../markdown.js";

describe("escaping", () => {
  test("HTML in a payload never reaches the DOM as markup", () => {
    assert.equal(esc(`<img src=x onerror=alert(1)>`), "&lt;img src=x onerror=alert(1)&gt;");
    assert.equal(esc(`a & b "c" 'd'`), "a &amp; b &quot;c&quot; &#39;d&#39;");
    assert.equal(esc(null), "");
    assert.equal(esc(undefined), "");
    assert.equal(esc(42), "42");
  });

  test("a hostile answer is rendered as text, not as an element", () => {
    const html = renderMarkdown(`<script>alert("xss")</script> and <b>bold</b>`);
    assert.ok(!html.includes("<script>"), html);
    assert.ok(!html.includes("<b>"), html);
    assert.ok(html.includes("&lt;script&gt;"), html);
  });

  test("escText keeps newlines, esc does not add any", () => {
    assert.equal(escText("a\nb"), "a\nb");
  });
});

describe("inline markup", () => {
  test("[n] becomes a clickable citation marker carrying its number", () => {
    const html = inline("Payload is 20 kg [1].");
    assert.match(html, /class="cite"[^>]*data-ref="1"/);
    assert.match(html, />\[1\]</);
  });

  test("a citation range keeps both numbers", () => {
    const html = inline("See [1][3] for details.");
    assert.equal((html.match(/class="cite"/g) || []).length, 2);
  });

  test("bold survives, a bare asterisk does not become emphasis", () => {
    assert.match(inline("**Zuladung** 20 kg"), /<strong>Zuladung<\/strong>/);
    // The corpus writes dimensions like this; italics here would be a lie.
    const html = inline("Dimensions 84*60*49 cm");
    assert.ok(!html.includes("<em>"), html);
    assert.ok(html.includes("84*60*49 cm"), html);
  });

  test("citations can be switched off (used for the copy-to-clipboard text)", () => {
    assert.ok(!inline("Payload 20 kg [1]", { citations: false }).includes('class="cite"'));
  });

  test("`code` is escaped inside the code element", () => {
    const html = inline("run `a < b` now");
    assert.match(html, /<code>a &lt; b<\/code>/);
  });
});

describe("blocks", () => {
  test("a pipe table becomes a real table with a scroll wrapper", () => {
    const html = renderMarkdown("| Model | Payload |\n| --- | --- |\n| FlashBot | 20 kg |");
    assert.match(html, /class="table-wrap"/);
    assert.match(html, /<th[^>]*>Model<\/th>/);
    assert.match(html, /<td[^>]*>20 kg<\/td>/);
  });

  test("a fenced code block keeps its content verbatim and escaped", () => {
    const html = renderMarkdown("```\npython -m ingest.run --data data\n```");
    assert.match(html, /class="code-block"/);
    assert.ok(html.includes("python -m ingest.run --data data"), html);
  });

  test("headings are demoted to styled paragraphs (one h1 per page)", () => {
    const html = renderMarkdown("## Charging time");
    assert.ok(!html.includes("<h2"), html);
    assert.match(html, /class="md-h md-h2"/);
  });

  test("lists keep their markers and nesting", () => {
    const html = renderMarkdown("1. Press the button\n2. Wait 0.5 s");
    assert.match(html, /<ol/);
    assert.equal((html.match(/<li/g) || []).length, 2);
  });

  test("a WARNING line from a manual becomes a callout, not plain text", () => {
    const html = renderMarkdown("WARNING: Do not lift the robot by the tray.");
    assert.match(html, /class="callout/);
    assert.match(html, /class="callout-kind">warning</);
    assert.ok(html.includes("Do not lift the robot by the tray."), html);
  });

  test("empty and non-string input render as an empty string", () => {
    assert.equal(renderMarkdown(""), "");
    assert.equal(renderMarkdown(null), "");
    assert.equal(renderMarkdown(undefined), "");
  });
});

describe("plainText", () => {
  test("markdown syntax is stripped for the clipboard", () => {
    assert.equal(plainText("**Zuladung** 20 kg [1]").trim(), "Zuladung 20 kg [1]");
    const table = plainText("| Model | Payload |\n| --- | --- |\n| FlashBot | 20 kg |");
    assert.ok(!table.includes("---"), table);          // the separator row is layout, not content
    assert.ok(!table.includes("|"), table);
    assert.ok(table.includes("FlashBot") && table.includes("20 kg"), table);
  });
});

describe("highlightJson", () => {
  test("keys, strings, numbers, booleans and null get distinct classes", () => {
    const html = highlightJson({ route: "lookup", k: 8, ready: true, image: null });
    assert.match(html, /class="j-key">"route":/);
    assert.match(html, /class="j-str">"lookup"/);
    assert.match(html, /class="j-num">8/);
    assert.match(html, /class="j-bool">true/);
    assert.match(html, /class="j-null">null/);
  });

  test("a value containing markup is escaped before it is coloured", () => {
    const html = highlightJson({ answer: "<script>x</script>" });
    assert.ok(!html.includes("<script>x"), html);
    assert.ok(html.includes("&lt;script&gt;"), html);
  });

  test("an error body is shown as an escaped comment, not thrown", () => {
    const circular = {};
    circular.self = circular;
    assert.doesNotThrow(() => highlightJson(circular));
  });
});
