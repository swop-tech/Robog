/* Local state: conversations, settings and feedback.
 *
 * IMPORTANT AND DELIBERATE: `POST /ask` is stateless — the API has no
 * conversations, no history and no feedback endpoint (see `CAPABILITIES` in
 * api.js and `docs/ui-backend-gaps.md`). Everything in this module therefore
 * lives in `localStorage` in *this browser only*, and the UI says so wherever it
 * is shown (sidebar footnote, settings, feedback toast). Nothing here is
 * presented as server state, and nothing is invented: a restored conversation
 * re-renders exactly what the API returned before, and its raw-`/search` drawer
 * starts empty until the user re-runs the live call.
 *
 * Pure logic over a plain state object with an injected storage adapter, so it
 * is unit-testable under `node --test ui/tests/` with no DOM and no browser.
 */

export const STORAGE_KEY = "telmekon.ui.v1";
export const SCHEMA_VERSION = 1;

/** 25 conversations × a few answers each stays well inside the ~5 MB quota. */
export const MAX_CONVERSATIONS = 25;

export const DEFAULT_SETTINGS = Object.freeze({
  theme: "system",              // "light" | "dark" | "system"
  language: "",                 // "" = detect from the question (Bonus 02 default)
  k: 8,                         // AskRequest.k — evidence depth
  followUserLanguage: true,     // false = the naive all-languages baseline
  crossLingualFallback: true,
  product: "",                  // "" = whole catalog
  provider: "",                 // "" = server default (Gemini when a key exists)
  autoScroll: true,
});

/** Settings that map 1:1 onto `AskRequest` fields, and nothing else. */
export function askOptions(settings) {
  const opts = {
    k: Number(settings.k) || DEFAULT_SETTINGS.k,
    follow_user_language: settings.followUserLanguage !== false,
    cross_lingual_fallback: settings.crossLingualFallback !== false,
  };
  if (settings.language) opts.language = settings.language;
  if (settings.product) opts.product = settings.product;
  if (settings.provider) opts.provider = settings.provider;
  return opts;
}

export const emptyState = () => ({
  version: SCHEMA_VERSION,
  settings: { ...DEFAULT_SETTINGS },
  conversations: [],
  activeId: null,
});

const nowIso = () => new Date().toISOString();

