/* Contract tests for local state (conversations, settings) and the theme.
 *
 * The point of this file is the honesty boundary: `POST /ask` is stateless, so
 * everything here is *browser* state. These tests pin that (a) settings map only
 * onto fields `AskRequest` really declares, (b) nothing that would be expensive
 * or misleading to restore (the raw `/search` body) is persisted, and (c) broken
 * or full storage degrades instead of breaking the app.
 *
 *   node --test ui/tests/store.test.js
 */

import assert from "node:assert/strict";
import test, { describe } from "node:test";

import {
  DEFAULT_SETTINGS,
  MAX_CONVERSATIONS,
  STORAGE_KEY,
  activeConversation,
  addMessage,
  askOptions,
  clearConversations,
  createStore,
  deleteConversation,
  emptyState,
  hydrate,
  newConversation,
  newId,
  persistableMessage,
  removeMessage,
  renameConversation,
  searchConversations,
  selectConversation,
  serialize,
  setFeedback,
  titleFrom,
  updateMessage,
  updateSettings,
} from "../store.js";
import { resolveTheme, THEMES } from "../theme.js";

/** A `localStorage` double, with an injectable quota failure. */
function memoryStorage({ failAfter = Infinity } = {}) {
  const map = new Map();
  let writes = 0;
  return {
    map,
    get length() { return map.size; },
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem(key, value) {
      if (++writes > failAfter) {
        const error = new Error("QuotaExceededError");
        error.name = "QuotaExceededError";
        throw error;
      }
      map.set(key, String(value));
    },
    removeItem: (key) => map.delete(key),
    clear: () => map.clear(),
  };
}

const userMessage = (text, id = "u-1") => ({ id, role: "user", question: text, text, createdAt: "2026-11-14T09:00:00.000Z" });
const botMessage = (id = "a-1", extra = {}) => ({
  id, role: "assistant", question: "Wieviel Zuladung hat der FlashBot?", createdAt: "2026-11-14T09:00:01.000Z",
  payload: { answer: "20 kg [1].", route: "lookup", citations: [{ n: 1 }] }, ...extra,
});

describe("settings -> AskRequest", () => {
  test("the defaults send the API's own defaults, not overrides", () => {
    assert.deepEqual(askOptions(DEFAULT_SETTINGS), {
      k: 8, follow_user_language: true, cross_lingual_fallback: true,
    });
  });

  test("empty language / product / provider are omitted so the server decides", () => {
    const opts = askOptions({ ...DEFAULT_SETTINGS, language: "", product: "", provider: "" });
    assert.ok(!("language" in opts));
    assert.ok(!("product" in opts));
    assert.ok(!("provider" in opts));
  });

  test("set values are forwarded under the API's field names", () => {
    assert.deepEqual(
      askOptions({ ...DEFAULT_SETTINGS, language: "it", product: "FlashBot", provider: "gemini", k: 12 }),
      { k: 12, language: "it", product: "FlashBot", provider: "gemini", follow_user_language: true, cross_lingual_fallback: true }
    );
  });

  test("a switched-off policy becomes an explicit false", () => {
    const opts = askOptions({ ...DEFAULT_SETTINGS, followUserLanguage: false, crossLingualFallback: false });
    assert.equal(opts.follow_user_language, false);
    assert.equal(opts.cross_lingual_fallback, false);
  });

  test("no field outside AskRequest leaks into the request", () => {
    const opts = askOptions({ ...DEFAULT_SETTINGS, theme: "dark", autoScroll: false });
    assert.deepEqual(Object.keys(opts).sort(), ["cross_lingual_fallback", "follow_user_language", "k"]);
  });

  test("k is coerced and clamped to the API's 1..50 range", () => {
    assert.equal(askOptions({ ...DEFAULT_SETTINGS, k: "12" }).k, 12);
    assert.equal(updateSettings(emptyState(), { k: 999 }).settings.k, 50);
    assert.equal(updateSettings(emptyState(), { k: 0 }).settings.k, 8);
    assert.equal(updateSettings(emptyState(), { k: null }).settings.k, 8);
  });
});

