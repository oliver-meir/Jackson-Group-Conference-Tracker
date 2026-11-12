"""Conference series added on the website, and reading dates/places off program pages.

A series is stored in data/conferences.json, e.g.
  {"id": "lorne-proteins", "name": "Lorne Proteins Conference",
   "urls": ["https://proteins-{year}.p.asnevents.com.au/"], "enabled": true}

If a link contains {year}, the checker tries last year, this year and next year, so the
sweep rolls forward on its own: next year's edition is picked up as soon as its site exists.
A link without {year} is checked as it is every week.
"""
from __future__ import annotations

import datetime as dt
import os
import re

from places import CITIES

YEAR_RE = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


def templatize(url: str) -> tuple[str, bool]:
    """Replace the year in a link with {year}. Returns (template, had_year)."""
    years = YEAR_RE.findall(url)
    if not years:
        return url, False
    y = years[-1]
    return url.replace(y, "{year}"), True


def edition_years(today: dt.date | None = None) -> list[int]:
    t = today or (dt.date.fromisoformat(os.environ["TRACKER_TODAY"]) if os.environ.get("TRACKER_TODAY") else dt.date.today())
    return [t.year - 1, t.year, t.year + 1]


def expand_series(series: dict, today: dt.date | None = None) -> list[dict]:
    """Turn one series into watch entries (one per edition year, or one if no {year})."""
    urls = [u for u in series.get("urls", []) if u]
    yearly = any("{year}" in u for u in urls)
    base = {
        "series": series["id"],
        "city": series.get("city", ""),
        "country": series.get("country", ""),
        "start": "",
        "end": "",
    }
    if not yearly:
        return [dict(base, id=f'{series["id"]}-page', conference=series["name"], urls=urls,
                     year=None, optional=False)]
    out = []
    for y in edition_years(today):
        out.append(dict(base, id=f'{series["id"]}-{y}', conference=f'{series["name"]} {y}',
                        urls=[u.replace("{year}", str(y)) for u in urls], year=y, optional=True))
    return out


# ---------- reading dates off a page ----------

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
WD = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
D = r"(\d{1,2})(?:st|nd|rd|th)?"
SEP = r"\s*(?:-|to|until)\s*"

RANGE_PATTERNS = [
    # 8-12 February 2027 / Sunday 8 - Thursday 12 February, 2027
    ("d-d m y", re.compile(WD + D + SEP + WD + D + r"(?:\s+of)?\s+" + MON + r",?\s+(20\d\d)", re.I)),
    # February 8-12, 2027
    ("m d-d y", re.compile(MON + r"\s+" + D + SEP + D + r",?\s+(20\d\d)", re.I)),
    # 30 June - 3 July 2027
    ("d m-d m y", re.compile(WD + D + r"(?:\s+of)?\s+" + MON + SEP + WD + D + r"(?:\s+of)?\s+" + MON + r",?\s+(20\d\d)", re.I)),
    # June 30 - July 3, 2027
    ("m d-m d y", re.compile(MON + r"\s+" + D + SEP + MON + r"\s+" + D + r",?\s+(20\d\d)", re.I)),
]


def _clean(text: str) -> str:
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text)


def _mk(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None


SINGLE_PATTERNS = [
    re.compile(MON + r"\s+" + D + r"(?!\d)", re.I),           # June 15 / June 15, 2026
    re.compile(D + r"(?:\s+of)?\s+" + MON + r"(?![a-z])", re.I),  # 15 June / 15th of June
]


def _support(t: str, s: dt.date, e: dt.date, span: tuple[int, int]) -> int:
    """How many other day-mentions on the page fall inside the range. A real
    conference's program repeats its own days (\"June 15, 2026\" headings); a date in a
    site menu or a promo for another event usually appears once."""
    days = set()
    d = s
    while d <= e:
        days.add((d.month, d.day)); d += dt.timedelta(days=1)
    n = 0
    for pat in SINGLE_PATTERNS:
        for m in pat.finditer(t):
            if span[0] <= m.start() < span[1]:
                continue
            g = m.groups()
            mon, day = (g[0], g[1]) if pat is SINGLE_PATTERNS[0] else (g[1], g[0])
            if (MONTHS[mon.lower().rstrip('.')[:3]], int(day)) in days:
                n += 1
    return n


def find_dates(text: str, year: int | None = None):
    """The page's main multi-day date range (in the given year, if any).
    Returns (start, end, position) or None. Single dates are ignored as candidates: on
    conference sites they are usually deadlines. When several ranges appear (site menus
    often list other events), the one the rest of the page backs up most wins; ties go to
    the earliest."""
    t = _clean(text)
    cands = []
    for kind, pat in RANGE_PATTERNS:
        for m in pat.finditer(t):
            g = m.groups()
            if kind == "d-d m y":
                d1, d2, mon, y = g; s = _mk(y, MONTHS[mon.lower()[:3]], d1); e = _mk(y, MONTHS[mon.lower()[:3]], d2)
            elif kind == "m d-d y":
                mon, d1, d2, y = g; s = _mk(y, MONTHS[mon.lower()[:3]], d1); e = _mk(y, MONTHS[mon.lower()[:3]], d2)
            elif kind == "d m-d m y":
                d1, m1, d2, m2, y = g; s = _mk(y, MONTHS[m1.lower()[:3]], d1); e = _mk(y, MONTHS[m2.lower()[:3]], d2)
            else:
                m1, d1, m2, d2, y = g; s = _mk(y, MONTHS[m1.lower()[:3]], d1); e = _mk(y, MONTHS[m2.lower()[:3]], d2)
            if not s or not e or e < s or (e - s).days > 14:
                continue
            if year and s.year != year:
                continue
            cands.append((s, e, m.start(), m.end()))
    if not cands:
        return None
    # each distinct range counts once per extra appearance as a range, plus day mentions
    def score(c):
        same = sum(1 for o in cands if (o[0], o[1]) == (c[0], c[1]) and o[2] != c[2])
        return same + _support(t, c[0], c[1], (c[2], c[3]))
    best = max(cands, key=lambda c: (score(c), -c[2]))
    return best[0], best[1], best[2]


def find_place(text: str, near: int | None = None, window: int = 300):
    """A known conference city mentioned near the dates (or in the first part of the page)."""
    t = _clean(text)
    if near is not None:
        seg = t[max(0, near - window): near + window]
        centre = min(near, window)
    else:
        seg = t[:2500]
        centre = 0
    best = None
    for city, country in CITIES.items():
        for m in re.finditer(r"(?<![A-Za-z])" + re.escape(city) + r"(?![A-Za-z])", seg):
            dist = abs(m.start() - centre)
            if best is None or dist < best[2]:
                best = (city, country, dist)
            break
    return (best[0], best[1]) if best else None


def describe_edition(text: str, year: int | None):
    """Dates and place read off an edition's page."""
    found = find_dates(text, year)
    info = {}
    if found:
        s, e, pos = found
        info.update(start=s.isoformat(), end=e.isoformat())
        place = find_place(text, near=pos)
    else:
        place = find_place(text)
    if place:
        info.update(city=place[0], country=place[1])
    return info
