# Changelog

## [1.1.0] - 2026-09-12 (local, not yet deployed)

Driven by the Debugging data of the week of 2026-09-05: 11 of 45 user runs failed, and
the causes were not what the success rate suggested.

### Fixed
- Runs started through Apify's AI channel (`meta.origin = APIFY_AI`) no longer fail at the
  end. SDK 3.x validates the run object against an enum that does not know that origin and
  raised inside `Actor.set_status_message` after all rows were pushed; 8 finished runs a
  week were marked FAILED. The status call is now wrapped and can never fail the run.
- Several usernames in one run are searched one by one. The scanner treats "alice bob" as a
  single handle containing a space and reported `instagram.com/alice bob` as found; a
  six-username comma list from a real user returned nothing for that reason.
- Email addresses typed into the username box (3 failed runs from one user in 5 days) are
  no longer an error: the part before the @ is searched and the summary points to
  holehe-email-osint for the address itself.
- Category and country filters work again. The scanner's own `--type` and `--countries`
  options select zero sites in version 0.45, so the site list is built here from the
  scanner's sites.json on every run and passed down as `--websites` with exact URL
  patterns (a bare host token would over-match: `t.me` also selects `about.me`).
- `slow` and `special` scan modes removed from the form: both return no data in the current
  scanner. A legacy `mode` value in API input is accepted and noted, and the fast scan runs.
- Time limit is respected: usernames that cannot start before the limit are listed in the
  summary instead of the platform killing the run with nothing written.

### Added
- Progress and chunked delivery. Each username is scanned in steps of about 250 sites,
  two at a time; after every step the rows are pushed, the run's status line is updated
  with the percentage and counts, and the OUTPUT record carries `status: running` and
  `percentDone`. A run cut off by the time or spending limit keeps what was delivered.
- Charging moved from the platform's default dataset-item event to a custom `profile`
  event at the same $0.005, charged per step as rows land; the summary row is free.
  Requires the pricing record to name the `profile` event at deploy time.
- Every run is the deepest scan the scanner can do: all 999 sites by default (was 100),
  every output field it can emit (status, type, country, language, rank, metadata,
  extracted), 60 parallel workers through `src/scan.py` instead of the hardcoded 15
  (all sites in about 2 to 3 minutes instead of 7), and its permissive detection level.
  The scanner's `slow`/`special` modes have no code behind them and its "extreme" level
  is a stricter gate, so no depth setting is offered: there is nothing deeper to offer.
- The confidence filter now works. The scanner's own `--filter` silently did nothing
  unless `status` was among the requested output fields, so "confident only" returned
  50% matches too. Filtering is done here on the match rate: confident = 100%, possible
  = 50 to 99%, everything = all. Confidence tiers follow the same thresholds. The default
  is "everything": a bare username must miss nothing, and every row is labelled.
- A username with nothing else set gets every option at its maximum: all 999 sites,
  worldwide, every category, every match, metadata and pattern extraction on, a one-hour
  time limit. Every other field only narrows the scan.
- Summary counts sites with no profile (`sitesNotFound`) and sites that did not answer
  (`sitesFailed`), so "0 results" is explained by the numbers.
- Rows carry `language` and `metadata` (meta tags of confident profiles); the previous
  version requested only link, rate, title and text, so the "Add page metadata" option
  had no visible effect.
- `usernames` list field for bulk input (up to 25 per run), alongside `username`.
- The site list is built by the actor on every run (top N by popularity from the
  scanner's own data), so the number of sites checked is exact and reported. Adult and
  dating sites (48) are part of every run, as they always were; the `adultSite` column
  marks those rows and the "Adult and dating sites" category checks only them.
- Every profile row carries `platform`, `site`, `category`, `categoryDetail`, `country`,
  `adultSite`, `siteRank`, `matchRate` and `pageTitle`; the scanner itself only returns
  link, rate, title and text.
- One summary row per run with per-username results, the filters applied, notes about
  cleaned or skipped input, and the plain-language message also shown as the run status.
  The same object is saved as the `OUTPUT` record for API and agent users.
- Named-site input accepts any of the 999 sites by domain or partial name; unknown names
  are reported with close matches.
- Category dropdown built from the scanner's data, with site counts per category.
- Unit tests for input cleaning, site selection, enrichment and the status-message guard
  (`python3 test_unit.py`), plus a two-site real scan when the CLI is installed.

### Changed
- Input form regrouped into "Who to look up", "Where to look", "Results", "Limits"; every
  title and description rewritten in plain words with the technical detail second.
- Dataset views show platform, profile URL, confidence, match %, category, site country and
  adult flag; the always-empty status, language and rank columns are gone.
- `trim` is always on and no longer a field; `method` is no longer a field (found profiles
  are always what is returned).
- `siteType` values are now category slugs and `countries` values are country names, both
  chosen from dropdowns. A saved task or API call that still sends the old free-text values
  (`Dating`, `gb`) is rejected by the platform's input validation before the run starts:
  pick the category or country again.

## [1.0.1] - 2026-06-30

### Added
- Auto-clean the username input: a pasted `@handle`, a profile link (`twitter.com/elonmusk`), or a comma-separated list is now normalized to bare handle(s). Tested live: a raw `@elonmusk` used to return 21 confidently-broken URLs (`github.com/@elonmusk`) while still reporting success; it now resolves to the real profiles.
- Early input validation: clearly invalid input now fails fast with a plain reason instead of a silent empty or garbage run.
- Confidence ranking: every detected profile gets a `confidence` tier (high / medium / low) from its match rate, and results are sorted strongest-first so the real profiles surface above long-shot guesses.
- Clear end-of-run status message: every run reports what it found (with the high-confidence count), and a 0-result or all-weak-matches run says so explicitly instead of looking identical to a strong run.

### Changed
- Input form regrouped: the username box stands alone on top, with the rest folded into "Scan options" and "Advanced" sections. No options removed.
- Rewrote field descriptions and option labels from tool jargon into plain English.
- Repositioned the listing for non-developer users (recruiting, people search, fraud, due diligence) while keeping the developer / OSINT / Sherlock-alternative discoverability.
- Run summary now reports high / medium / low confidence counts.

### Fixed
- README input table and output example now match what the actor actually accepts and returns (was listing non-existent `usernames`, `categories`, `confidence_min` fields and a wrong output shape).
- Removed em-dashes from the input schema and source.