describe("updateSettings", () => {
  test("unknown keys are ignored rather than stored", () => {
    const next = updateSettings(emptyState(), { theme: "dark", apiKey: "AIza-secret", admin: true });
    assert.equal(next.settings.theme, "dark");
    assert.ok(!("apiKey" in next.settings));
    assert.ok(!("admin" in next.settings));
  });

  test("state is never mutated in place", () => {
    const state = emptyState();
    const next = updateSettings(state, { theme: "dark" });
    assert.equal(state.settings.theme, "system");
    assert.notEqual(next.settings, state.settings);
  });
});

describe("conversations", () => {
  test("the first question creates a conversation and titles itself", () => {
    const state = addMessage(emptyState(), userMessage("Wieviel Zuladung hat der FlashBot?"));
    const conv = activeConversation(state);
    assert.ok(conv);
    assert.equal(conv.title, "Wieviel Zuladung hat der FlashBot?");
    assert.equal(conv.messages.length, 1);
  });

  test("a later question does not rename the conversation", () => {
    let state = addMessage(emptyState(), userMessage("First question"));
    state = addMessage(state, userMessage("Second question", "u-2"));
    assert.equal(activeConversation(state).title, "First question");
  });

  test("titles are truncated with an ellipsis, never mid-word garbage", () => {
    assert.equal(titleFrom("x".repeat(80)).length, 46);
    assert.ok(titleFrom("x".repeat(80)).endsWith("…"));
    assert.equal(titleFrom("   "), "New conversation");
    assert.equal(titleFrom(null), "New conversation");
  });

  test("new conversation is prepended and becomes active", () => {
    let state = addMessage(emptyState(), userMessage("One"));
    state = newConversation(state);
    assert.equal(state.conversations.length, 2);
    assert.equal(state.conversations[0].id, state.activeId);
    assert.equal(activeConversation(state).messages.length, 0);
  });

  test("the conversation list is capped, oldest dropped", () => {
    let state = emptyState();
    for (let i = 0; i < MAX_CONVERSATIONS + 5; i += 1) state = newConversation(state);
    assert.equal(state.conversations.length, MAX_CONVERSATIONS);
  });

  test("selecting an unknown id is a no-op", () => {
    const state = addMessage(emptyState(), userMessage("q"));
    assert.deepEqual(selectConversation(state, "nope"), state);
  });

  test("deleting the active conversation falls back to the newest remaining one", () => {
    let state = addMessage(emptyState(), userMessage("One"));
    const firstId = state.activeId;
    state = newConversation(state);
    state = addMessage(state, userMessage("Two"));
    const secondId = state.activeId;
    state = deleteConversation(state, secondId);
    assert.equal(state.activeId, firstId);
    assert.equal(state.conversations.length, 1);
  });

  test("deleting the last conversation leaves an empty, usable state", () => {
    let state = addMessage(emptyState(), userMessage("One"));
    state = deleteConversation(state, state.activeId);
    assert.equal(state.conversations.length, 0);
    assert.equal(state.activeId, null);
    assert.equal(activeConversation(state), null);
  });

  test("clearConversations empties everything but keeps settings", () => {
    let state = updateSettings(addMessage(emptyState(), userMessage("One")), { theme: "dark" });
    state = clearConversations(state);
    assert.equal(state.conversations.length, 0);
    assert.equal(state.settings.theme, "dark");
  });

  test("renameConversation sanitises the title", () => {
    let state = addMessage(emptyState(), userMessage("One"));
    state = renameConversation(state, state.activeId, "  FlashBot   payload  ");
    assert.equal(activeConversation(state).title, "FlashBot payload");
  });
});

