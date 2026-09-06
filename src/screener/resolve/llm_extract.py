"""Step 4, last resort: structured extraction from prose via an LLM call
against a fixed JSON schema.

This is one narrow job — pull ``{street, postal_code, town}`` or nothing out
of a page's text — not an agent loop, and it is never trusted on its own:
its output is a *candidate* like any other, run back through the same
``AddressValidator`` gate in ``resolve/pipeline.py`` before anything is
accepted. An address the register doesn't recognise drops the row. Never
geocode raw model output.

Calls the Anthropic Messages API directly via ``ANTHROPIC_API_KEY``. Behind
an injectable ``Extractor`` so tests don't need a real key or network
access — the ``anthropic`` package is imported lazily, only when the real
extractor actually runs.
"""
from __future__ import annotations

import json
import os
from typing import Callable

from screener.resolve.address_regex import AddressCandidate

_SYSTEM_PROMPT = (
    "Extract a single Danish postal address (street with house number, "
    "4-digit postal code, town) from the given text, if one is clearly "
    'present. Respond with ONLY a JSON object of the form '
    '{"street": string or null, "postal_code": string or null, "town": string or null}. '
    "If no address is clearly present, return all null. Never invent or guess "
    "an address that isn't in the text."
)

Extractor = Callable[[str], dict]
"""text -> a dict with (at minimum) street/postal_code/town keys, or a dict
of nulls if nothing was found. Raises on failure rather than returning a
guess."""

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def anthropic_extractor(text: str, *, model: str = DEFAULT_MODEL) -> dict:
    import anthropic  # lazy: only required if this path actually executes

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — required for the LLM-extraction fallback step")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=200,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text[:4000]}],
    )
    raw_text = response.content[0].text
    return json.loads(raw_text)


def extract_candidate(text: str, extractor: Extractor = anthropic_extractor) -> AddressCandidate | None:
    try:
        data = extractor(text)
    except (json.JSONDecodeError, RuntimeError, IndexError, KeyError):
        return None
    street, postal, town = data.get("street"), data.get("postal_code"), data.get("town")
    if not (street and postal and town):
        return None
    return AddressCandidate(street=str(street), postal_code=str(postal), town=str(town), raw_text=text[:200])
