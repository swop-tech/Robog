/* Cross-file contract tests — the ones that catch wiring mistakes.
 *
 * `ui/` is a zero-build app: nothing compiles the HTML, CSS and JS together, so
 * a typo in an element id or a class name is invisible until a browser renders
 * it (and this sandbox has no browser). These tests close that gap statically:
 *
 *   1. the markers `tests/test_ui.py` pins, asserted from the JS side too, so a
 *      rewrite that breaks the Python contract fails here first and locally;
 *   2. every `$("id")` in app.js exists in index.html, and every element the
 *      tests require to start `hidden` really does;
 *   3. every class a template emits exists in styles.css (typos surface here);
 *   4. no absolute URLs, no API key, no secret-looking string anywhere in `ui/`.
 *
 *   node --test ui/tests/contract.test.js
 */

import assert from "node:assert/strict";
import test, { describe } from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { ICON_NAMES } from "../icons.js";

const UI = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (name) => readFileSync(join(UI, name), "utf8");

const html = read("index.html");
const appJs = read("app.js");
const css = read("styles.css");
const renderJs = read("render.js");

/** The four scenarios the starter chips must showcase (the judging rubric). */
const STARTER_PROMPTS = [
  "Wieviel Zuladung hat der FlashBot?",
  "Which robot fits a busy restaurant best?",
  "Qual è la velocità massima del FlashBot?",
  "Show me the component diagram of BellaBot",
];

