"""Weekly check: fetch every page in watchlist.yaml, look for tracked professors,
add new matches to data/tracker.json as "Unverified", and write report.md.

Run locally:   python scripts/check_watchlist.py
Dry run:       python scripts/check_watchlist.py --dry-run
Offline test:  python scripts/check_watchlist.py --from-dir tests/fixtures  (reads <watch id>.html/.txt/.pdf)

No LLM is used. Every new row is saved as "Unverified" for a lab member to Confirm or Remove on the website.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matcher import find_all, normalise  # noqa: E402
from editions import describe_edition, expand_series, site_furniture  # noqa: E402
import build_outputs  # noqa: E402
import photos  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRACKER = DATA / "tracker.json"
HASHES = DATA / "page_hashes.json"
STATUS = DATA / "check_status.json"
REPORT = ROOT / "report.md"
WATCHLIST = ROOT / "watchlist.yaml"
SERIES = DATA / "conferences.json"

USER_AGENT = "LabConferenceTracker/1.0 (academic conference tracker; https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "oliver-meir/Jackson-Group-Conference-Tracker") + ")"
EVENT_WORDS = r"\b(symposium|conference|congress|meeting|keynote|plenary|invited|workshop|summit|lecture|talk|speaker|gordon|keystone)\b"


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def pdf_to_text(data: bytes) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def fetch_text(url: str, session: requests.Session) -> str:
    r = session.get(url, timeout=40, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").lower()
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf") or r.content[:4] == b"%PDF":
        return pdf_to_text(r.content)
    return html_to_text(r.text)


def text_from_dir(directory: Path, key: str) -> str:
    for ext in (".html", ".htm", ".txt", ".pdf"):
        p = directory / f"{key}{ext}"
        if p.exists():
            if ext == ".pdf":
                return pdf_to_text(p.read_bytes())
            raw = p.read_text(encoding="utf-8", errors="ignore")
            return html_to_text(raw) if ext.startswith(".htm") else raw
    raise FileNotFoundError(f"no fixture for {key}")


def already_logged(apps: list[dict], prof_id: str, watch: dict) -> bool:
    conf_norm = normalise(watch["conference"])
    for a in apps:
        if a.get("profId") != prof_id:
            continue
        if a.get("watchId") == watch["id"]:
            return True
        if normalise(a.get("conference", "")) == conf_norm:
            return True
    return False


def refresh_row(apps, prof_id, watch, start, end_, city, country):
    """An auto-found row nobody has confirmed or edited yet follows the latest reading of
    the edition's dates and place, so a fix to the date reader also fixes old rows."""
    for a in apps:
        if a.get("profId") != prof_id or a.get("watchId") != watch["id"]:
            continue
        if a.get("confidence") != "Unverified" or a.get("edited") or not a.get("series"):
            return
        new = dict(startDate=start, endDate=end_, city=city, country=country)
        if any(a.get(k, "") != v for k, v in new.items()):
            a.update(new)
            return True
        return False


def _wm_json(url, params):
    """Wikimedia API call (they ask for a descriptive User-Agent)."""
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.json()


def _wm_bytes(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "")


def _not_published(exc) -> bool:
    """For next/last year's edition, a missing page just means it is not online (yet)."""
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (404, 410):
        return True
    return isinstance(exc, (requests.ConnectionError,))


def build_entries(watchlist, series_list, only_series=None, today=None):
    """Everything to check this run: fixed conferences from watchlist.yaml plus every edition of
    the conference series added on the website (data/conferences.json)."""
    entries = []
    if only_series is None:
        for w in watchlist.get("conferences", []) or []:
            if w.get("enabled", True):
                entries.append(dict(w, optional=False, series=None, year=None))
    for srs in series_list:
        if only_series is not None and srs["id"] != only_series:
            continue
        if not srs.get("enabled", True):
            continue
        entries.extend(expand_series(srs, today))
    return entries


