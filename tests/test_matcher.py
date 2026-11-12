import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from matcher import find_professor, name_patterns, split_name  # noqa: E402

PROFS = {p["id"]: p for p in json.loads((ROOT / "data" / "tracker.json").read_text())["profs"]}


def find(text, pid):
    return find_professor(text, PROFS[pid])


def test_full_name_with_affiliation_is_confident():
    m = find("10:20 Colin J. Jackson, Australian National University: Enthalpy-entropy trade-offs", "colin-jackson")
    assert m and m.confident


def test_surname_first_with_initials():
    m = find("Speakers: Jackson, C. J. (ANU, Canberra); Smith, A. (Oxford)", "colin-jackson")
    assert m and m.confident


def test_different_first_name_is_not_a_match():
    assert find("Lauren Jackson, Vanderbilt University: Membrane remodeling", "colin-jackson") is None
    assert find("chair: Takaki Koide (Waseda University)", "shohei-koide") is None


def test_particle_surname():
    assert split_name("Wilfred A. van der Donk") == (["wilfred", "a"], ["van", "der", "donk"])
    assert find("Session chair: W. A. van der Donk (UIUC)", "wilfred-van-der-donk")
    assert find("Wilfred van der Donk, University of Illinois Urbana-Champaign", "wilfred-van-der-donk")


def test_accents_and_case():
    p = {"id": "x", "name": "José Müller", "aliases": [], "context": []}
    assert find_professor("JOSE MULLER (ETH)", p)


def test_small_misspelling_found_by_fuzzy_pass():
    m = find("Invited: Sergey Ovchinikov (MIT) on protein design", "sergey-ovchinnikov")
    assert m and "close spelling" in m.reason


def test_common_name_without_affiliation_is_flagged():
    m = find("Poster 112: Feng Zhang, Peking University", "feng-zhang")
    assert m and not m.confident
    m2 = find("Keynote: Feng Zhang, Broad Institute of MIT and Harvard", "feng-zhang")
    assert m2 and m2.confident and m2.role == "Keynote"


def test_role_guess():
    assert find("Plenary 2: Hiroaki Suga (University of Tokyo)", "hiroaki-suga").role == "Plenary"
    assert find("Poster session: Colin Jackson, ANU", "colin-jackson").role == "Poster"


def test_patterns_include_initial_forms():
    pats = dict(name_patterns("Colin J. Jackson", ["Colin Jackson"]))
    assert "c jackson" in pats and pats["c jackson"] is True
    assert "colin jackson" in pats and pats["colin jackson"] is False


def test_end_to_end_with_fixture(tmp_path, monkeypatch):
    """Run the whole weekly check against a local fixture page instead of the web."""
    import check_watchlist as cw
    import build_outputs as bo

    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    shutil.copy(ROOT / "data" / "tracker.json", repo / "data" / "tracker.json")
    shutil.copy(ROOT / "data" / "geocache.json", repo / "data" / "geocache.json")
    (repo / "watchlist.yaml").write_text(
        "conferences:\n"
        "  - id: demo-2027\n    conference: Demo Protein Meeting 2027\n    start: 2027-05-03\n"
        "    end: 2027-05-06\n    city: Boston\n    country: USA\n    urls: [https://example.org/program]\n"
        "prof_pages: []\n")
    for name in ("ROOT",):
        monkeypatch.setattr(cw, name, repo)
    monkeypatch.setattr(cw, "DATA", repo / "data")
    monkeypatch.setattr(cw, "TRACKER", repo / "data" / "tracker.json")
    monkeypatch.setattr(cw, "HASHES", repo / "data" / "page_hashes.json")
    monkeypatch.setattr(cw, "STATUS", repo / "data" / "check_status.json")
    monkeypatch.setattr(cw, "REPORT", repo / "report.md")
    monkeypatch.setattr(cw, "WATCHLIST", repo / "watchlist.yaml")
    monkeypatch.setattr(cw, "SERIES", repo / "data" / "conferences.json")
    monkeypatch.setattr(bo, "DATA", repo / "data")

    assert cw.main(["--from-dir", str(ROOT / "tests" / "fixtures")]) == 0
    data = json.loads((repo / "data" / "tracker.json").read_text())
    new = [a for a in data["apps"] if a.get("watchId") == "demo-2027"]
    assert {a["profId"] for a in new} == {"shohei-koide", "jeffrey-gray", "luke-lavis"}
    assert all(a["confidence"] == "Unverified" for a in new)
    roles = {a["profId"]: a["role"] for a in new}
    assert roles == {"jeffrey-gray": "Keynote", "shohei-koide": "Invited talk", "luke-lavis": "Session chair"}
    report = (repo / "report.md").read_text()
    assert "3 new possible appearance" in report
    assert (repo / "data" / "tracker.js").exists() and (repo / "data" / "talks.ics").exists()

    # second run finds nothing new
    assert cw.main(["--from-dir", str(ROOT / "tests" / "fixtures")]) == 0
    data2 = json.loads((repo / "data" / "tracker.json").read_text())
    assert len(data2["apps"]) == len(data["apps"])


