# Social Analyzer — Find Profiles Across 900+ Sites

📦 **Open source · MIT:** [github.com/AnshumanAtrey/social-analyzer](https://github.com/AnshumanAtrey/social-analyzer)


Cloud-hosted [qeeqbox/social-analyzer](https://github.com/qeeqbox/social-analyzer) — find a username (a person, brand, or org) across **900+ social media and online platforms** in a single scan. Results stream as structured dataset records (one per detected profile) with confidence scoring, country, category, and extracted metadata.

## Quick start

```json
{
  "username": "elonmusk",
  "mode": "fast",
  "top": 100
}
```

Scans the top 100 most popular sites in ~30-60 seconds. Each detected profile becomes a dataset record.

## Multiple usernames in one run

```json
{
  "username": "elonmusk,jeffbezos,billgates",
  "mode": "fast",
  "top": 50
}
```

## Comprehensive scan (top 500 sites + metadata)

```json
{
  "username": "anshumanatrey",
  "mode": "slow",
  "top": 500,
  "metadata": true,
  "extract": true
}
```

Takes a few minutes, but extracts page metadata (Open Graph, Twitter Card) and any URLs / patterns embedded in detected profile pages.

## Output structure

Each scan produces multiple dataset records with a `recordType` discriminator:

| recordType | Fields | When |
|---|---|---|
| `profile` | `username`, `platform`, `link`, `status`, `rate`, `country`, `language`, `type`, `rank`, `extracted`, `metadata`, `text` | One per detected profile |
| `summary` | `username`, `mode`, `sitesChecked`, `profilesFound`, `duration`, `cmd` | Always last record |

Filter by `recordType=profile` in the Apify Console table view to see only detections.

## Modes

| Mode | What it does | When to use |
|---|---|---|
| `fast` | HTTP probing — check if URL responds with a profile page | 90% of cases |
| `slow` | Page analysis — parse responses for OSINT signal | Deeper investigation |
| `special` | Extra signal extraction (text/image/name analysis) | Most thorough |

## Confidence filter

- `good` (default) — only high-confidence matches (recommended for clean results)
- `good,maybe` — include uncertain matches
- `all` — every probe, including false positives

## Pricing

$0.005 per dataset record. A typical fast scan of top 100 sites returns 5-15 detected profiles = $0.025-$0.075 per scan.

## Use cases

- **Recruiters** — verify a candidate's online presence before an interview
- **Journalists** — investigate subjects for stories
- **Fraud teams** — confirm or deny suspect identities
- **HR background checks** — verify professional accounts
- **Dating safety** — confirm someone is who they say they are
- **OSINT investigators** — case work on individuals or brands

## Authorization

Only search usernames you're authorized to investigate. Some jurisdictions restrict OSINT collection on private individuals — consult local law (GDPR in EU, CCPA in California, etc.).

## FAQ

### How accurate are the results?
The `good` confidence filter (default) returns only high-signal matches — these are reliable (~95%+ true positives). The `good,maybe` filter trades precision for recall — useful when you want exhaustive coverage and don't mind manually checking some false positives.

### Why does a scan sometimes take 5+ minutes?
`slow` mode parses each detected page for OSINT signals (emails, links, metadata). `special` mode runs OCR on profile images. If you just need a list of platforms, stick with `fast` mode — usually under 60 seconds for top 100 sites.

### Can I scan a username only on platforms from certain countries?
Yes — pass a comma-separated list of two-letter country codes (`US,IN,JP`) and the actor will probe only sites registered to those countries. Useful for compliance investigations restricted to specific jurisdictions.

### Why does it sometimes miss LinkedIn?
LinkedIn aggressively rate-limits anonymous probes — confirmation of a profile often requires a logged-in scrape. For LinkedIn specifically, use a dedicated LinkedIn-scraper actor; social-analyzer's value is in the long tail of 900+ other platforms.

### How is this different from Sherlock / Maigret?
Sherlock checks ~400 sites with binary "exists / doesn't exist." Social Analyzer adds: 900+ sites, confidence scoring (so you can trust the results), metadata extraction, country/category filtering, and parallel HTTP probing for speed. Maigret is the closest competitor — Social Analyzer covers more sites but Maigret is faster for username-only queries.

## Pairs nicely with

Bundle for full identity-resolution workflows:

- **[Holehe Email OSINT](https://apify.com/anshumanatrey/holehe-email-osint)** — Start with an email, find which sites it's registered on, then probe those usernames with Social Analyzer
- **[theHarvester](https://apify.com/anshumanatrey/theharvester-osint)** — Discover emails/people for a domain, then OSINT the names with Social Analyzer
- **[NetIntel](https://apify.com/anshumanatrey/netintel)** — Add WHOIS / GeoIP context to domains found in discovered profiles
- **[Bug Bounty Finder](https://apify.com/anshumanatrey/bug-bounty-finder)** — Audit your own brand's presence + check for active disclosure programs
- **[nmap](https://apify.com/anshumanatrey/nmap-scanner)** — Network recon for the technical-investigation half of OSINT
- **[Zomato Restaurant Scraper](https://apify.com/anshumanatrey/zomato-restaurant-scraper)** — Restaurant lead lists (separate B2B use case)

## Credits

Built on [qeeqbox/social-analyzer](https://github.com/qeeqbox/social-analyzer) by Qeeqbox. AGPL-3.0 licensed per upstream.
