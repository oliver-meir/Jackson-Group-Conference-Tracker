# Professor Conference Tracker

Tracks where a list of professors of interest have spoken at conferences, and where they are scheduled to speak. Anyone in the lab can add a professor on the website; the tracker then searches every conference program it knows about and shows the hits within a few minutes. A free weekly GitHub Action re-checks everyone every Monday. Lab members review hits on the website with Confirm or Remove. No AI or paid service is involved.

How the pieces fit:

```
website (GitHub Pages)  --->  helper (Cloudflare Worker, free)  --->  GitHub: saves data/tracker.json,
     reads live data  <---    holds the GitHub token secretly          starts "Check one professor" Action
                                                                   GitHub Actions: Python name matching
                                                                   over the pages in watchlist.yaml
```

Live site: once GitHub Pages is on, it is at `https://<your-username>.github.io/<repo-name>/`.

## What is in here

```
index.html                     the website (open it directly, or host it on GitHub Pages)
config.js                      website setting: the helper service address
helper/worker.js               the helper service (paste into Cloudflare)
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
scripts/save_results.sh        saves the checker's results, retrying if someone saved at the same time
tests/                         tests, including an offline sample program page
.github/workflows/             weekly-check.yml, check-professor.yml, rebuild-data.yml, tests.yml
```

## First-time setup (about 20 minutes)

1. **Make a GitHub account** at github.com if you do not have one.
2. **Create a repository:** the + menu, New repository. Name it e.g. `conference-tracker`. Public is simplest (free GitHub Pages). Private also works, but Pages on a private repo needs a paid plan; you can still open `index.html` from a download. Do not add a README (this one is included).
3. **Upload the files.** Easiest is **GitHub Desktop** (desktop.github.com): clone the new repo, copy all these files into the folder, Commit, Push. If you drag files into the browser instead, make sure the hidden `.github` folder goes too (on a Mac press Cmd+Shift+. in Finder to show hidden folders). Without it nothing runs automatically.
4. **Let Actions write:** repo Settings, Actions, General, Workflow permissions: choose **Read and write permissions**. Save.
5. **Turn on the website:** Settings, Pages, Source: Deploy from a branch, Branch: `main`, folder `/ (root)`. Save. The link appears on that page after a minute or two.
6. **Test the weekly check:** Actions tab, "Weekly conference check", Run workflow. When it finishes with a green tick, click it and read the Summary for the report.

**Moving it to the lab later:** when the lab's GitHub organisation is ready, an owner of the repo can use Settings, General, Transfer ownership. Everything (history, Actions, Pages) moves with it.

## Setting up the helper service (about 20 minutes, once)

This is what lets people add professors and confirm or remove hits straight from the website. Without it the site still works, read-only.

1. **Make a GitHub token** that can only touch this repo: GitHub, your profile picture, Settings, Developer settings, Personal access tokens, **Fine-grained tokens**, Generate new token. Name it `conference-tracker-helper`. Expiration: 1 year (put a reminder in your calendar to renew). Repository access: **Only select repositories**, pick this repo. Permissions, Repository permissions: **Contents: Read and write**, **Actions: Read and write**. Generate, and copy the token (it is shown once).
2. **Make a free Cloudflare account** at dash.cloudflare.com.
3. **Create the Worker:** Workers & Pages (or Compute), Create, Create Worker (Hello World). Name it `conference-tracker-helper`, Deploy. Then Edit code, delete everything in the editor, paste the whole of `helper/worker.js`, and click Deploy.
4. **Add its settings:** the Worker, Settings, Variables and Secrets, Add:
   - `GITHUB_TOKEN` (type Secret): the token from step 1
   - `LAB_PASSPHRASE` (type Secret): a phrase to share with the lab
   - `REPO` (type Text): `your-username/your-repo-name`
   - `ALLOWED_ORIGIN` (type Text): your site's origin, e.g. `https://your-username.github.io` (no path)
   Deploy again if asked.
5. **Test it:** open the Worker's address (shown on its page, ending `.workers.dev`) followed by `/data` in a browser. You should see the tracker's data as text.
6. **Point the website at it:** edit `config.js` in the repo and set `helperUrl` to the Worker address (without `/data`). Commit. After a minute, the site shows an "Add a professor and search" button.