def test_endowed_chair_is_not_session_chair():
    m = find("Wilfred A. van der Donk, Richard E. Heckert Endowed Chair in Chemistry at Illinois", "wilfred-van-der-donk")
    assert m and m.role != "Session chair"


def _repo(tmp_path, monkeypatch, tracker=None):
    import check_watchlist as cw
    import build_outputs as bo
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    data = tracker or json.loads((ROOT / "data" / "tracker.json").read_text())
    (repo / "data" / "tracker.json").write_text(json.dumps(data))
    shutil.copy(ROOT / "data" / "geocache.json", repo / "data" / "geocache.json")
    (repo / "watchlist.yaml").write_text(
        "conferences:\n"
        "  - id: demo-2027\n    conference: Demo Protein Meeting 2027\n    start: 2027-05-03\n"
        "    end: 2027-05-06\n    city: Boston\n    country: USA\n    urls: [https://example.org/program]\n"
        "prof_pages: []\n")
    for name, val in {"ROOT": repo, "DATA": repo / "data", "TRACKER": repo / "data" / "tracker.json",
                      "HASHES": repo / "data" / "page_hashes.json", "STATUS": repo / "data" / "check_status.json",
                      "REPORT": repo / "report.md", "WATCHLIST": repo / "watchlist.yaml",
                      "SERIES": repo / "data" / "conferences.json"}.items():
        monkeypatch.setattr(cw, name, val)
    monkeypatch.setattr(bo, "DATA", repo / "data")
    return cw, repo


def test_only_one_professor(tmp_path, monkeypatch):
    cw, repo = _repo(tmp_path, monkeypatch)
    assert cw.main(["--from-dir", str(ROOT / "tests" / "fixtures"), "--only", "shohei-koide"]) == 0
    data = json.loads((repo / "data" / "tracker.json").read_text())
    new = [a for a in data["apps"] if a.get("watchId") == "demo-2027"]
    assert [a["profId"] for a in new] == ["shohei-koide"]
    assert new[0]["check"] == "looks right"
    koide = next(p for p in data["profs"] if p["id"] == "shohei-koide")
    assert koide["lastChecked"].endswith("Z") and koide["lastCheckHits"] == 1
    assert cw.main(["--from-dir", str(ROOT / "tests" / "fixtures"), "--only", "nobody"]) == 1


def test_rejected_hits_are_not_suggested_again(tmp_path, monkeypatch):
    data = json.loads((ROOT / "data" / "tracker.json").read_text())
    data["rejected"] = ["demo-2027:jeffrey-gray"]
    cw, repo = _repo(tmp_path, monkeypatch, data)
    cw.main(["--from-dir", str(ROOT / "tests" / "fixtures")])
    out = json.loads((repo / "data" / "tracker.json").read_text())
    ids = {a["profId"] for a in out["apps"] if a.get("watchId") == "demo-2027"}
    assert ids == {"shohei-koide", "luke-lavis"}


