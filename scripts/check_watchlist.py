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
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matcher import find_all, normalise  # noqa: E402
import build_outputs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRACKER = DATA / "tracker.json"
HASHES = DATA / "page_hashes.json"
STATUS = DATA / "check_status.json"
REPORT = ROOT / "report.md"
WATCHLIST = ROOT / "watchlist.yaml"

USER_AGENT = "LabConferenceTracker/1.0 (academic, weekly check; contact via repo)"
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


def check_conferences(watchlist, tracker, getter, report, errors, only=None):
    profs = [p for p in tracker["profs"] if not p.get("paused") and (only is None or p["id"] == only)]
    rejected = set(tracker.get("rejected") or [])
    new_rows = []
    for watch in watchlist.get("conferences", []):
        if not watch.get("enabled", True):
            continue
        urls = watch.get("urls") or []
        texts = []
        for idx, url in enumerate(urls):
            try:
                texts.append((url, getter(url, f'{watch["id"]}' + (f"-{idx}" if idx else ""))))
            except Exception as e:  # noqa: BLE001
                errors.append(f'{watch["id"]}: could not read {url} ({type(e).__name__}: {str(e)[:120]})')
        for url, text in texts:
            if len(text) < 200:
                errors.append(f'{watch["id"]}: {url} returned almost no text (page may need a browser or has moved)')
                continue
            for m in find_all(text, profs):
                if m.prof_id in (watch.get("ignore") or []) or f'{watch["id"]}:{m.prof_id}' in rejected:
                    continue
                if already_logged(tracker["apps"] + new_rows, m.prof_id, watch):
                    continue
                row = {
                    "id": f'{watch["id"]}-{m.prof_id}',
                    "profId": m.prof_id,
                    "watchId": watch["id"],
                    "conference": watch["conference"],
                    "startDate": str(watch.get("start") or ""),
                    "endDate": str(watch.get("end") or ""),
                    "city": watch.get("city", ""),
                    "country": watch.get("country", ""),
                    "role": m.role,
                    "confidence": "Unverified",
                    "check": "looks right" if m.confident else f"check carefully: {m.reason}",
                    "sourceUrl": url,
                    "notes": f"Auto-found {dt.date.today().isoformat()}: {m.snippet[:220]}",
                }
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
    ap.add_argument("--only", help="check just this professor id (used when someone adds a professor on the website)")
    args = ap.parse_args(argv)

    watchlist = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or {}
    tracker = load_json(TRACKER, {"profs": [], "apps": []})
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
    new_rows = check_conferences(watchlist, tracker, getter, report, errors, only=args.only)
    if not args.only:
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
    if new_rows or args.only:
        save_json(TRACKER, tracker)
    if not args.only:
        save_json(HASHES, hashes)
        save_json(STATUS, {"unreadable_pages": sorted(e.split(" (")[0] for e in errors)})
    build_outputs.main(geocode=not args.no_geocode and not args.from_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
