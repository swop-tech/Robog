"""Pluggable answer generators: Gemini first, deterministic formatter always.

The composer asks a *generator* for prose; the composer itself owns the prompt,
the evidence numbering and -- critically -- the validation. That separation is
what makes the LLM optional:

* ``GeminiGenerator`` -- ``gemini-3.8-flash`` via ``google-genai`` (already in
  ``requirements-rag.txt`` for the embedding step; no LlamaIndex, no extra
  framework). The key comes from ``GEMINI_API_KEY`` in ``.env``.
* ``OfflineGenerator`` -- the deterministic formatter of ``answer/formatter.py``.
  No API key, no network, no model download.

``build_generator()`` picks Gemini when a key *and* the SDK are present,
otherwise offline -- and it says which one it picked, so the API can report it
and the UI can label the answer.

Nothing in this module is allowed to raise on a missing key, a quota error, a
timeout or a malformed response: ``GeminiGenerator.draft`` returns ``None`` in
those cases and the composer falls back to the offline formatter. A live demo
must not be able to die because Google's free tier hiccuped.

It must not fail *silently* either. The offline formatter can only quote the
manuals, so a Gemini call that fails on every request looks exactly like "the
bot pastes paragraphs". ``GeminiGenerator.last_error`` keeps the reason in
plain words (wrong model ID, rejected key, quota, empty response); the composer
puts it on the answer and ``python -m answer --check`` tests the call directly.

One failure is repaired instead of reported: a model ID the key can no longer
call. Google retires model IDs, and a ``.env`` copied a few months ago still
names the old one, so every answer would come from the formatter until someone
edits a file. ``GeminiGenerator`` instead tries the model the API's error names,
then the default, then the models the key lists, and keeps the first that
answers (``model_note`` says which one, and why).

Usage:
    python -m answer --check               # one real call: who writes the answers?

    from answer.llm import build_generator
    generator = build_generator()          # Gemini if configured, else offline
    draft = generator.draft(prompt, evidence, language="it")
    if draft is None: ...                  # caller falls back
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

from retrieve.env import load_dotenv
from retrieve.vector import VectorStoreUnavailable, create_gemini_client

from .evidence import Evidence
from .formatter import Draft, format_answer

#: The answering model. Google has limited the 2.5 Flash family to existing
#: users (``gemini-2.5-flash`` returns 404 NOT_FOUND for new keys) and points new
#: projects to 3.8 Flash. Override with ``ROBOG_LLM_MODEL`` if your key differs.
DEFAULT_LLM_MODEL = "gemini-3.8-flash"
ENV_LLM_MODEL = "ROBOG_LLM_MODEL"
ENV_LLM_PROVIDER = "ROBOG_LLM_PROVIDER"     # "gemini" | "offline"
ENV_LLM_TIMEOUT = "ROBOG_LLM_TIMEOUT"

#: Room for the answer *and* the model's reasoning. The Flash models think
#: before they write, and those tokens are billed against this same limit: at
#: the previous 1024 a longer question could spend the whole budget thinking
#: and return no text at all, which then fell back to the offline formatter.
MAX_OUTPUT_TOKENS = 8192

#: How many other models to try when the configured one is not available.
#: Bounded so a request cannot spend its time walking a model list.
MAX_MODEL_REPLACEMENTS = 4


class Generator(Protocol):
    """Anything that can turn a prompt + evidence into an answer draft."""

    name: str
    model: str | None

    def draft(
        self,
        prompt: str,
        evidence: Evidence,
        *,
        language: str,
        route: str = "default",
        question: str = "",
    ) -> Draft | None:
        """Return an answer draft, or ``None`` when this generator cannot answer.

        ``route`` is the retriever's routing decision (lookup / figure /
        advisory / default); the offline formatter uses it to choose the shape
        of the answer, an LLM generator can ignore it.
        """


@dataclass
class OfflineGenerator:
    """The deterministic formatter, wrapped in the generator interface."""

    name: str = "offline"
    model: str | None = None
    route: str = "default"

    def draft(
        self,
        prompt: str,
        evidence: Evidence,
        *,
        language: str,
        route: str = "default",
        question: str = "",
    ) -> Draft:
        return format_answer(
            evidence, question=question, language=language, route=route or self.route
        )


class GeminiGenerator:
    """``gemini-3.8-flash`` through ``google-genai`` (one HTTP call, no framework)."""

    name = "gemini"

    def __init__(
        self,
        model: str | None = None,
        *,
        client=None,
        api_key: str | None = None,
        attempts: int = 2,
        base_delay: float = 1.0,
        timeout: float | None = None,
        sleep_fn=time.sleep,
    ):
        self.model = (model or os.environ.get(ENV_LLM_MODEL) or DEFAULT_LLM_MODEL).strip()
        #: The model that was asked for. ``model`` moves to a replacement when
        #: this one turns out to be unavailable for the key; this does not.
        self.requested_model = self.model
        #: Set when ``model`` is a replacement: what was replaced and why.
        self.model_note: str | None = None
        self._client = client
        self._api_key = api_key
        self.attempts = max(1, attempts)
        self.base_delay = base_delay
        self.timeout = timeout
        self._sleep = sleep_fn
        #: Why the most recent call produced no answer (``None`` after a success).
        self.last_error: str | None = None
        self._unavailable: set[str] = set()
        #: True once a search found no usable replacement; it is not repeated on
        #: every request (a restart, or fixing ``ROBOG_LLM_MODEL``, clears it).
        self._no_replacement = False

    # -- availability ------------------------------------------------------
    @property
    def available(self) -> bool:
        """True when a client can be created (key + SDK present)."""
        return self._resolve_client() is not None

    def _resolve_client(self):
        if self._client is not None:
            return self._client
        try:
            self._client = create_gemini_client(self._api_key)
        except VectorStoreUnavailable:
            return None
        return self._client

    # -- generation --------------------------------------------------------
    def draft(
        self,
        prompt: str,
        evidence: Evidence,
        *,
        language: str,
        route: str = "default",
        question: str = "",
    ) -> Draft | None:
        """Ask Gemini; ``None`` on any failure (caller falls back to offline)."""
        client = self._resolve_client()
        if client is None:
            self.last_error = missing_gemini_reason()
            return None

        model = self.model
        answer, error = self._call(client, model, prompt)
        if answer is None and is_model_unavailable(error):
            answer, model, error = self._call_replacement(client, prompt, error)

        if answer is None:
            self.last_error = explain_failure(error, self.model)
            _log_failure(self.model, error)
            return None
        self.last_error = None
        return Draft(text=answer.strip(), provider=self.name, model=model, language=language)

    def _call(self, client, model: str, prompt: str) -> tuple[str | None, Exception | None]:
        """One model, with retries for transient errors: ``(text, None)`` or ``(None, error)``."""
        error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=_generation_config(),
                )
                answer = _response_text(response)
                if not answer:
                    raise ValueError(f"empty response{_finish_detail(response)}")
                return answer, None
            except Exception as exc:  # quota, timeout, bad JSON, SDK quirk ...
                error = exc
                if attempt + 1 >= self.attempts or not _is_retryable(exc):
                    break
                self._sleep(self.base_delay * (2**attempt))
        return None, error

    def _call_replacement(
        self, client, prompt: str, error: Exception | None
    ) -> tuple[str | None, str, Exception | None]:
        """The configured model is gone: answer with one this key can call.

        Returns ``(text, model, error)``. On success ``self.model`` becomes the
        replacement, so only the first request pays for the failed call. When
        no replacement answers, the *original* error is returned: it names the
        setting to change, which a later quota error on some other model would not.
        """
        failed = self.model
        self._unavailable.add(failed)
        if self._no_replacement:
            return None, failed, error
        tried = 0
        for candidate in replacement_models(client, failed, error):
            if candidate in self._unavailable:
                continue
            if tried >= MAX_MODEL_REPLACEMENTS:
                break
            tried += 1
            answer, candidate_error = self._call(client, candidate, prompt)
            if answer is not None:
                self.model = candidate
                self.model_note = (
                    f"'{self.requested_model}' is not available for this API key; "
                    f"answering with '{candidate}' instead "
                    f"(set {ENV_LLM_MODEL}={candidate} in .env to make it permanent)"
                )
                _log_replacement(self.model_note)
                return answer, candidate, None
            if not is_model_unavailable(candidate_error):
                # The candidate exists but failed for another reason (quota,
                # network). Trying further models would only repeat that; the
                # next request may try this candidate again.
                return None, failed, error
            self._unavailable.add(candidate)
        self._no_replacement = True
        return None, failed, error


# --------------------------------------------------------------------------
# factory
# --------------------------------------------------------------------------
def build_generator(
    provider: str | None = None,
    *,
    model: str | None = None,
    route: str = "default",
    client=None,
) -> Generator:
    """Pick a generator: explicit provider, else Gemini when configured, else offline.

    ``provider`` recognises ``"gemini"``, ``"offline"``/``"deterministic"`` and
    ``"auto"``/``None``. ``ROBOG_LLM_PROVIDER`` sets the default.
    """
    load_dotenv()
    wanted = (provider or os.environ.get(ENV_LLM_PROVIDER) or "auto").strip().lower()
    if wanted in ("offline", "deterministic", "none"):
        return OfflineGenerator(route=route)
    gemini = GeminiGenerator(model, client=client)
    if wanted in ("gemini", "google", "auto", ""):
        if gemini.available:
            return gemini
        if wanted not in ("auto", ""):
            return OfflineGenerator(route=route)
    return OfflineGenerator(route=route)


def missing_gemini_reason() -> str:
    """Why Gemini is not in use -- shown as a note so the demo is never a mystery."""
    if (os.environ.get(ENV_LLM_PROVIDER) or "").strip().lower() in ("offline", "deterministic", "none"):
        return f"{ENV_LLM_PROVIDER} is set to offline"
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip():
        return "GEMINI_API_KEY not set"
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return "google-genai not installed (pip install -r requirements-rag.txt)"
    return "Gemini client unavailable"


def describe_provider(generator: Generator) -> dict:
    """Metadata about the active generator, for the API response and the UI."""
    name = getattr(generator, "name", "offline")
    return {
        "provider": name,
        "model": getattr(generator, "model", None),
        "offline": name != "gemini",
        "reason": None if name == "gemini" else missing_gemini_reason(),
        # Set when the configured model was not available for the key and
        # another one answers instead (``model`` is then the replacement).
        "requested_model": getattr(generator, "requested_model", None),
        "model_note": getattr(generator, "model_note", None),
        # A configured Gemini whose calls fail still answers through the
        # formatter; this is how /health says so instead of reporting "Gemini".
        "last_error": getattr(generator, "last_error", None),
    }


# --------------------------------------------------------------------------
# diagnosis: `python -m answer --check`
# --------------------------------------------------------------------------
def available_models(client) -> list[str]:
    """Model IDs this key can call for text generation (``[]`` if listing fails)."""
    names: list[str] = []
    try:
        for model in client.models.list():
            actions = getattr(model, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            name = str(getattr(model, "name", "") or "").removeprefix("models/")
            if name and name not in names:
                names.append(name)
    except Exception:  # listing is a convenience; the diagnosis must still print
        return []
    # Flash models first: they are what the free tier is meant for.
    return sorted(names, key=lambda name: ("flash" not in name, name))


def check_llm(model: str | None = None, *, client=None) -> tuple[bool, list[str]]:
    """Make one real call and report, step by step, who will write the answers.

    Returns ``(ok, lines)``. ``ok`` is True only when the model answered, which
    is the one condition under which the chat explains instead of quoting.
    """
    load_dotenv()
    lines: list[str] = []
    wanted = (os.environ.get(ENV_LLM_PROVIDER) or "auto").strip().lower()
    if wanted in ("offline", "deterministic", "none"):
        lines.append(f"FAIL  {ENV_LLM_PROVIDER}={wanted}: the offline formatter is forced.")
        lines.append(f"      Set {ENV_LLM_PROVIDER}=auto in .env.")
        return False, lines
    lines.append(f"ok    {ENV_LLM_PROVIDER}={wanted}")

    generator = GeminiGenerator(model, client=client, attempts=1)
    if not generator.available:
        reason = missing_gemini_reason()
        lines.append(f"FAIL  {reason}")
        if "KEY" in reason:
            lines.append("      Create a key at https://aistudio.google.com/apikey and put")
            lines.append("      GEMINI_API_KEY=... in .env at the repository root, then restart the API.")
        return False, lines
    lines.append("ok    API key found and google-genai installed")

    draft = generator.draft("Reply with the single word: ready", Evidence(), language="en")
    if draft is None:
        lines.append(f"FAIL  {generator.model}: {generator.last_error}")
        if "is not available for this API key" in (generator.last_error or ""):
            models = available_models(generator._resolve_client())
            if models:
                lines.append("      Models this key can use (set one as ROBOG_LLM_MODEL in .env):")
                lines.extend(f"        {name}" for name in models[:12])
        return False, lines
    if generator.model_note:
        lines.append(f"warn  {generator.requested_model} is not available for this API key.")
        lines.append(f"      Robog switched to {generator.model}, and does so again after every restart.")
        lines.append(f"      Set {ENV_LLM_MODEL}={generator.model} in .env to skip the failed first call.")
    lines.append(f"ok    {generator.model} answered: {draft.text[:60]!r}")
    lines.append("      Answers will be written by the model.")
    return True, lines


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _generation_config():
    """Low but not zero temperature (facts stay put, wording is not robotic)."""
    try:
        from google.genai import types
    except ImportError:  # pragma: no cover - guarded by available
        return None
    try:
        return types.GenerateContentConfig(
            temperature=0.3,
            top_p=0.95,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except TypeError:  # very old SDKs
        return None


def _finish_detail(response) -> str:
    """`` (finish_reason=MAX_TOKENS)`` when the API says why there is no text."""
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason:
            return f" (finish_reason={getattr(reason, 'name', reason)})"
    return ""


def explain_failure(exc: Exception | None, model: str) -> str | None:
    """A failed call in words a user can act on, with the raw error kept."""
    if exc is None:
        return None
    raw = f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}"
    message = raw.lower()
    if is_model_unavailable(exc):
        hint = (
            f"the model '{model}' is not available for this API key, and no replacement "
            f"answered either; set {ENV_LLM_MODEL} to a model your key lists "
            f"(python -m answer --check shows them)"
        )
    elif "429" in message or "quota" in message or "resource_exhausted" in message:
        hint = "the Gemini quota or rate limit is exhausted; wait or use another key"
    elif any(
        marker in message
        for marker in ("api key", "api_key", "401", "403", "permission", "unauthenticated")
    ):
        hint = "the API key was rejected; check GEMINI_API_KEY"
    elif "empty response" in message:
        hint = "the model returned no text"
    else:
        return raw
    return f"{hint} [{raw}]"


def is_model_unavailable(exc: Exception | None) -> bool:
    """True when the API says this model ID cannot be called with this key.

    Covers a typo, a model that was never offered to the key and a retired one
    ("models/gemini-2.5-flash is no longer available to new users").
    """
    if exc is None:
        return False
    message = f"{type(exc).__name__}: {exc}".lower()
    return (
        "404" in message
        or "not_found" in message
        or ("not found" in message and "model" in message)
        or "no longer available" in message
    )


_MODEL_IN_ERROR = re.compile(r"models/([a-z0-9][a-z0-9.\-]*[a-z0-9])", re.IGNORECASE)
_STABLE_TEXT_MODEL = re.compile(r"^gemini-(\d+(?:\.\d+)?)-(flash|flash-lite|pro)$")
_TIER_ORDER = {"flash": 0, "flash-lite": 1, "pro": 2}


def replacement_models(client, failed: str, error: Exception | None = None):
    """Yield models worth trying instead of ``failed``, best guess first.

    1. the model the API's own error message recommends,
    2. :data:`DEFAULT_LLM_MODEL`,
    3. the stable text models the key lists: Flash before Flash-Lite before Pro,
       newest version first. Image, audio, live and preview variants are left
       out; they are listed under ``generateContent`` but do not answer a chat.

    The list call is only made when the first two did not settle it.
    """
    seen = {failed, failed.removeprefix("models/")}

    def fresh(name: str) -> bool:
        if not name or name in seen:
            return False
        seen.add(name)
        return True

    for match in _MODEL_IN_ERROR.finditer(str(error or "")):
        if fresh(match.group(1)):
            yield match.group(1)
    if fresh(DEFAULT_LLM_MODEL):
        yield DEFAULT_LLM_MODEL

    ranked = []
    for name in available_models(client):
        stable = _STABLE_TEXT_MODEL.match(name)
        if stable:
            ranked.append((_TIER_ORDER[stable.group(2)], -float(stable.group(1)), name))
    for _tier, _version, name in sorted(ranked):
        if fresh(name):
            yield name


def _response_text(response) -> str:
    """Extract text from a ``generate_content`` response (SDK shape tolerant)."""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        chunks = [getattr(part, "text", "") or "" for part in parts]
        joined = "".join(chunks).strip()
        if joined:
            return joined
    return ""


_RETRYABLE = ("429", "500", "502", "503", "504", "timeout", "deadline", "unavailable", "overloaded")


def _is_retryable(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    if "quota" in message and "exceeded" in message and "per day" in message:
        return False
    return any(marker in message for marker in _RETRYABLE)


def _log_failure(model: str, exc: Exception | None) -> None:
    """One line to stderr -- the API request must still succeed offline."""
    if exc is None:
        return
    import sys

    print(
        f"warning: Gemini answer generation failed ({model}): "
        f"{type(exc).__name__}: {str(exc)[:200]} -- falling back to the offline formatter",
        file=sys.stderr,
    )


def _log_replacement(note: str) -> None:
    import sys

    print(f"warning: Gemini model replaced: {note}", file=sys.stderr)