def test_dates_and_places_read_off_pages():
    from editions import describe_edition, templatize
    assert templatize("https://proteins-2026.p.asnevents.com.au/") == ("https://proteins-{year}.p.asnevents.com.au/", True)
    assert templatize("https://ozpeptide.org/") == ("https://ozpeptide.org/", False)
    assert describe_edition("8–12 February 2026, Mantra Lorne. Deadline 30 October 2025", 2026) == \
        {"start": "2026-02-08", "end": "2026-02-12", "city": "Lorne", "country": "Australia"}
    assert describe_edition("Join us February 20-24, 2027 in Philadelphia", None)["start"] == "2027-02-20"
    assert describe_edition("6 - 11th of September 2026, Vienna", 2026)["end"] == "2026-09-11"
    assert describe_edition("Sunday 30 June – Wednesday 3 July 2027, Boston", 2027)["end"] == "2027-07-03"
    assert "start" not in describe_edition("Registration closes 1 March 2027.", 2027)


def test_conference_series_rolls_forward(tmp_path, monkeypatch):
    cw, repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(cw, "SERIES", repo / "data" / "conferences.json")
    monkeypatch.setenv("TRACKER_TODAY", "2026-11-12")
    (repo / "data" / "conferences.json").write_text(json.dumps([
        {"id": "lorne-proteins", "name": "Lorne Proteins Conference",
         "urls": ["https://proteins-{year}.p.asnevents.com.au/"], "enabled": True},
        {"id": "peptide", "name": "Australian Peptide Conference", "urls": ["https://ozpeptide.org/"], "enabled": True},
    ]))
    # fixtures exist for lorne-proteins-2026 and peptide-page; 2025 and 2027 editions are "not published"
    assert cw.main(["--from-dir", str(ROOT / "tests" / "fixtures"), "--conference", "lorne-proteins"]) == 0
    data = json.loads((repo / "data" / "tracker.json").read_text())
    hit = next(a for a in data["apps"] if a.get("watchId") == "lorne-proteins-2026")
    assert hit["profId"] == "colin-jackson" and hit["role"] == "Invited talk"
    assert (hit["startDate"], hit["endDate"], hit["city"]) == ("2026-02-08", "2026-02-12", "Lorne")
    assert hit["conference"] == "Lorne Proteins Conference 2026"
    series = json.loads((repo / "data" / "conferences.json").read_text())
    lorne = series[0]
    assert lorne["editions"]["2026"]["status"] == "found"
    assert lorne["editions"]["2027"]["status"] == "not published yet"
    assert lorne["lastCheckHits"] == 1 and lorne["lastCheckErrors"] == [] and lorne["lastChecked"]
    assert "lastChecked" not in series[1]  # only the requested series was checked

    # a page without a year in the link is checked as-is, and dates/places come off the page
    assert cw.main(["--from-dir", str(ROOT / "tests" / "fixtures"), "--conference", "peptide"]) == 0
    data = json.loads((repo / "data" / "tracker.json").read_text())
    suga = next(a for a in data["apps"] if a.get("watchId") == "peptide-page")
    assert suga["profId"] == "hiroaki-suga" and suga["city"] == "Port Douglas" and suga["startDate"] == "2027-10-17"

    # adding a professor later checks them against the series too
    data["profs"].append({"id": "x-tester", "name": "Lauren Smith", "aliases": [], "context": []})
    (repo / "data" / "tracker.json").write_text(json.dumps(data))
    assert cw.main(["--from-dir", str(ROOT / "tests" / "fixtures"), "--only", "x-tester"]) == 0
    data = json.loads((repo / "data" / "tracker.json").read_text())
    assert any(a["profId"] == "x-tester" and a["watchId"] == "lorne-proteins-2026" for a in data["apps"])


def test_chair_label_of_neighbour_is_not_used():
    p = {"id": "nt", "name": "Nobuhiko Tokuriki", "aliases": [], "context": ["British Columbia"]}
    text = ("10:45 AM Protein Ancestry and Adaptation Randy Stockbridge, University of Michigan, USA, Chair "
            "Banu Ozkan, Arizona State University, USA Nobuhiko Tokuriki, Michael Smith Laboratories, University of British Columbia")
    m = find_professor(text, p)
    assert m and m.role != "Session chair"


