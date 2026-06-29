# Changelog

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