describe("the serving contract tests/test_ui.py pins", () => {
  test("the page title starts with Robog", () => {
    assert.ok(html.includes("<title>Robog"), html.slice(0, 400));
  });

  test("all four starter prompts appear verbatim", () => {
    for (const prompt of STARTER_PROMPTS) assert.ok(html.includes(prompt), prompt);
  });

  test("the rubric's containers and markers are present", () => {
    for (const marker of ["citation-overlay", 'id="drawer"', "lightbox", "Raw <code>/search</code>"]) {
      assert.ok(html.includes(marker), marker);
    }
  });

  test("app.js keeps the markers the Python test greps for", () => {
    for (const marker of ["verify-badge", "Labels", "figure-labels", "cross-badge", "openDrawer"]) {
      assert.ok(appJs.includes(marker), marker);
    }
  });

  test("app.js calls the real endpoints and normalises figure URLs", () => {
    for (const marker of ['"/ask"', "/search", "/health", "mediaUrl"]) {
      assert.ok(appJs.includes(marker), marker);
    }
  });

  test("styles.css keeps the pinned selectors", () => {
    for (const marker of [".verify-badge", ".figure-labels", ".label-chip"]) {
      assert.ok(css.includes(marker), marker);
    }
    assert.match(css, /\[hidden\]\s*{\s*display:\s*none\s*!important/);
  });

  test("the page links the local zero-build assets as a module", () => {
    assert.ok(html.includes("/ui/styles.css"));
    assert.ok(html.includes("/ui/app.js"));
    assert.ok(html.includes('type="module"'));
  });

  test("no absolute URL anywhere in the page (no CDN, no icon font, no proxy hop)", () => {
    assert.ok(!html.includes("http://"), html.match(/http:\/\/[^\s"'<]*/)?.[0]);
    assert.ok(!html.includes("https://"), html.match(/https:\/\/[^\s"'<]*/)?.[0]);
  });
});

describe("DOM wiring: app.js and index.html agree", () => {
  const idsInApp = [...appJs.matchAll(/\$\("([a-z0-9-]+)"\)/g)].map((m) => m[1]);
  // The settings dialog is built at runtime, so its ids are declared in app.js.
  const idsInHtml = new Set(
    [...`${html}\n${appJs}`.matchAll(/id="([a-zA-Z0-9_-]+)"/g)].map((m) => m[1])
  );

  test("app.js looks up at least the main panels", () => {
    assert.ok(idsInApp.length >= 30, `only ${idsInApp.length} lookups found`);
  });

  test("every id app.js looks up exists in the page", () => {
    const missing = [...new Set(idsInApp)].filter((id) => !idsInHtml.has(id));
    assert.deepEqual(missing, [], `missing from index.html: ${missing.join(", ")}`);
  });

  test("dialogs and the toast start hidden, with id as the first attribute", () => {
    // Same regex tests/test_ui.py uses: `<tag id="x" ... hidden>`.
    for (const id of ["citation-overlay", "drawer", "drawer-backdrop", "lightbox", "toast", "chat"]) {
      const tag = html.match(new RegExp(`<[a-z]+ id="${id}"[^>]*>`));
      assert.ok(tag, `no tag with id="${id}"`);
      assert.ok(tag[0].includes(" hidden"), `${id} must start hidden: ${tag[0]}`);
    }
  });

  test("overlays are real dialogs with a modal role and a label", () => {
    for (const id of ["citation-overlay", "drawer", "lightbox", "settings-overlay"]) {
      const tag = html.match(new RegExp(`<[a-z]+ id="${id}"[^>]*>`))[0];
      assert.match(tag, /role="dialog"/, id);
      assert.match(tag, /aria-modal="true"/, id);
      assert.match(tag, /aria-label/, id);
    }
  });

  test("every overlay has a way to be closed", () => {
    for (const id of ["citation-overlay", "drawer", "lightbox", "settings-overlay"]) {
      const block = html.slice(html.indexOf(`id="${id}"`));
      assert.ok(block.slice(0, 1600).includes("data-close"), `${id} has no close control`);
    }
  });

  test("the composer has a label, a hint and a live char counter", () => {
    assert.match(html, /<label class="sr-only" for="input">/);
    assert.ok(html.includes('id="composer-hint"'));
    assert.ok(html.includes('aria-describedby="composer-hint"'));
    assert.ok(html.includes('id="char-count"'));
    assert.match(html, /<textarea id="input"[^>]*rows="1"/);
  });

  test("the send button starts disabled so an empty question cannot be sent", () => {
    assert.match(html, /<button class="send-btn" id="send"[^>]*disabled/);
  });

  test("a live region exists for screen-reader announcements", () => {
    assert.match(html, /id="live"[^>]*aria-live="polite"/);
  });

  test("there is a skip link and a noscript fallback", () => {
    assert.match(html, /class="skip-link" href="#input"/);
    assert.match(html, /<noscript>/);
  });
});

describe("icons: every requested glyph exists", () => {
  test("data-icon hosts in the page name real icons", () => {
    const requested = [...html.matchAll(/data-icon="([a-zA-Z]+)"/g)].map((m) => m[1]);
    assert.ok(requested.length >= 10, "the page should not be icon-free");
    const missing = [...new Set(requested)].filter((name) => !ICON_NAMES.includes(name));
    assert.deepEqual(missing, [], `unknown icons: ${missing.join(", ")}`);
  });

  test("icons named in app.js and render.js exist too", () => {
    const names = [...`${appJs}\n${renderJs}`.matchAll(/icon\(\s*"([a-zA-Z]+)"|ic\("([a-zA-Z]+)"/g)].map((m) => m[1] || m[2]);
    const missing = [...new Set(names)].filter((name) => !ICON_NAMES.includes(name));
    assert.deepEqual(missing, [], `unknown icons: ${missing.join(", ")}`);
  });
});

describe("styles: every class a template emits is styled", () => {
  /** Class tokens from `class="..."` literals, skipping interpolated ones. */
  function classTokens(source) {
    const tokens = new Set();
    for (const match of source.matchAll(/class="([^"$]*)"/g)) {
      for (const token of match[1].split(/\s+/)) {
        if (token && !token.includes("{")) tokens.add(token);
      }
    }
    return tokens;
  }

  test("render.js classes all have a rule in styles.css", () => {
    const missing = [...classTokens(renderJs)].filter((token) => !css.includes(`.${token}`));
    assert.deepEqual(missing, [], `unstyled: ${missing.join(", ")}`);
  });

  test("the classes app.js builds at runtime are styled too", () => {
    const missing = [...classTokens(appJs)].filter((token) => !css.includes(`.${token}`));
    assert.deepEqual(missing, [], `unstyled: ${missing.join(", ")}`);
  });

  test("index.html classes are styled too", () => {
    const missing = [...classTokens(html)].filter((token) => !css.includes(`.${token}`));
    assert.deepEqual(missing, [], `unstyled: ${missing.join(", ")}`);
  });
});

describe("no secrets, no invented backend", () => {
  const files = readdirSync(UI).filter((name) => /\.(js|css|html|json)$/.test(name));

  test("no API key material is embedded in any UI file", () => {
    for (const name of files) {
      const text = read(name);
      assert.ok(!/AIza[0-9A-Za-z_-]{10,}/.test(text), `${name} contains something that looks like a Gemini key`);
      assert.ok(!/(api[_-]?key|apikey|secret|token)\s*[:=]\s*["'][^"']{8,}["']/i.test(text), `${name} embeds a credential`);
      assert.ok(!/Authorization.*Bearer/i.test(text), `${name} sends an auth header`);
    }
  });

  test("the UI mentions the key only to say it stays on the server", () => {
    assert.match(appJs, /API key stays on the server|GEMINI_API_KEY on the server/);
  });

  test("no endpoint outside the OpenAPI list is referenced", () => {
    const allowed = ["/ask", "/search", "/health", "/products", "/media", "/docs", "/ui", "/openapi.json"];
    const paths = new Set([...appJs.matchAll(/["'`](\/[a-z][a-z0-9_{}/.-]*)["'`]/g)].map((m) => m[1]));
    const unexpected = [...paths].filter((path) => !allowed.some((ok) => path.startsWith(ok)));
    assert.deepEqual(unexpected, [], `unknown paths: ${unexpected.join(", ")}`);
  });

  test("ui/ ships no build artefacts and no dependencies", () => {
    const pkg = JSON.parse(read("package.json"));
    assert.equal(pkg.type, "module");
    assert.deepEqual(pkg.dependencies ?? {}, {});
    assert.deepEqual(pkg.devDependencies ?? {}, {});
    for (const name of files) assert.ok(!/\.min\.(js|css)$/.test(name), `${name} looks generated`);
  });

  test("every module parses as an ES module", () => {
    // `node --check` on each file is run by `npm run check`; here we at least
    // assert the import graph resolves to files that exist.
    for (const name of files.filter((f) => f.endsWith(".js"))) {
      const text = read(name);
      for (const match of text.matchAll(/from "\.\/([a-z-]+\.js)"/g)) {
        assert.ok(files.includes(match[1]), `${name} imports ./${match[1]} which does not exist`);
      }
    }
  });
});