def check_conferences(entries, tracker, getter, report, errors, only=None, editions_out=None):
    profs = [p for p in tracker["profs"] if not p.get("paused") and (only is None or p["id"] == only)]
    rejected = set(tracker.get("rejected") or [])
    new_rows = []
    # read every page once, then work out which date ranges are site furniture per series
    cache = {}

    def fetch(url, key):
        if url not in cache:
            try:
                cache[url] = (True, getter(url, key))
            except Exception as e:  # noqa: BLE001
                cache[url] = (False, e)
        ok, val = cache[url]
        if not ok:
            raise val
        return val

    furniture = {}
    by_series = {}
    for watch in entries:
        if watch.get("series"):
            by_series.setdefault(watch["series"], []).append(watch)
    for sid, ws in by_series.items():
        pages = []
        for watch in ws:
            parts = []
            for idx, url in enumerate(watch.get("urls") or []):
                try:
                    parts.append(fetch(url, f'{watch["id"]}' + (f"-{idx}" if idx else "")))
                except Exception:  # noqa: BLE001
                    pass
            if parts:
                pages.append(" ".join(parts))
        furniture[sid] = site_furniture(pages) if len(pages) >= 2 else set()

    for watch in entries:
        urls = watch.get("urls") or []
        texts, problems, missing = [], [], 0
        for idx, url in enumerate(urls):
            try:
                texts.append((url, fetch(url, f'{watch["id"]}' + (f"-{idx}" if idx else ""))))
            except Exception as e:  # noqa: BLE001
                if watch.get("optional") and _not_published(e):
                    missing += 1
                else:
                    problems.append(f'{watch["id"]}: could not read {url} ({type(e).__name__}: {str(e)[:120]})')
        good = []
        for url, text in texts:
            if len(text) < 200:
                problems.append(f'{watch["id"]}: {url} returned almost no text (page may need a browser or has moved)')
            elif watch.get("year") and str(watch["year"]) not in text:
                missing += 1  # site answered but shows another year's page: this edition is not online yet
            else:
                good.append((url, text))
        status = "found" if good else ("not published yet" if missing and not problems else "could not read")
        info = {}
        if good and watch.get("series"):
            info = describe_edition(" ".join(t for _, t in good), watch.get("year"),
                                    furniture.get(watch["series"], set()))
        start = str(watch.get("start") or info.get("start") or "")
        end_ = str(watch.get("end") or info.get("end") or "")
        city = watch.get("city") or info.get("city", "")
        country = watch.get("country") or info.get("country", "")
        if editions_out is not None and watch.get("series"):
            editions_out.setdefault(watch["series"], {})[str(watch.get("year") or "page")] = {
                k: v for k, v in {"status": status, "start": start, "end": end_, "city": city,
                                  "country": country, "urls": urls, "problems": problems}.items() if v}
        errors.extend(problems)
        for url, text in good:
            for m in find_all(text, profs):
                if m.prof_id in (watch.get("ignore") or []) or f'{watch["id"]}:{m.prof_id}' in rejected:
                    continue
                if already_logged(tracker["apps"] + new_rows, m.prof_id, watch):
                    if refresh_row(tracker["apps"], m.prof_id, watch, start, end_, city, country):
                        report["refreshed"] = report.get("refreshed", 0) + 1
                    continue
                row = {
                    "id": f'{watch["id"]}-{m.prof_id}',
                    "profId": m.prof_id,
                    "watchId": watch["id"],
                    "conference": watch["conference"],
                    "startDate": start,
                    "endDate": end_,
                    "city": city,
                    "country": country,
                    "role": m.role,
                    "confidence": "Unverified",
                    "check": "looks right" if m.confident else f"check carefully: {m.reason}",
                    "sourceUrl": url,
                    "notes": f"Auto-found {dt.date.today().isoformat()}: {m.snippet[:220]}"
                             + ("" if watch.get("start") or not info.get("start") else " (dates read from the page, please check)"),
                }
                if watch.get("series"):
                    row["series"] = watch["series"]
                new_rows.append(row)
                report["matches"].append((watch, m, url))
    return new_rows


def check_prof_pages(watchlist, getter, hashes, report, errors):
    for entry in watchlist.get("prof_pages", []) or []:
        if not entry.get("enabled", True):
            continue
        url = entry["url"]
        key = f'page-{entry["prof"]}'
        try:
            text = getter(url, key)
        except Exception as e:  # noqa: BLE001
            errors.append(f'{entry["prof"]} page: could not read {url} ({type(e).__name__})')
            continue
        lines = [ln.strip() for ln in re.split(r"[\n\r]+|(?<=[.!?])\s{2,}", text) if ln.strip()]
        digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        old = hashes.get(url)
        if old and old["sha256"] != digest:
            added = [ln for ln in difflib.unified_diff(old.get("lines", []), lines, lineterm="", n=0)
                     if ln.startswith("+") and not ln.startswith("+++")]
            interesting = [ln[1:] for ln in added if re.search(EVENT_WORDS, ln.lower())]
            if interesting:
                report["page_changes"].append((entry, url, interesting[:15]))
        hashes[url] = {"sha256": digest, "lines": lines[:2000]}