/** A short, honest id — no dependency, no crypto requirement. */
export function newId(prefix = "c") {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Conversation titles come from the first question, never from invented text. */
export function titleFrom(question, limit = 46) {
  const clean = String(question || "").replace(/\s+/g, " ").trim();
  if (!clean) return "New conversation";
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
}

/**
 * Drop what only matters for a live render: the raw `/search` body is re-fetched
 * on demand ("Re-run live"), and re-storing it would burn the storage quota.
 */
export function persistableMessage(message) {
  const { search, pending, ...rest } = message;
  const payload = rest.payload ? { ...rest.payload, search: null, evidence: null } : undefined;
  return { ...rest, payload };
}

// ---------------------------------------------------------------------------
// reducers — every one returns a new state, so persistence is a single call
// ---------------------------------------------------------------------------
export function updateSettings(state, patch) {
  const settings = { ...state.settings };
  for (const [key, value] of Object.entries(patch || {})) {
    if (key in DEFAULT_SETTINGS) settings[key] = value;   // unknown keys are ignored, not stored
  }
  // k always ends up a usable integer in AskRequest's 1..50 range: a stored
  // null/NaN would otherwise reach the <input type="range"> as `value="null"`.
  const k = Number(settings.k);
  settings.k = Number.isFinite(k) && k > 0 ? Math.min(50, Math.max(1, Math.round(k))) : DEFAULT_SETTINGS.k;
  return { ...state, settings };
}

export function newConversation(state) {
  const conversation = { id: newId(), title: "New conversation", createdAt: nowIso(), updatedAt: nowIso(), messages: [] };
  const conversations = [conversation, ...state.conversations].slice(0, MAX_CONVERSATIONS);
  return { ...state, conversations, activeId: conversation.id };
}

export function selectConversation(state, id) {
  if (!state.conversations.some((c) => c.id === id)) return state;
  return { ...state, activeId: id };
}

export function deleteConversation(state, id) {
  const conversations = state.conversations.filter((c) => c.id !== id);
  const activeId = state.activeId === id ? (conversations[0]?.id ?? null) : state.activeId;
  return { ...state, conversations, activeId };
}

export function clearConversations(state) {
  return { ...state, conversations: [], activeId: null };
}

export function renameConversation(state, id, title) {
  return mapConversation(state, id, (c) => ({ ...c, title: titleFrom(title, 60), updatedAt: nowIso() }));
}

function mapConversation(state, id, fn) {
  if (!id) return state;
  let touched = false;
  const conversations = state.conversations.map((c) => {
    if (c.id !== id) return c;
    touched = true;
    return fn(c);
  });
  return touched ? { ...state, conversations } : state;
}

export function activeConversation(state) {
  return state.conversations.find((c) => c.id === state.activeId) || null;
}

/** Append a message, creating the conversation on first use (the "new chat" case). */
export function addMessage(state, message, { conversationId = null } = {}) {
  let next = state;
  let id = conversationId || state.activeId;
  if (!id || !next.conversations.some((c) => c.id === id)) {
    next = newConversation(next);
    id = next.activeId;
  }
  return mapConversation(next, id, (c) => ({
    ...c,
    title: c.messages.length === 0 && message.role === "user" ? titleFrom(message.question || message.text) : c.title,
    updatedAt: nowIso(),
    messages: [...c.messages, message],
  }));
}

export function updateMessage(state, conversationId, messageId, patch) {
  return mapConversation(state, conversationId, (c) => ({
    ...c,
    updatedAt: nowIso(),
    messages: c.messages.map((m) => (m.id === messageId ? { ...m, ...patch } : m)),
  }));
}

export function removeMessage(state, conversationId, messageId) {
  return mapConversation(state, conversationId, (c) => ({
    ...c,
    messages: c.messages.filter((m) => m.id !== messageId),
  }));
}

/** Thumbs up/down — recorded locally, because there is no endpoint to send it to. */
export function setFeedback(state, conversationId, messageId, value) {
  return updateMessage(state, conversationId, messageId, {
    feedback: value,                       // "up" | "down" | null
    feedbackAt: value ? nowIso() : null,
  });
}

/** Substring match over titles and questions; local data only. */
export function searchConversations(state, query) {
  const q = String(query || "").trim().toLowerCase();
  const list = state.conversations;
  if (!q) return list;
  return list.filter(
    (c) =>
      c.title.toLowerCase().includes(q) ||
      c.messages.some((m) => String(m.question || m.text || "").toLowerCase().includes(q))
  );
}

// ---------------------------------------------------------------------------
// persistence
// ---------------------------------------------------------------------------
/** Merge stored data over the defaults; anything unparseable yields a fresh state. */
export function hydrate(raw) {
  const base = emptyState();
  if (!raw) return base;
  let data;
  try {
    data = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch {
    return base;                            // corrupt storage must not break the app
  }
  if (!data || typeof data !== "object" || data.version !== SCHEMA_VERSION) return base;
  const conversations = Array.isArray(data.conversations)
    ? data.conversations
        .filter((c) => c && typeof c.id === "string" && Array.isArray(c.messages))
        .slice(0, MAX_CONVERSATIONS)
        .map((c) => ({
          id: c.id,
          title: titleFrom(c.title, 60),
          createdAt: c.createdAt || nowIso(),
          updatedAt: c.updatedAt || c.createdAt || nowIso(),
          messages: c.messages.filter((m) => m && typeof m === "object"),
        }))
    : [];
  const activeId = conversations.some((c) => c.id === data.activeId) ? data.activeId : (conversations[0]?.id ?? null);
  return { version: SCHEMA_VERSION, settings: { ...base.settings, ...(data.settings || {}) }, conversations, activeId };
}

export function serialize(state) {
  return JSON.stringify({
    version: state.version,
    settings: state.settings,
    activeId: state.activeId,
    conversations: state.conversations.map((c) => ({ ...c, messages: c.messages.map(persistableMessage) })),
  });
}

/**
 * The store: state + persistence against an injected adapter
 * (`window.localStorage` in the browser, a Map in tests).
 */
export function createStore(storage = null, { key = STORAGE_KEY, onWarn = null } = {}) {
  let state = emptyState();
  try {
    if (storage) state = hydrate(storage.getItem(key));
  } catch (err) {
    onWarn?.(`Could not read saved conversations: ${err?.message || err}`);
  }

  const commit = (next) => {
    state = next;
    if (!storage) return true;
    try {
      storage.setItem(key, serialize(state));
      return true;
    } catch (err) {
      // Quota exceeded: drop the oldest conversation once, then tell the user
      // instead of silently losing the newest one.
      if (state.conversations.length > 1) {
        const trimmed = { ...state, conversations: state.conversations.slice(0, -1) };
        try {
          storage.setItem(key, serialize(trimmed));
          state = trimmed;
          onWarn?.("Storage is full — the oldest saved conversation was removed.");
          return true;
        } catch { /* fall through */ }
      }
      onWarn?.(`Could not save this conversation locally: ${err?.message || err}`);
      return false;
    }
  };

  return {
    get state() { return state; },
    set(patchFn) { return commit(typeof patchFn === "function" ? patchFn(state) : patchFn); },
    /** Forget every conversation and setting stored in this browser. */
    reset() {
      state = emptyState();
      try { storage?.removeItem(key); } catch { /* nothing to forget */ }
      return true;
    },
    get persisted() { return Boolean(storage); },
  };
}