def test_dates_ignore_site_menu_events():
    """AIChE pages list other events in their menu before the real banner (SEED 2026 bug)."""
    from editions import describe_edition
    page = ("Events 2026 AIChE Annual Meeting November 8-12, 2026 Minneapolis Convention Center, "
            "Minneapolis, MN 2027 Spring Meeting March 21-25, 2027 Oklahoma City "
            "2026 SEED Technical Program June 15-18, 2026 | Hyatt Regency Denver at Colorado "
            "Convention Center, Denver, CO June 15, 2026 Day 1 Keynote Hiroaki Suga June 16, 2026 Day 2")
    info = describe_edition(page, 2026)
    assert info["start"] == "2026-06-15" and info["end"] == "2026-06-18"
    assert info["city"] == "Denver"


def test_series_ignores_site_menu_and_fixes_old_rows(tmp_path, monkeypatch):
    """SEED 2026 bug: AIChE's Events menu (other meetings, other cities) is on every page.
    Ranges repeated across editions are site furniture; the real banner wins. An old
    unconfirmed row with wrong dates/place is corrected on the next check."""
    menu = ('<nav><ul class="menu"><li>2026 AIChE Annual Meeting November 8-12, 2026 Minneapolis Convention '
            'Center, Minneapolis, MN</li><li>2027 Spring Meeting March 21-25, 2027 Oklahoma City</li></ul></nav>')
    fx = tmp_path / "fx"; fx.mkdir()
    (fx / "seed-2025.html").write_text(f"<html><body>{menu}<h1>2025 SEED</h1><p>June 23-26, 2025 | Houston, TX</p>"
                                       "<p>June 23, 2025 Day 1 opening remarks and a long program follows " + "x " * 120 + "</p></body></html>")
    (fx / "seed-2026.html").write_text(f"<html><body>{menu}<h1>2026 SEED</h1><p>June 15-18, 2026 | Hyatt Regency Denver, Denver, CO</p>"
                                       "<p>June 15, 2026 Day 1 KEYNOTE Hiroaki Suga, University of Tokyo, Co-founder of Peptidream "
                                       + "x " * 120 + "</p></body></html>")
    (fx / "seed-2027.html").write_text(f"<html><body>{menu}<h1>2027 SEED</h1><p>Last year: 2026 SEED, June 15-18, 2026, Denver. Program coming soon for 2027 " + "x " * 120 + "</p></body></html>")
    tracker = {"profs": [{"id": "hiroaki-suga", "name": "Hiroaki Suga", "aliases": [], "affiliation": "University of Tokyo",
                          "context": ["Tokyo", "peptide"]}],
               "apps": [{"id": "seed-2026-hiroaki-suga", "profId": "hiroaki-suga", "watchId": "seed-2026", "series": "seed",
                         "conference": "SEED 2026", "startDate": "", "endDate": "", "city": "Tokyo", "country": "Japan",
                         "confidence": "Unverified"}]}
    cw, repo = _repo(tmp_path, monkeypatch, tracker)
    monkeypatch.setattr(cw, "SERIES", repo / "data" / "conferences.json")
    monkeypatch.setenv("TRACKER_TODAY", "2026-11-12")
    (repo / "data" / "conferences.json").write_text(json.dumps([
        {"id": "seed", "name": "SEED", "urls": ["https://synbioconference.org/{year}/technical-program"], "enabled": True}]))
    assert cw.main(["--from-dir", str(fx), "--conference", "seed", "--no-geocode"]) == 0
    eds = json.loads((repo / "data" / "conferences.json").read_text())[0]["editions"]
    assert (eds["2026"]["start"], eds["2026"]["end"], eds["2026"]["city"]) == ("2026-06-15", "2026-06-18", "Denver")
    assert (eds["2025"]["start"], eds["2025"]["city"]) == ("2025-06-23", "Houston")
    assert "start" not in eds["2027"] and "city" not in eds["2027"]  # menu dates/cities never leak in
    row = next(a for a in json.loads((repo / "data" / "tracker.json").read_text())["apps"] if a["id"] == "seed-2026-hiroaki-suga")
    assert (row["startDate"], row["endDate"], row["city"], row["country"]) == ("2026-06-15", "2026-06-18", "Denver", "USA")


