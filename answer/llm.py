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

Usage:
    from answer.llm import build_generator
    generator = build_generator()          # Gemini if configured, else offline
    draft = generator.draft(prompt, evidence, language="it")
    if draft is None: ...                  # caller falls back
"""

from __future__ import annotations

import os
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
        self._client = client
        self._api_key = api_key
        self.attempts = max(1, attempts)
        self.base_delay = base_delay
        self.timeout = timeout
        self._sleep = sleep_fn

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
            return None

        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=_generation_config(),
                )
                text = _response_text(response)
                if not text:
                    raise ValueError("empty response")
                return Draft(
                    text=text.strip(),
                    provider=self.name,
                    model=self.model,
                    language=language,
                )
            except Exception as exc:  # quota, timeout, bad JSON, SDK quirk ...
                last_error = exc
                if attempt + 1 >= self.attempts or not _is_retryable(exc):
                    break
                self._sleep(self.base_delay * (2**attempt))

        _log_failure(self.model, last_error)
        return None


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
    }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _generation_config():
    """``temperature=0`` and a JSON-friendly, low-variance configuration."""
    try:
        from google.genai import types
    except ImportError:  # pragma: no cover - guarded by available
        return None
    try:
        return types.GenerateContentConfig(
            temperature=0.1,
            top_p=0.9,
            max_output_tokens=1024,
        )
    except TypeError:  # very old SDKs
        return None


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
