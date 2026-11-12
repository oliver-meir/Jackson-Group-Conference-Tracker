"""Find tracked professors in a block of conference text. No LLM, just rules.

How a match works:
  1. Text and names are normalised (lowercase, accents stripped, punctuation removed).
  2. Each professor gets a set of name patterns built from their name and aliases:
     "colin j jackson", "colin jackson", "c j jackson", "c jackson", "jackson colin", "jackson c".
  3. Patterns are searched as whole words. A fuzzy pass (rapidfuzz) also catches small
     spelling slips in the surname, e.g. "Ovchinikov", as long as the first name or
     initial sits right next to it.
  4. If the professor has "context" terms (affiliation words), a match with none of them
     nearby is kept but marked low confidence, so the reviewer can check it.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

PARTICLES = {"van", "der", "den", "de", "del", "della", "di", "da", "von", "le", "la", "du", "dos", "das", "ter", "ten"}

ROLE_RULES = [
    ("Keynote", r"\bkeynote"),
    ("Plenary", r"\bplenary"),
    ("Session chair", r"\b(session )?chair(s|ed|person)?\b(?! (in|of)\b)"),  # not "Chair in Chemistry"
    ("Organiser", r"\borgani[sz](er|ers|ing committee)\b"),
    ("Panel", r"\bpanel(ist)?\b"),
    ("Poster", r"\bposter\b"),
    ("Invited talk", r"\binvited\b|\baward lecture\b|\blecture\b"),
]

CONTEXT_WINDOW = 300
ROLE_WINDOW = 160
SNIPPET_WINDOW = 140


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalise_map(text: str) -> tuple[str, list[int]]:
    """Like normalise(), but also returns, for each output character, its index in the raw text."""
    out, idx = [], []
    for i, ch in enumerate(text):
        dec = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).lower()
        for c in dec:
            if c.isascii() and c.isalnum():
                out.append(c); idx.append(i)
            elif out and out[-1] != " ":
                out.append(" "); idx.append(i)
    while out and out[-1] == " ":
        out.pop(); idx.pop()
    return "".join(out), idx


def split_name(form: str) -> tuple[list[str], list[str]]:
    """Return (given-name tokens, surname tokens). Particles like 'van der' join the surname."""
    toks = normalise(form).split()
    if len(toks) == 1:
        return [], toks
    i = len(toks) - 1
    while i - 1 >= 1 and toks[i - 1] in PARTICLES:
        i -= 1
    return toks[:i], toks[i:]


def name_patterns(name: str, aliases: list[str] | None = None) -> list[tuple[str, bool]]:
    """Patterns as (normalised phrase, is_initial_only)."""
    out: dict[str, bool] = {}
    for form in [name] + list(aliases or []):
        given, surname = split_name(form)
        sur = " ".join(surname)
        if not given:
            # surname-only alias such as "van der Donk": allowed only if multi-word
            if len(surname) > 1:
                out.setdefault(sur, False)
            continue
        full_given = [g for g in given if len(g) > 1]
        initials = [g[0] for g in given]
        if full_given:
            out.setdefault(" ".join(given + surname), False)
            out.setdefault(f"{full_given[0]} {sur}", False)
            out.setdefault(f"{sur} {full_given[0]}", False)
            out.setdefault(f"{sur} {' '.join(given)}", False)
        out.setdefault(f"{' '.join(initials)} {sur}", True)
        out.setdefault(f"{initials[0]} {sur}", True)
        out.setdefault(f"{sur} {' '.join(initials)}", True)
    return list(out.items())


@dataclass
class Match:
    prof_id: str
    snippet: str
    role: str
    confident: bool
    reason: str
    position: int = field(default=0, repr=False)


def _snippet(raw: str, idx: list[int], start: int, end: int) -> str:
    rs, re_ = idx[start], idx[min(end, len(idx)) - 1] + 1
    s = max(0, rs - SNIPPET_WINDOW)
    e = min(len(raw), re_ + SNIPPET_WINDOW)
    text = re.sub(r"\s+", " ", raw[s:e]).strip()
    return ("..." if s > 0 else "") + text + ("..." if e < len(raw) else "")


def guess_role(norm_text: str, start: int, end: int) -> str:
    """Pick the role keyword closest to the name. Words just before the name count more,
    so 'Invited talk: A. Smith' beats a 'Keynote' two lines up."""
    cands = []
    lo = max(0, start - ROLE_WINDOW)
    win = norm_text[lo: end + ROLE_WINDOW]
    for order, (role, pat) in enumerate(ROLE_RULES):
        for m in re.finditer(pat, win):
            ks, ke = lo + m.start(), lo + m.end()
            if ke <= start:
                d = start - ke
            elif ks >= end:
                d = (ks - end) * 1.5
            else:
                d = 0
            if d < ROLE_WINDOW:
                cands.append((d, order, role))
    if not cands:
        return "Unknown"
    best_d = min(c[0] for c in cands)
    # keywords that sit together ("keynote lecture") resolve by priority order
    return min((c for c in cands if c[0] <= best_d + 20), key=lambda c: c[1])[2]


def _has_context(norm_text: str, start: int, end: int, context: list[str]) -> bool:
    if not context:
        return True
    win = norm_text[max(0, start - CONTEXT_WINDOW): end + CONTEXT_WINDOW]
    return any(re.search(r"\b" + re.escape(normalise(c)) + r"\b", win) for c in context)


def find_professor(text: str, prof: dict) -> Match | None:
    """Return the best match for one professor in the text, or None."""
    norm, idx = normalise_map(text)
    if not norm:
        return None
    context = prof.get("context") or []
    hits = []
    for phrase, initial_only in name_patterns(prof["name"], prof.get("aliases")):
        for m in re.finditer(r"\b" + re.escape(phrase) + r"\b", norm):
            ctx = _has_context(norm, m.start(), m.end(), context)
            hits.append((m.start(), m.end(), initial_only, ctx, f'name form "{phrase}"'))
    if not hits:
        hits.extend(_fuzzy_hits(norm, prof, context))
    if not hits:
        return None
    # prefer: has context, then full name over initials, then earliest
    hits.sort(key=lambda h: (not h[3], h[2], h[0]))
    start, end, initial_only, ctx, why = hits[0]
    confident = ctx and not (initial_only and not context)
    reason = why
    if not ctx:
        reason += "; no affiliation words nearby"
    elif initial_only and not context:
        reason += "; initials only"
    return Match(prof["id"], _snippet(text, idx, start, end), guess_role(norm, start, end), confident, reason, start)


def _fuzzy_hits(norm: str, prof: dict, context: list[str]):
    given, surname = split_name(prof["name"])
    if not given or len(surname) != 1 or len(surname[0]) < 6:
        return []
    sur = surname[0]
    first = given[0]
    toks = list(re.finditer(r"[a-z]+", norm))
    out = []
    for i, t in enumerate(toks):
        w = t.group()
        if abs(len(w) - len(sur)) > 2 or w == sur or fuzz.ratio(w, sur) < 88:
            continue
        neighbours = [toks[j].group() for j in (i - 2, i - 1, i + 1) if 0 <= j < len(toks)]
        if first in neighbours or first[0] in neighbours:
            ctx = _has_context(norm, t.start(), t.end(), context)
            out.append((t.start(), t.end(), False, ctx, f'close spelling "{w}"'))
    return out


def find_all(text: str, profs: list[dict]) -> list[Match]:
    return [m for p in profs if (m := find_professor(text, p))]
