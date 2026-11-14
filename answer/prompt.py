"""The grounding prompt: numbered evidence in, cited answer out.

The prompt is written once, in English, with the *output* language injected --
that is what makes Bonus 02 work without a translation step. Instructions that
matter, in order of importance:

1. answer only from the numbered evidence (no outside knowledge);
2. every factual sentence ends with ``[n]`` referencing that evidence;
3. numbers and units are copied exactly, never computed or converted;
4. quoted text must be verbatim from the evidence;
5. say plainly when the evidence does not contain the answer;
6. answer in the question's language, and mention when the evidence is in
   another language (the Italian question / German manual case).

The prompt deliberately does *not* ask the model to translate the sources: the
answer is written in the user's language, and the evidence stays quoted in its
own, so the quote check can always verify it.
"""

from __future__ import annotations

from .evidence import Evidence
from .strings import language_name

SYSTEM_INSTRUCTIONS = """You are Robog, a documentation assistant for PUDU service robots.

You answer technician and non-technical questions about delivery, cleaning and
industrial robots using ONLY the numbered evidence below, which was retrieved
from the official manuals.

Rules you must follow:
1. Use only the evidence. Never add facts from general knowledge, and never
   guess a specification.
2. Every factual sentence must end with one or more citations of the form [1],
   [2] or [2, 3], referring to the evidence numbers below.
3. Copy numbers and units exactly as they appear (e.g. "20 kg", "1.2 m/s",
   "29.2 Vdc"). Never convert, round, average or invent a value, and never
   attach a unit the evidence does not show.
4. If you quote the manual, copy the words exactly. Keep quotations short.
5. If the evidence does not answer the question, say so in one sentence and
   stop. Do not fill the gap with other content.
6. Answer in {language}. The evidence may be in another language: quote it as
   it is, and mention briefly which language the source is in.
7. Be concise: 2-5 sentences for a lookup, a short bulleted list for a
   comparison. No preamble, no "according to the context", no restating the
   question."""

_USER_TEMPLATE = """Question ({language}): {question}

Retrieved evidence:
{evidence_block}

Write the answer in {language}, following every rule above."""


def build_prompt(
    question: str,
    evidence: Evidence,
    *,
    language: str | None = None,
    route: str = "default",
) -> str:
    """Assemble the full prompt (instructions + numbered evidence)."""
    language = language or evidence.language or "en"
    instructions = SYSTEM_INSTRUCTIONS.replace("{language}", language_name(language, in_language="en"))
    if route == "advisory":
        instructions += (
            "\n8. This is a recommendation question: name each suitable robot, give one "
            "or two decisive specs each, and cite the profile card for every robot."
        )
    elif route == "figure":
        instructions += (
            "\n8. The user asked for a picture: name the figure or diagram that shows it "
            "and list the relevant numbered labels."
        )
    elif route == "lookup":
        instructions += (
            "\n8. This is a specification lookup: lead with the exact value and its unit."
        )
    block = evidence_block(evidence)
    user = _USER_TEMPLATE.format(
        question=question.strip(),
        language=language_name(language, in_language="en"),
        evidence_block=block,
    )
    return f"{instructions}\n\n{user}"


def evidence_block(evidence: Evidence, *, max_text: int = 900) -> str:
    """Render evidence as ``[n] (kind) citation / text`` lines.

    The text is capped per ref so a huge table cannot crowd the prompt out of
    the model's context; the cap is generous enough to contain the parameter
    rows an answer needs.
    """
    lines: list[str] = []
    for ref in evidence.refs:
        language = ref.language.upper() if ref.language else "?"
        header = f"[{ref.n}] {ref.kind} — {ref.citation or 'unreferenced'} — source language: {language}"
        lines.append(header)
        body = (ref.text or "").strip()
        if len(body) > max_text:
            body = body[:max_text].rstrip() + " …"
        if body:
            lines.append(body)
        if ref.labels:
            lines.append("Labels: " + "; ".join(ref.labels[:20]))
        if ref.image:
            lines.append(f"Figure image: {ref.image}")
        lines.append("")
    return "\n".join(lines).strip()


def language_instruction(language: str | None) -> str:
    """Short, testable description of the output-language requirement."""
    language = language or "en"
    name = language_name(language, in_language="en")
    return f"Answer in {name} ({language}); evidence may be in another language."
