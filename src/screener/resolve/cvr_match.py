"""Step 1 of the address-resolution pipeline: name -> CVR, fuzzy, within a
postal code. Handles legal-vs-trading-name mismatches ("Bisserup Havnekro
ApS" vs "Bisserup Havnekro") by matching against both the registered name
and any trading names (binavne) cvrapi.dk returns. Per the brief, this
should resolve the majority of cases and is worth investing in — it's also
the cheapest step, so it runs first.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from screener.fetch.cvr import CvrClient, CvrCompany, CvrNotFoundError

MATCH_THRESHOLD = 0.72  # conservative: a false accept here silently mislocates a business

_LEGAL_SUFFIX_RE = re.compile(r"\b(aps|a/s|i/s|ivs|k/s|amba)\b")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    name = name.lower()
    name = _LEGAL_SUFFIX_RE.sub("", name)
    name = _PUNCT_RE.sub("", name)
    return _WHITESPACE_RE.sub(" ", name).strip()


def _best_score(query: str, company: CvrCompany) -> float:
    q = _normalize(query)
    candidates = [company.name, *company.trading_names]
    scores = [difflib.SequenceMatcher(None, q, _normalize(c)).ratio() for c in candidates if c]
    return max(scores) if scores else 0.0


@dataclass
class CvrMatch:
    company: CvrCompany
    score: float


def match_name_to_cvr(client: CvrClient, name: str, postal_code: int) -> CvrMatch | None:
    try:
        company = client.search_by_name(name, postal_code=postal_code)
    except CvrNotFoundError:
        return None
    if not company.active:
        # An ophoert (closed) business is not a hangout you can actually visit.
        return None
    score = _best_score(name, company)
    if score < MATCH_THRESHOLD:
        return None
    return CvrMatch(company=company, score=score)