def write_report(report, errors, new_rows, profs_by_id):
    out = [f"# Weekly conference check, {dt.date.today().isoformat()}", ""]
    if new_rows:
        out += [f"Found **{len(new_rows)} new possible appearance(s)**. They were added to the tracker as *Unverified*.",
                "", "Review them on the website: Confirm the right ones, Remove the wrong ones.", ""]
        out += ["| Professor | Conference | Role guess | Check | Snippet |", "|---|---|---|---|---|"]
        for watch, m, url in report["matches"]:
            name = profs_by_id.get(m.prof_id, {}).get("name", m.prof_id)
            flag = "looks right" if m.confident else f"**check carefully**: {m.reason}"
            snip = m.snippet.replace("|", "/")[:200]
            out.append(f'| {name} | [{watch["conference"]}]({url}) | {m.role} | {flag} | {snip} |')
        out.append("")
    if report["page_changes"]:
        out += ["## Professor pages that changed", "These lines are new and mention an event. Nothing was added automatically.", ""]
        for entry, url, lines in report["page_changes"]:
            name = profs_by_id.get(entry["prof"], {}).get("name", entry["prof"])
            out.append(f"**{name}** ([page]({url}))")
            out += [f"- {ln[:240]}" for ln in lines]
            out.append("")
    if errors:
        out += ["## Pages that could not be read", "Update or disable these URLs in `watchlist.yaml`.", ""]
        out += [f"- {e}" for e in errors]
        out.append("")
    if not new_rows and not report["page_changes"] and not errors:
        out.append("No new matches this week.")
    REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="do not write any files except report.md")
    ap.add_argument("--from-dir", type=Path, help="read page text from local files named <id>.html/.txt/.pdf")
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--no-photos", action="store_true", help="skip looking up professor photos on Wikidata")
    ap.add_argument("--only", help="check just this professor id (used when someone adds a professor on the website)")
    ap.add_argument("--conference", help="check just this conference series id from data/conferences.json, for every professor")
    args = ap.parse_args(argv)

    watchlist = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or {}
    tracker = load_json(TRACKER, {"profs": [], "apps": []})
    series_list = load_json(SERIES, [])
    hashes = load_json(HASHES, {})
    report = {"matches": [], "page_changes": []}
    errors: list[str] = []

    if args.from_dir:
        getter = lambda url, key: text_from_dir(args.from_dir, key)  # noqa: E731
    else:
        session = requests.Session()

        def getter(url, key):
            time.sleep(1.5)  # be polite to conference sites
            return fetch_text(url, session)

    if args.only and not any(p["id"] == args.only for p in tracker["profs"]):
        print(f"professor {args.only!r} not found in tracker.json")
        return 1
    if args.conference and not any(x["id"] == args.conference for x in series_list):
        print(f"conference {args.conference!r} not found in conferences.json")
        return 1
    editions: dict = {}
    entries = build_entries(watchlist, series_list, only_series=args.conference)
    new_rows = check_conferences(entries, tracker, getter, report, errors, only=args.only, editions_out=editions)
    if not args.only and not args.conference:
        check_prof_pages(watchlist, getter, hashes, report, errors)
    profs_by_id = {p["id"]: p for p in tracker["profs"]}
    write_report(report, errors, new_rows, profs_by_id)

    print(f"{len(new_rows)} new rows, {len(report['page_changes'])} changed pages, {len(errors)} errors")
    if args.dry_run:
        return 0
    if new_rows:
        tracker["apps"].extend(new_rows)
        tracker["generated"] = dt.date.today().isoformat()
    if args.only:
        for p in tracker["profs"]:
            if p["id"] == args.only:
                p["lastChecked"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                p["lastCheckHits"] = len(new_rows)
                p["lastCheckErrors"] = len(errors)
    if not args.from_dir and not args.no_photos and not args.conference:
        report["photos"] = photos.fill_photos(tracker["profs"], ROOT / "photos", _wm_json, _wm_bytes, only=args.only)
    if new_rows or args.only or report.get("refreshed") or report.get("photos"):
        save_json(TRACKER, tracker)
    if not args.only and editions:
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for srs in series_list:
            if srs["id"] in editions:
                srs["editions"] = editions[srs["id"]]
                srs["lastChecked"] = now
                srs["lastCheckHits"] = sum(1 for r in new_rows if r.get("series") == srs["id"])
                srs["lastCheckErrors"] = [p for e in editions[srs["id"]].values() for p in e.get("problems", [])][:10]
        save_json(SERIES, series_list)
    if not args.only and not args.conference:
        save_json(HASHES, hashes)
        save_json(STATUS, {"unreadable_pages": sorted(e.split(" (")[0] for e in errors)})
    build_outputs.main(geocode=not args.no_geocode and not args.from_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