describe("messages", () => {
  test("updateMessage replaces only the fields it is given", () => {
    let state = addMessage(emptyState(), userMessage("q"));
    state = addMessage(state, botMessage("a-1", { pending: true }));
    const convId = state.activeId;
    state = updateMessage(state, convId, "a-1", { pending: false, payload: { answer: "20 kg" } });
    const msg = activeConversation(state).messages[1];
    assert.equal(msg.pending, false);
    assert.equal(msg.payload.answer, "20 kg");
    assert.equal(msg.role, "assistant");
  });

  test("updating a message in another conversation touches nothing", () => {
    let state = addMessage(emptyState(), userMessage("q"));
    state = addMessage(state, botMessage("a-1"));
    const before = state.conversations[0].messages[1];
    state = updateMessage(state, "other-conv", "a-1", { payload: null });
    assert.equal(state.conversations[0].messages[1], before);
  });

  test("removeMessage drops exactly one message", () => {
    let state = addMessage(emptyState(), userMessage("q"));
    state = addMessage(state, botMessage("a-1"));
    state = removeMessage(state, state.activeId, "a-1");
    assert.equal(activeConversation(state).messages.length, 1);
  });

  test("feedback toggles between up, down and null", () => {
    let state = addMessage(emptyState(), userMessage("q"));
    state = addMessage(state, botMessage("a-1"));
    const convId = state.activeId;
    state = setFeedback(state, convId, "a-1", "up");
    assert.equal(activeConversation(state).messages[1].feedback, "up");
    assert.ok(activeConversation(state).messages[1].feedbackAt);
    state = setFeedback(state, convId, "a-1", null);
    assert.equal(activeConversation(state).messages[1].feedback, null);
    assert.equal(activeConversation(state).messages[1].feedbackAt, null);
  });

  test("ids are unique enough for DOM lookup", () => {
    const ids = new Set(Array.from({ length: 500 }, () => newId("a")));
    assert.equal(ids.size, 500);
    assert.ok([...ids].every((id) => id.startsWith("a-")));
  });
});

describe("searchConversations — local filter, clearly local", () => {
  const state = () => {
    let s = addMessage(emptyState(), userMessage("Wieviel Zuladung hat der FlashBot?"));
    s = newConversation(s);
    s = addMessage(s, userMessage("Come si pulisce il filtro?"));
    return s;
  };

  test("an empty query returns everything", () => {
    assert.equal(searchConversations(state(), "").length, 2);
    assert.equal(searchConversations(state(), "   ").length, 2);
  });

  test("it matches titles and question text, case-insensitively", () => {
    assert.equal(searchConversations(state(), "flashbot").length, 1);
    assert.equal(searchConversations(state(), "FILTRO").length, 1);
    assert.equal(searchConversations(state(), "nothing matches").length, 0);
  });
});

describe("persistence", () => {
  test("the raw /search body is dropped before storage (re-fetched on demand)", () => {
    const stored = persistableMessage(botMessage("a-1", { search: { hits: [1, 2, 3] }, pending: false }));
    assert.equal(stored.search, undefined);
    assert.equal(stored.pending, undefined);
    assert.deepEqual(stored.payload.citations, [{ n: 1 }]);
  });

  test("include_search / include_evidence blobs are not persisted either", () => {
    const stored = persistableMessage(botMessage("a-1", { payload: { answer: "x", search: { big: true }, evidence: { big: true } } }));
    assert.equal(stored.payload.search, null);
    assert.equal(stored.payload.evidence, null);
    assert.equal(stored.payload.answer, "x");
  });

  test("serialize/hydrate round-trips conversations and settings", () => {
    let state = addMessage(emptyState(), userMessage("Wieviel Zuladung hat der FlashBot?"));
    state = addMessage(state, botMessage("a-1"));
    state = updateSettings(state, { theme: "dark", k: 12, language: "de" });
    const restored = hydrate(serialize(state));
    assert.equal(restored.settings.theme, "dark");
    assert.equal(restored.settings.k, 12);
    assert.equal(restored.activeId, state.activeId);
    assert.equal(restored.conversations[0].messages.length, 2);
    assert.equal(restored.conversations[0].messages[1].payload.answer, "20 kg [1].");
  });

  test("corrupt storage yields a fresh state instead of throwing", () => {
    for (const raw of ["{not json", "null", "[]", '"a string"', "", undefined]) {
      const state = hydrate(raw);
      assert.deepEqual(state.conversations, []);
      assert.deepEqual(state.settings, DEFAULT_SETTINGS);
    }
  });

  test("a stored blob from another schema version is discarded", () => {
    const state = hydrate(JSON.stringify({ version: 99, conversations: [{ id: "c", messages: [] }] }));
    assert.deepEqual(state.conversations, []);
  });

  test("malformed conversations are filtered, valid ones kept", () => {
    const raw = JSON.stringify({
      version: 1,
      settings: { theme: "dark" },
      activeId: "gone",
      conversations: [null, 42, { id: "ok", messages: [{ id: "m", role: "user", text: "hi" }] }, { id: "no-messages" }],
    });
    const state = hydrate(raw);
    assert.equal(state.conversations.length, 1);
    assert.equal(state.activeId, "ok");          // the stored activeId did not survive -> first valid
    assert.equal(state.settings.theme, "dark");
  });

  test("a missing settings block falls back to the defaults", () => {
    const state = hydrate(JSON.stringify({ version: 1, conversations: [] }));
    assert.deepEqual(state.settings, DEFAULT_SETTINGS);
  });
});

