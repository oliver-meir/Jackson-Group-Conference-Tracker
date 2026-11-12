"""Find a freely licensed photo for each professor on Wikidata / Wikimedia Commons.

No LLM and no scraping: Wikidata is an open database, and the photo it links to (property
P18) is on Wikimedia Commons under a free licence. The photo is saved as
photos/<professor id>.jpg (or .png) with its credit, which the website shows on the
professor's page.

A photo is only used when the match is clear: the Wikidata entry must be a person, be
described as a scientist/professor of some kind, and either be the only such person with
that name or name the professor's university. Otherwise nothing is saved and the initials
stay. Photos someone added by hand are never replaced, and anyone can switch the automatic
photo off for one professor by setting "autoPhoto": false in data/tracker.json.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

WD_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
RETRY_DAYS = 30  # people without a photo are looked up again after this many days
RULES = 2  # bump when the matching improves, so everyone without a photo is retried

SCIENCE = re.compile(
    r"\b(scientist|chemist|biologist|biochemist|biophysicist|physicist|engineer|bioengineer|"
    r"professor|researcher|academic|geneticist|immunologist|pharmacologist|neuroscientist|"
    r"computer scientist|mathematician|structural biologist|molecular biologist|microbiologist)\b", re.I)
STOP = {"university", "of", "the", "and", "institute", "college", "school", "at", "for", "de", "national",
        "department", "state", "faculty", "center", "centre", "research", "also", "medical", "technology"}


def _tokens(text: str) -> set[str]:
    words = {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in STOP}
    for part in re.split(r"[/,;()]| also ", text or ""):  # "Broad Institute / MIT" -> also "bi", "mit"
        a = _acronym(part.strip())
        if len(a) >= 3:
            words.add(a)
    return words


def _acronym(name: str) -> str:
    """Massachusetts Institute of Technology -> mit, University of Illinois Urbana-Champaign -> uiuc."""
    words = [w for w in re.findall(r"[A-Za-z]+", name or "") if w.lower() not in {"of", "the", "and", "at", "for", "de"}]
    return "".join(w[0] for w in words).lower() if len(words) >= 2 else ""


def _claims(entity: dict, prop: str) -> list:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if v is not None:
            out.append(v)
    return out


def _label(entity: dict) -> str:
    return entity.get("labels", {}).get("en", {}).get("value", "")


def _desc(entity: dict) -> str:
    return entity.get("descriptions", {}).get("en", {}).get("value", "")


def find_photo(name: str, affiliation: str, get_json) -> dict | None:
    """Return {"file": <Commons file name>, "qid": ...} for a clear match, else None.
    get_json(url, params) -> parsed JSON (injected so tests run offline)."""
    found = get_json(WD_API, {"action": "wbsearchentities", "search": name, "language": "en",
                              "type": "item", "limit": 10, "format": "json"})
    ids = [r["id"] for r in found.get("search", [])][:10]
    if not ids:
        return None
    ents = get_json(WD_API, {"action": "wbgetentities", "ids": "|".join(ids),
                             "props": "claims|labels|descriptions", "languages": "en", "format": "json"}).get("entities", {})
    people = []
    for qid in ids:
        e = ents.get(qid) or {}
        is_human = any(v.get("id") == "Q5" for v in _claims(e, "P31") if isinstance(v, dict))
        if not is_human or not SCIENCE.search(_desc(e)):
            continue
        people.append((qid, e))
    if not people:
        return None
    # look up employer / education names so the university can be compared
    org_ids = sorted({v["id"] for _, e in people for p in ("P108", "P1416") for v in _claims(e, p) if isinstance(v, dict) and "id" in v})
    orgs = {}
    for i in range(0, len(org_ids), 50):
        orgs.update(get_json(WD_API, {"action": "wbgetentities", "ids": "|".join(org_ids[i:i + 50]),
                                      "props": "labels", "languages": "en", "format": "json"}).get("entities", {}))
    aff = _tokens(affiliation)

    def aff_match(e):
        if not aff:
            return False
        words = _tokens(_desc(e))
        for p in ("P108", "P1416"):
            for v in _claims(e, p):
                if isinstance(v, dict) and v.get("id") in orgs:
                    org = _label(orgs[v["id"]])
                    words |= _tokens(org)
                    if _acronym(org):
                        words.add(_acronym(org))
        return len(aff & words) >= 1

    matching = [(q, e) for q, e in people if aff_match(e)]
    if len(matching) >= 1:
        pick = matching[0]
    elif len(people) == 1:
        pick = people[0]
    else:
        return None  # several scientists with this name and none names the university: too risky
    files = [v for v in _claims(pick[1], "P18") if isinstance(v, str)]
    if not files:
        return None
    return {"file": files[0], "qid": pick[0]}


def credit_for(file: str, get_json) -> dict:
    data = get_json(COMMONS_API, {"action": "query", "titles": f"File:{file}", "prop": "imageinfo",
                                  "iiprop": "extmetadata|url", "iiurlwidth": 320, "format": "json"})
    page = next(iter(data.get("query", {}).get("pages", {}).values()), {})
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata", {})

    def plain(k):
        return re.sub(r"<[^>]+>", "", meta.get(k, {}).get("value", "")).strip()
    return {"thumb": info.get("thumburl") or info.get("url", ""),
            "page": info.get("descriptionurl", f"https://commons.wikimedia.org/wiki/File:{file.replace(' ', '_')}"),
            "artist": plain("Artist")[:120], "license": plain("LicenseShortName")[:60]}


def has_manual_photo(prof: dict, photos_dir: Path) -> bool:
    if prof.get("photo") and not prof.get("photoAuto"):
        return True
    return any((photos_dir / f'{prof["id"]}{ext}').exists() for ext in (".jpg", ".png")) and not prof.get("photoAuto")


def fill_photos(profs: list[dict], photos_dir: Path, get_json, get_bytes, only: str | None = None,
                today: dt.date | None = None, log=print) -> int:
    """Look up photos for professors that have none. Returns how many were added or changed
    (including "looked, found nothing" stamps, so the caller knows to save)."""
    today = today or dt.date.today()
    changed = 0
    for p in profs:
        if only and p["id"] != only:
            continue
        if p.get("autoPhoto") is False or p.get("photo") == "none" or has_manual_photo(p, photos_dir) or p.get("photoAuto"):
            continue
        last = p.get("photoChecked")
        if last and not only and p.get("photoRules", 1) >= RULES:
            try:
                if (today - dt.date.fromisoformat(last)).days < RETRY_DAYS:
                    continue
            except ValueError:
                pass
        try:
            hit = find_photo(p["name"], p.get("affiliation", ""), get_json)
            if hit:
                cr = credit_for(hit["file"], get_json)
                data, ctype = get_bytes(cr["thumb"])
                ext = ".png" if "png" in (ctype or "") else ".jpg"
                if len(data) < 2000:
                    raise ValueError("image too small")
                photos_dir.mkdir(exist_ok=True)
                for old in (".jpg", ".png"):
                    (photos_dir / f'{p["id"]}{old}').unlink(missing_ok=True)
                (photos_dir / f'{p["id"]}{ext}').write_bytes(data)
                p.update(photo=f'{p["id"]}{ext}', photoAuto=True, photoSource=cr["page"],
                         photoCredit=", ".join(x for x in (cr["artist"], cr["license"]) if x) + " via Wikimedia Commons",
                         photoWikidata=hit["qid"])
                log(f'photo: {p["name"]} <- {hit["file"]} ({hit["qid"]})')
            else:
                log(f'photo: no clear Wikidata match for {p["name"]}')
        except Exception as e:  # noqa: BLE001  (a photo is never worth failing the run for)
            log(f'photo: lookup failed for {p["name"]} ({type(e).__name__})')
        p["photoChecked"] = today.isoformat()
        p["photoRules"] = RULES
        changed += 1
    return changed
