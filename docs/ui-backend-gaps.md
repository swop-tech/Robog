# What the web UI needs from the backend and does not have yet

The Telmekon AI Assistant interface (`ui/`) is wired **only** to endpoints that
exist. Nothing is simulated: where a feature would need backend support that
isn't there, the UI says so in the product (Settings → *Not supported by the API
yet*, plus the tooltip on the affected control) and this file records the exact
requirement.

Source of truth for what exists today:

```bash
curl -s localhost:8000/openapi.json | python -c 'import json,sys; print(sorted(json.load(sys.stdin)["paths"]))'
# ['/', '/ask', '/health', '/media/{doc_id}/{filename}', '/products', '/search']
```

The UI's own mirror of that list is `CAPABILITIES` in `ui/api.js`; the Settings
dialog renders from it, so a flag flipped to `true` there is the only change the
frontend needs once an endpoint lands. `ui/tests/api.test.js` fails if a flag
claims a capability the tests cannot see, and `ui/tests/contract.test.js` fails
if `ui/` references a path outside the list above.

| # | Feature | UI behaviour today | Backend requirement | Priority |
|---|---|---|---|---|
| 1 | Streaming answers | one spinner, then the whole answer (`POST /ask` is a single JSON body) | `POST /ask/stream` (SSE) | high — perceived latency |
| 2 | Server-side conversation history | conversations live in `localStorage`, labelled as browser-only everywhere they appear | `POST /conversations`, `GET /conversations`, `GET/DELETE /conversations/{id}` | medium |
| 3 | Search across conversations | the sidebar filter runs over local data only | `GET /conversations?q=` | low |
| 4 | Answer feedback | thumbs up/down stored locally; the tooltip and the toast both say the API has no feedback endpoint | `POST /feedback` | high — it is the only way to close the quality loop |
| 5 | Regenerate | re-issues the identical `POST /ask` (a real second request) | `POST /ask/regenerate` with cache-bypass + audit id | medium |
| 6 | Follow-up suggestions | not offered — the UI will not invent questions the engine did not propose | `followups[]` on the `/ask` payload | medium |
| 7 | Persona register | shows the answer as the composer wrote it | `audience` on `AskRequest` (see `docs/brief-alignment.md` §4.1) | medium |

---

## 1. Streaming — `POST /ask/stream`

**Why it matters.** A Gemini answer takes seconds; today the user watches a
spinner with an elapsed-time counter. The UI is ready for a token stream —
`ui/render.js` renders `payload.answer` through one function, so partial text can
be repainted as it arrives — but `fetch()` + `response.json()` cannot express it.

**Requirement.** Same request body as `POST /ask` (`AskRequest`), response
`text/event-stream`:

```
event: meta
data: {"route":"lookup","language":"de","provider":"gemini","model":"gemini-2.5-flash"}

event: token
data: {"delta":"Laut Handbuch beträgt "}

event: evidence
data: {"citations":[…],"params":[…],"figures":[…],"profiles":[…]}

event: verification
data: {"checked":true,"state":"verified","sentences_cited":2,"sentences_checked":2,…}

event: done
data: {"latency_ms":4213}
```

Notes for the implementer:

* `evidence` and `verification` must arrive **after** the text, because
  `answer/validator.py` repairs the answer once it is complete; the UI keeps the
  badge hidden until `verification` lands rather than showing a provisional green.
* On any failure mid-stream, emit `event: error` with the same body shape as the
  current error responses (`{"reason": …, "fix": …}` for a 503) so the UI can
  reuse its existing error card.
* `api/app.py` already runs the pipeline in a threadpool; an
  `asyncio.Queue`-backed `StreamingResponse` keeps that unchanged.

## 2. Conversation history — `/conversations`