describe("createStore", () => {
  test("it writes through to storage on every change", () => {
    const storage = memoryStorage();
    const store = createStore(storage);
    store.set((state) => addMessage(state, userMessage("q")));
    assert.equal(storage.map.size, 1);
    assert.ok(storage.map.get(STORAGE_KEY).includes("q"));
  });

  test("it rehydrates from storage on construction", () => {
    const storage = memoryStorage();
    const first = createStore(storage);
    first.set((state) => addMessage(state, userMessage("persisted question")));
    const second = createStore(storage);
    assert.equal(activeConversation(second.state).messages[0].text, "persisted question");
  });

  test("without storage (private mode) it still works in memory", () => {
    const store = createStore(null);
    store.set((state) => addMessage(state, userMessage("q")));
    assert.equal(store.persisted, false);
    assert.equal(activeConversation(store.state).messages.length, 1);
  });

  test("a full quota drops the oldest conversation and warns, once", () => {
    const storage = memoryStorage({ failAfter: 2 });
    const warnings = [];
    const store = createStore(storage, { onWarn: (m) => warnings.push(m) });
    store.set((state) => addMessage(state, userMessage("one")));
    store.set((state) => newConversation(state));
    store.set((state) => addMessage(state, userMessage("two")));
    assert.ok(warnings.some((w) => /oldest saved conversation was removed|Could not save/.test(w)), warnings);
  });

  test("unreadable storage warns instead of breaking the app", () => {
    const storage = memoryStorage();
    storage.getItem = () => { throw new Error("SecurityError: denied"); };
    const warnings = [];
    const store = createStore(storage, { onWarn: (m) => warnings.push(m) });
    assert.deepEqual(store.state.conversations, []);
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /Could not read saved conversations/);
  });

  test("reset() clears state and storage", () => {
    const storage = memoryStorage();
    const store = createStore(storage);
    store.set((state) => addMessage(state, userMessage("q")));
    store.reset();
    assert.deepEqual(store.state.conversations, []);
    assert.equal(storage.map.size, 0);
  });
});

describe("theme", () => {
  test("the three preferences are the only ones offered", () => {
    assert.deepEqual(THEMES, ["light", "dark", "system"]);
  });

  test("light and dark are explicit; system follows the OS", () => {
    assert.equal(resolveTheme("light", true), "light");
    assert.equal(resolveTheme("dark", false), "dark");
    assert.equal(resolveTheme("system", true), "dark");
    assert.equal(resolveTheme("system", false), "light");
  });

  test("an unknown or missing preference is light (the primary theme)", () => {
    assert.equal(resolveTheme(undefined, false), "light");
    assert.equal(resolveTheme("neon", false), "light");
    assert.equal(DEFAULT_SETTINGS.theme, "system");
  });
});