def _fake_wikimedia(people, orgs=None):
    """people: {qid: (label, description, [employer qids], image or None)}"""
    orgs = orgs or {}

    def get_json(url, params):
        if params.get("action") == "wbsearchentities":
            return {"search": [{"id": q} for q, v in people.items() if v[0].lower() == params["search"].lower()]}
        if params.get("action") == "wbgetentities":
            out = {}
            for q in params["ids"].split("|"):
                if q in people:
                    label, desc, emp, img = people[q]
                    claims = {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}],
                              "P108": [{"mainsnak": {"datavalue": {"value": {"id": e}}}} for e in emp]}
                    if img:
                        claims["P18"] = [{"mainsnak": {"datavalue": {"value": img}}}]
                    out[q] = {"labels": {"en": {"value": label}}, "descriptions": {"en": {"value": desc}}, "claims": claims}
                elif q in orgs:
                    out[q] = {"labels": {"en": {"value": orgs[q]}}}
            return {"entities": out}
        if params.get("action") == "query":
            return {"query": {"pages": {"1": {"imageinfo": [{"thumburl": "https://upload.example/x.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:X.jpg",
                    "extmetadata": {"Artist": {"value": "<a>Jane Photographer</a>"}, "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]}}}}
        raise AssertionError(params)
    return get_json


def test_photos_only_for_clear_matches(tmp_path):
    from photos import fill_photos
    people = {
        "Q1": ("Huimin Zhao", "American chemical engineer", ["Q100"], "Zhao.jpg"),
        "Q2": ("Huimin Zhao", "Chinese chemist", ["Q200"], "Other.jpg"),          # same name, other university
        "Q3": ("Alex Smith", "British biologist", [], "Smith.jpg"),
        "Q4": ("Alex Smith", "American physicist", [], "Smith2.jpg"),             # two, no university match: skip
        "Q5": ("Pat Lee", "American footballer", [], "Lee.jpg"),                   # not a scientist: skip
        "Q6": ("Robin Roe", "Australian biochemist", [], "Roe.jpg"),               # only one scientist: ok
    }
    orgs = {"Q100": "University of Illinois Urbana-Champaign", "Q200": "Peking University"}
    profs = [{"id": "huimin-zhao", "name": "Huimin Zhao", "affiliation": "University of Illinois Urbana-Champaign"},
             {"id": "alex-smith", "name": "Alex Smith", "affiliation": "ANU"},
             {"id": "pat-lee", "name": "Pat Lee", "affiliation": "MIT"},
             {"id": "robin-roe", "name": "Robin Roe", "affiliation": "Somewhere"},
             {"id": "has-own", "name": "Robin Roe", "affiliation": "x"},
             {"id": "removed", "name": "Robin Roe", "affiliation": "x", "photo": "none", "photoAuto": True}]
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "has-own.jpg").write_bytes(b"mine")
    got = []
    n = fill_photos(profs, tmp_path / "photos", _fake_wikimedia(people, orgs),
                    lambda url: (got.append(url) or b"\xff\xd8" + b"0" * 5000, "image/jpeg"), log=lambda *_: None)
    by = {p["id"]: p for p in profs}
    assert by["huimin-zhao"]["photo"] == "huimin-zhao.jpg" and by["huimin-zhao"]["photoWikidata"] == "Q1"
    assert by["huimin-zhao"]["photoCredit"] == "Jane Photographer, CC BY-SA 4.0 via Wikimedia Commons"
    assert (tmp_path / "photos" / "huimin-zhao.jpg").exists()
    assert "photo" not in by["alex-smith"] and by["alex-smith"]["photoChecked"]
    assert "photo" not in by["pat-lee"]
    assert by["robin-roe"]["photoWikidata"] == "Q6"
    assert "photo" not in by["has-own"] and (tmp_path / "photos" / "has-own.jpg").read_bytes() == b"mine"
    assert by["removed"]["photo"] == "none"
    assert n == 4 and len(got) == 2
    # next run within 30 days does not look again
    assert fill_photos(profs, tmp_path / "photos", _fake_wikimedia(people, orgs), lambda u: 1 / 0, log=lambda *_: None) == 0
