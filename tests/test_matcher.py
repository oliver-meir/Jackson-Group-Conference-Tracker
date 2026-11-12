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
                      "REPORT": repo / "report.md", "WATCHLIST": repo / "watchlist.yaml"}.items():
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
