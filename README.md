# Professor Conference Tracker

Tracks where a list of professors of interest have spoken at conferences, and where they are scheduled to speak. A free weekly GitHub Action reads conference program pages, looks for the professors' names, and opens a pull request with anything new for a person to approve. No AI or paid service is involved.

Live site: once GitHub Pages is on, it is at `https://<your-username>.github.io/<repo-name>/`.

## What is in here

```
index.html                     the website (open it directly, or host it on GitHub Pages)
data/tracker.json              THE data: professors and appearances. Edit this one.
data/tracker.js                built from tracker.json for the website (do not edit)
data/talks.ics                 calendar of dated talks (built)
data/geocache.json             city coordinates for the map (built)
data/page_hashes.json          memory of watched professor pages (built by the weekly check)
photos/                        optional professor photos, named <professor id>.jpg
watchlist.yaml                 conference pages and professor pages the weekly check reads
scripts/check_watchlist.py     the weekly check
scripts/matcher.py             name matching rules
scripts/build_outputs.py       rebuilds tracker.js, talks.ics and map coordinates
tests/                         tests, including an offline sample program page
.github/workflows/             weekly-check.yml, rebuild-data.yml, tests.yml
```

## First-time setup (about 20 minutes)

1. **Make a GitHub account** at github.com if you do not have one.
2. **Create a repository:** the + menu, New repository. Name it e.g. `conference-tracker`. Public is simplest (free GitHub Pages). Private also works, but Pages on a private repo needs a paid plan; you can still open `index.html` from a download. Do not add a README (this one is included).
3. **Upload the files.** Easiest is **GitHub Desktop** (desktop.github.com): clone the new repo, copy all these files into the folder, Commit, Push. If you drag files into the browser instead, make sure the hidden `.github` folder goes too (on a Mac press Cmd+Shift+. in Finder to show hidden folders). Without it nothing runs automatically.
4. **Let Actions write:** repo Settings, Actions, General, Workflow permissions: choose **Read and write permissions** and tick **Allow GitHub Actions to create and approve pull requests**. Save.
5. **Turn on the website:** Settings, Pages, Source: Deploy from a branch, Branch: `main`, folder `/ (root)`. Save. The link appears on that page after a minute or two.
6. **Test the weekly check:** Actions tab, "Weekly conference check", Run workflow. When it finishes, look under Pull requests. On the first run it usually just records the watched professor pages.

**Moving it to the lab later:** when the lab's GitHub organisation is ready, an owner of the repo can use Settings, General, Transfer ownership. Everything (history, Actions, Pages) moves with it.

## Weekly routine (5 minutes)

On Monday a pull request called "Weekly check: new possible conference appearances" may appear. Its description lists each match with the professor, conference, guessed role, a text snippet, and a flag:

- **looks right:** full name found with an affiliation word nearby.
- **check carefully:** only initials, or no affiliation nearby, so it could be someone else with the same name.

For each row: open the source link, then in the pull request open `data/tracker.json` (Files changed, then the ... menu, Edit file) to fix the role or dates, set `"confidence"` to `"Confirmed"` if the official program names them, or delete the row if it is the wrong person (and add it to `ignore`, below, so it is not suggested again). Then **Merge**. If every match is wrong, **Close** the pull request, and stop it coming back by adding `ignore: [professor-id]` to that conference in `watchlist.yaml` (for example `ignore: [feng-zhang]`).

The pull request also lists pages that could not be read (moved, 404, or needing a browser). Fix or disable those in `watchlist.yaml`.

## Common edits