If the token expires or is revoked, the site keeps showing data but saving fails with a message. Make a new token and replace `GITHUB_TOKEN` in Cloudflare.

## Using the site

- **Add a professor:** type the name in the box on the left, click Add and search, add their university. Hits show up in 2 to 3 minutes. They are in the Monday check from then on.
- **Review hits:** new hits are Unverified, flagged "Looks right" or "Check carefully". Click Confirm or Remove. Removed hits are remembered in `rejected` in tracker.json and not suggested again.
- **Search again:** on a professor's timeline, "Search conferences again" re-runs the check for them (useful after adding conferences to the watchlist).

## Weekly routine (5 minutes)

Every Monday morning the weekly check runs for every professor and saves new hits straight to the site as Unverified. Open the site, tick nothing, and look for Unverified entries (or untick "Hide unverified" if it is on), then Confirm or Remove them. The run's report, including any conference pages that could not be read, is on GitHub: Actions tab, the latest "Weekly conference check", Summary. Fix or disable unreadable pages in `watchlist.yaml`.

## Common edits

- **Add a conference:** edit `watchlist.yaml` on GitHub (pencil icon), copy a block, change `id`, name, dates, city, and `urls` (the program or invited-speakers page, HTML or PDF). Once a year, add the next edition's block when its site goes up.
- **Fix an entry:** with the helper set up, use Edit on the website. Without it: edit on the website, click Export data (saves `tracker.json`), then upload it to the `data` folder on GitHub.
- **Fine-tune a professor:** Edit professor on the website to add "other name forms" (e.g. `C. J. Jackson`, accented or hyphenated spellings). In `tracker.json` you can also set `"context": ["Nagoya"]` (affiliation words that make a match "looks right") or `"paused": true` to stop checking someone.
- **Add a photo:** upload a square image to `photos/` named `<professor id>.jpg` or `.png` (ids are in tracker.json, e.g. `colin-jackson`). No photo means initials are shown. Only use images you have permission to use, e.g. ones the professor or their institution supplies for this purpose.
- **Watch a professor's own page:** add it under `prof_pages` in `watchlist.yaml` (a lab news page, CV, or "presentations" list). When it changes, new lines mentioning a conference or talk are listed in the weekly report (Actions tab, Summary). Nothing is added automatically from these.

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
- Common names will produce some false matches. That is why every new hit starts as Unverified until someone confirms it.

## Open-source tools used, and ones worth adding

Used: [Cloudflare Workers](https://developers.cloudflare.com/workers/) (free plan) for the helper, [requests](https://github.com/psf/requests), [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/), [pdfplumber](https://github.com/jsvine/pdfplumber), [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz), [PyYAML](https://github.com/yaml/pyyaml), [Leaflet](https://github.com/Leaflet/Leaflet) with [OpenStreetMap](https://www.openstreetmap.org/) tiles, [Nominatim](https://nominatim.org/) for city coordinates (max 1 request per second, cached).

Ideas for more features, all without an LLM:

- [changedetection.io](https://github.com/dgtlmoon/changedetection.io): self-hosted page-change monitor with email/Teams alerts and a browser engine for JavaScript-heavy sites. Worth running on a lab server if many conference sites turn out to need a browser.
- [Playwright for Python](https://github.com/microsoft/playwright-python): could be added to the weekly check to read JavaScript-built program pages.
- [ccf-deadlines](https://github.com/ccfddl/ccf-deadlines) and [ai-deadlines](https://github.com/paperswithcode/ai-deadlines): community-maintained conference date trackers with a YAML-per-conference format and calendar feeds. Good models if the lab also wants abstract and registration deadlines in the tracker.
- [OpenAlex](https://openalex.org/) (free API, Python client [pyalex](https://github.com/J535D165/pyalex)): could add each professor's recent papers and flag affiliation changes.
- [spaCy](https://github.com/explosion/spaCy) named-entity recognition (a small non-generative model): could list every speaker on a program, to spot new people worth tracking.

If an AI step is ever wanted, a small open-source model run locally (for example through [Ollama](https://github.com/ollama/ollama)) can be added as an optional extra. The tracker is designed to work without one.

## Etiquette

The check waits between requests and identifies itself in its User-Agent. All data comes from public pages. Before making the site public, the lab may want to decide whether a public page listing where named professors will be is appropriate; a private repo avoids the question.