**Why it matters.** The brief's "ease of use" criterion is multi-turn. `POST
/ask` is stateless by design, so the UI stores conversations in `localStorage`
(key `telmekon.ui.v1`, capped at 25, oldest dropped first when the quota is
hit). That is honest but browser-bound: nothing survives a different device or a
cleared cache, and the UI says so in the sidebar footnote, in Settings and in the
empty-state text.

**Requirement.**

```
POST   /conversations                 -> {"id","title","created_at"}
GET    /conversations?limit=&cursor=  -> {"items":[{"id","title","created_at","updated_at","message_count"}],"next_cursor"}
GET    /conversations/{id}            -> {"id","title","messages":[…]}
DELETE /conversations/{id}            -> 204
POST   /conversations/{id}/messages   -> the /ask payload, appended server-side
```

* A message object needs at least: `id`, `role`, `question`, `answer`,
  `created_at`, plus the evidence block the `/ask` payload already returns
  (`citations`, `params`, `figures`, `profiles`, `verification`, `route`,
  `language`, `cross_lingual`, `provider`, `latency_ms`). Storing the whole
  payload means a restored conversation renders identically — that is exactly
  what the UI does today from `localStorage`, so the migration is mechanical.
* Storage: `build/` is gitignored and per-run; a conversation store belongs
  beside it (SQLite file or JSONL) and must not be committed.
* `POST /ask` should keep working unchanged; the UI will call the conversation
  route only when `CAPABILITIES.conversationHistory` is `true`.

## 3. Conversation search — `GET /conversations?q=`

Only worth building together with #2. The UI's filter already searches titles
and question text; with server-side history it would send `q` instead and page
the results. Substring match over `title` + `question` is sufficient — no new
index needed.

## 4. Feedback — `POST /feedback`

**Why it matters.** `docs/brief-alignment.md` §4.2 records that the repo's own
benchmark grades *expected-string presence* in answers the repo wrote, so there
is no independent quality signal. Thumbs up/down on real questions is the
cheapest one, and the UI already collects the vote — it just has nowhere to put
it. Both the button tooltip and the confirmation toast state that the vote stays
in the browser.

**Requirement.**

```
POST /feedback
{
  "question": "Wieviel Zuladung hat der FlashBot?",
  "answer": "…",                     // or "answer_hash": "sha256:…"
  "vote": "up" | "down" | null,       // null clears a previous vote
  "route": "lookup",
  "language": "de",
  "provider": "gemini",
  "verification_state": "verified",
  "citations": 1,
  "comment": "",                      // optional free text
  "conversation_id": null,            // present once #2 exists
  "message_id": null,
  "client": "ui/1.0"
}
-> 201 {"id": "…"}
```

Append-only JSONL (`build/feedback.jsonl`) is enough to start; the point is that
`answer/evaluate.py` can then report a human signal next to its automatic one.

## 5. Regenerate — `POST /ask/regenerate`

**Today.** The *Regenerate* button re-sends the identical `POST /ask` body. That
is a real request and it does produce a new answer, but with the deterministic
offline formatter (`provider: "offline"`, which is what runs without
`GEMINI_API_KEY`) the result is byte-identical, so the button looks inert in a
demo. The UI does not hide that: the tooltip reads "Ask the API again".

**Requirement.** Either

* `POST /ask/regenerate` — `{…AskRequest, "message_id": "…", "vary": true}` —
  which bypasses any cache and, for Gemini, raises the temperature slightly, and
  returns the normal `/ask` payload plus `{"regenerated_from": "…"}`; or
* a documented statement that `/ask` is intentionally deterministic, in which
  case the UI will relabel the button "Ask again" and stop implying variation.

## 6. Follow-up suggestions — `followups[]`

The composer could offer "ask about the next thing" chips, but only if the
engine proposes them. A UI-invented suggestion list would be exactly the kind of
fabricated content the brief forbids, so the UI shows none.

**Requirement.** On the `/ask` payload:

```json
"followups": [
  {"question": "Wie reinige ich die Ablagen des FlashBot?", "why": "same section, §2.3"},
  {"question": "What is the charging time of the FlashBot?", "why": "same product, parameter index"}
]
```

Cheap to derive from data the composer already has: the sibling parameter rows of
the matched product (`api/service.py::_param_rows`), the sections adjacent to the
cited chunk, and the translation siblings of a cross-lingual hit. Each item must
carry a `why` so the UI can show provenance instead of a bare guess.

## 7. Persona register — `audience` on `AskRequest`

`docs/brief-alignment.md` §4.1: the brief defines two personas (a technician who
wants *"the value of this parameter"* and a non-technical buyer who wants *"which
robot fits my customer"*), and the composer answers both in the same technician
register. The UI already separates them visually — spec cards for the first,
product cards for the second — and the routing signal exists (`payload.route` is
`lookup` / `advisory` / `figure` / `default`).

**Requirement.** `audience: "technician" | "business" | "auto"` on `AskRequest`
(default `auto` = today's behaviour, derived from the route), plumbed into
`answer/composer.py` so the formatter can shorten or expand. When it lands, the
UI adds one segmented control in Settings and one field to the request; nothing
else changes.

---

## Things the UI was told to expect and does *not* need after all

Recorded so nobody builds them:

* **`image_available`** — not needed. `api/media.py::url_for` already returns
  `None` unless the file resolves, so a non-null `image_url` is a promise the
  server keeps. The UI still listens for a failed image load and replaces the
  figure with a message rather than a broken-image icon.
* **CORS changes** — `api/app.py` already mounts `CORSMiddleware` from
  `settings.cors_origins`, and the UI is served same-origin from `GET /`.
* **Auth** — the UI holds no credentials and sends no auth header. The Gemini key
  is read server-side from `.env` (`answer/llm.py`); the browser only ever sees
  the strings `"gemini"` / `"offline"` in `payload.provider`. Any future auth
  must stay server-side (a session cookie), never a key in `ui/`.
* **`include_search` / `include_evidence`** — both exist on `AskRequest`. The UI
  deliberately uses the standalone `GET /search` instead, because Bonus 01 asks
  for retrieval to be *demonstrably* separate; the drawer shows the real URL it
  called, which embedding the payload inside `/ask` would hide.

## How to check the UI is still honest after a backend change

```bash
python -m api                                             # terminal 1
npm install --no-save jsdom                               # once, dev-only
node --test ui/tests/*.test.js                            # 201 tests, stubs only
ROBOG_LIVE_API=http://localhost:8000 node --test ui/tests/live.test.js
```

The live suite boots `index.html` in jsdom against the running server, clicks all
four starter scenarios and asserts that what lands in the DOM came from the
response — real locators, real figure paths, real verification counts — and that
a question the manuals cannot answer never receives a green badge.