- **Add a conference:** edit `watchlist.yaml` on GitHub (pencil icon), copy a block, change `id`, name, dates, city, and `urls` (the program or invited-speakers page, HTML or PDF). Once a year, add the next edition's block when its site goes up.
- **Add or fix entries without code:** edit on the website, click **Export data** (saves `tracker.json`), then on GitHub open `data`, Add file, Upload files, upload it, Commit. The "Rebuild website data" action updates the site files.
- **Add a professor:** on the website, Add professor, fill in "other name forms" (e.g. `C. J. Jackson`, accented or hyphenated spellings), Export and upload. In `tracker.json` you can also add `"context": ["Nagoya"]`: affiliation words that make a match "looks right". Add `"paused": true` to stop checking someone.
- **Add a photo:** upload a square image to `photos/` named `<professor id>.jpg` or `.png` (ids are in tracker.json, e.g. `colin-jackson`). No photo means initials are shown. Only use images you have permission to use, e.g. ones the professor or their institution supplies for this purpose.
- **Watch a professor's own page:** add it under `prof_pages` in `watchlist.yaml` (a lab news page, CV, or "presentations" list). When it changes, new lines mentioning a conference or talk are listed in the pull request. Nothing is added automatically from these.

## Running it on your own computer (optional)

```
pip install -r requirements.txt
pytest -q                                          # tests, offline
python scripts/check_watchlist.py --dry-run        # real check, writes only report.md
python scripts/check_watchlist.py --from-dir tests/fixtures   # offline demo with the sample page
python scripts/build_outputs.py                    # rebuild tracker.js after editing tracker.json
```

## How the matching works, and its limits

`scripts/matcher.py` normalises the page text (lowercase, accents and punctuation removed) and searches for each professor's name forms as whole words: full name, first name plus surname, initials plus surname, and surname-first versions ("Jackson, C. J."). Surname particles such as "van der" are handled. A fuzzy pass with RapidFuzz catches small spelling slips in long surnames when the first name or initial is next to it. The role (keynote, plenary, chair, poster, invited) is guessed from the nearest keyword.

Limits worth knowing:

- It only reads pages listed in `watchlist.yaml`. It does not search the whole web. Google Alerts sent to a shared lab inbox (e.g. `"Shohei Koide" keynote OR plenary`) are a good free complement.
- Pages that build their content with JavaScript, or sit behind a login, return little text. They are reported as unreadable. The workaround is to paste the program into "Check a speaker list" on the website.
- Speaking roles are tracked. Plain attendance is almost never published.
- Common names will produce some false matches. That is why every new row starts as Unverified and goes through a pull request.

## Open-source tools used, and ones worth adding

Used: [requests](https://github.com/psf/requests), [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/), [pdfplumber](https://github.com/jsvine/pdfplumber), [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz), [PyYAML](https://github.com/yaml/pyyaml), [create-pull-request](https://github.com/peter-evans/create-pull-request), [Leaflet](https://github.com/Leaflet/Leaflet) with [OpenStreetMap](https://www.openstreetmap.org/) tiles, [Nominatim](https://nominatim.org/) for city coordinates (max 1 request per second, cached).

Ideas for more features, all without an LLM:

- [changedetection.io](https://github.com/dgtlmoon/changedetection.io): self-hosted page-change monitor with email/Teams alerts and a browser engine for JavaScript-heavy sites. Worth running on a lab server if many conference sites turn out to need a browser.
- [Playwright for Python](https://github.com/microsoft/playwright-python): could be added to the weekly check to read JavaScript-built program pages.
- [ccf-deadlines](https://github.com/ccfddl/ccf-deadlines) and [ai-deadlines](https://github.com/paperswithcode/ai-deadlines): community-maintained conference date trackers with a YAML-per-conference format and calendar feeds. Good models if the lab also wants abstract and registration deadlines in the tracker.
- [OpenAlex](https://openalex.org/) (free API, Python client [pyalex](https://github.com/J535D165/pyalex)): could add each professor's recent papers and flag affiliation changes.
- [spaCy](https://github.com/explosion/spaCy) named-entity recognition (a small non-generative model): could list every speaker on a program, to spot new people worth tracking.

If an AI step is ever wanted, a small open-source model run locally (for example through [Ollama](https://github.com/ollama/ollama)) can be added as an optional extra. The tracker is designed to work without one.

## Etiquette

The check waits between requests and identifies itself in its User-Agent. All data comes from public pages. Before making the site public, the lab may want to decide whether a public page listing where named professors will be is appropriate; a private repo avoids the question.
