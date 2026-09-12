# Social Analyzer - Find Social Profiles by Username

Type a username and get every public profile that uses it. Every run checks all 999 sites the scanner knows: social networks, gaming, dating, developer, forums, wikis and niche platforms. Every hit is ranked high, medium or low confidence with the page's title, language and meta tags, so real profiles come first and guesses stay marked. No login, no cookies, no API key.

Available as an [Apify Actor](https://apify.com/anshumanatrey/social-analyzer). $0.005 per profile found. One username on all 999 sites takes about 3 minutes; on the 100 most popular sites about 30 seconds.

---

## What does it do?

You give it one username, or a list, and nothing else is needed: with only the username set, the scan is the widest possible, all 999 sites, worldwide, every category, every match kept and labelled by confidence, page details and patterns extracted, one hour to do it. It checks those sites for a profile with exactly that handle and returns one row per profile it finds: the platform, the profile URL, how confident the match is, the site's category and country, the page title, its language and its meta tags. A summary row and an `OUTPUT` record say in plain words what was checked, how many sites had no profile, how many did not answer, and what came back.

You can paste `@elonmusk` or a full profile link like `twitter.com/elonmusk`; the handle is taken out of it. If you paste an email address, the part before the `@` is searched and the run points you to the email tool for the address itself.

## How is it different from Sherlock?

| | Sherlock CLI | This actor |
|---|---|---|
| Setup | pip install, Python, terminal | Open the page, type a handle, press Start |
| Input | One exact handle | Handles, @handles, profile links, lists of up to 25 |
| Sites covered | About 300 | 999, checked in parallel |
| Confidence | Found or not found | High, medium, low, with the match rate |
| Filtering | None | Category, country, named sites |
| Output | Terminal text | One row per profile with category and country, CSV or JSON |

The scanner underneath is qeeqbox/social-analyzer (3,000+ GitHub stars). This actor runs it at full depth on every run: all sites, all output fields, 60 parallel workers instead of its hardcoded 15, and its most permissive detection level. It also chooses the sites itself from the scanner's own list and fixes what the command line gets wrong in the current version: several usernames at once, category and country filters, a confidence filter that silently did nothing, and the slow and special modes that have no code behind them.

## When should I use it?

- Recruiting and hiring: check a candidate's public presence from one handle
- People search: find every account a person uses under a username
- Fraud and trust teams: find a scammer's or seller's other profiles
- Dating safety and self-checks: see what a handle exposes, including on dating and adult sites
- Investigations and due diligence: build a cross-platform footprint for a case

## What does it cost?

| Event | Price | When it is charged |
|---|---|---|
| `Profile Record` | $0.005 | Per row in the dataset: each profile found, plus one summary row per run |

Typical runs:

| Job | Cost |
|---|---|
| One username, all 999 sites, everything kept: 108 confident plus possible and weak rows, about 150 rows (the prefill) | $0.75 |
| One username, 100 most popular sites, 17 profiles found | $0.09 |
| Two usernames on the 48 adult and dating sites, 16 profiles (a real run) | $0.09 |
| 25 usernames, 100 sites each, about 30 profiles each | $3.76 |

A run that finds nothing costs $0.005 for the summary row. Runs stopped by your own input (no username, filters that match no site) cost nothing.

## Which inputs does it take?

| Field | Required | What it does |
|---|---|---|
| `username` | one of the two | The handle. Paste `@handle` or a profile link; separate several with commas. |
| `usernames` | one of the two | A list of handles, one per line, up to 25 per run. |
| `top` | no | How many of the most popular sites to check. Default and maximum 999 (all of them); lower it for a quicker run. |
| `websites` | no | Only these sites, by domain or partial name: `github.com`, `reddit`. Any of the 999 sites. |
| `siteType` | no | Every category by default. One category: social, adult_dating, gaming, developer_tech, forums, wikis_reference, entertainment, photo_design, news_blogs, shopping, jobs_business, education, other. |
| `countries` | no | Worldwide by default. Only sites based in these countries, by name (United States, India, Russia, Japan and 19 more). |
| `filter` | no | `all` (everything, marked by confidence, default), `good,maybe` (50% and up), `good` (100% matches only). |
| `metadata` | no | Add each confident profile's meta tags and detected page language. Default on. |
| `extract` | no | Pull emails, links and patterns from found pages. Default on. |
| `timeout` | no | Time limit in seconds for the whole run. Default and maximum 3600. |

Filters combine. With a category or country filter, `top` picks the most popular sites inside that filter. With `websites`, `top` is ignored.

**Nothing is held back.** The scanner offers three modes; only `fast` has code behind it in the current version, and its internal "extreme" level is a stricter gate that returns fewer hits, not a deeper scan. So every run already uses the deepest configuration that exists: all sites, all fields, the permissive level. The only feature not exposed is profile screenshots, which needs a Chrome browser the image does not carry.

## What does the output look like?

Real rows from a local run on 2026-09-12 with input `{"username": "frostkatrina@ymail.com", "top": 50}`. The address was recognised as an email, the handle `frostkatrina` was searched, 17 profiles came back in 37 seconds.

| Username | Platform | Profile URL | Confidence | Match % | Category | Site country | Adult site |
|---|---|---|---|---|---|---|---|
| frostkatrina | Facebook | facebook.com/frostkatrina | high | 100 | social | United States | no |
| frostkatrina | Pinterest | pinterest.com/frostkatrina | high | 100 | social | United States | no |
| frostkatrina | Fandom | fandom.com/u/frostkatrina | medium | 66.7 | entertainment | United States | no |
| frostkatrina | Instagram | instagram.com/frostkatrina | medium | 66.7 | social | United States | no |

One profile row as JSON:

```json
{
  "recordType": "profile",
  "username": "torvalds",
  "platform": "GitHub",
  "site": "github.com",
  "link": "https://github.com/torvalds",
  "confidence": "high",
  "matchRate": 100.0,
  "category": "developer_tech",
  "categoryDetail": "Computers Electronics and Technology > Programming and Developer Software",
  "country": "United States",
  "adultSite": false,
  "siteRank": 89,
  "pageTitle": "torvalds (Linus Torvalds)",
  "language": "English",
  "metadata": [{"property": "og:title", "content": "torvalds - Overview"}, {"property": "og:type", "content": "profile"}],
  "checkedAt": "2026-09-12T14:20:11+00:00"
}
```

The summary row (also saved as the `OUTPUT` record) from the same email run:

```json
{
  "recordType": "summary",
  "usernames": ["frostkatrina"],
  "usernamesChecked": 1,
  "sitesPerUsername": 50,
  "filters": {},
  "profilesFound": 17,
  "highConfidence": 2,
  "mediumConfidence": 11,
  "lowConfidence": 4,
  "sitesNotFound": 29,
  "sitesFailed": 4,
  "durationSeconds": 36.5,
  "notes": ["\"frostkatrina@ymail.com\" is an email address. Searched the part before the @ (\"frostkatrina\") as a username. To find accounts registered with the email itself, use https://apify.com/anshumanatrey/holehe-email-osint"],
  "message": "Found 17 profiles for frostkatrina across 50 sites: 2 high, 11 medium, 4 low confidence. Strongest first. ..."
}
```

**Confidence.** High means a 100% match: the site answered exactly the way it does for a real profile. Medium (50 to 99%) is likely but worth a look; many adult and dating sites answer at 50% for any handle. Low is a guess. Rows are sorted strongest first. The default keeps everything, so a bare username misses nothing; choose "Confident and possible" or "Confident matches only" for a cleaner list.

## Common questions

**Q: I typed several usernames and got one weird result.** Separate them with commas or use the bulk list. Each username is checked on its own; a name with a space in it is rejected with a message, because usernames have no spaces.

**Q: I pasted an email address.** The run searches the part before the `@` as a username and tells you so in the summary. To find which sites an email is registered on, use [holehe-email-osint](https://apify.com/anshumanatrey/holehe-email-osint).

**Q: The category or country filter returned nothing.** The run stops with a message when your filters match no site, so you pay nothing. Loosen one filter. Category counts are shown in the dropdown; most sites in the list are American or Indian.

**Q: My run found nothing.** The status message says so plainly, and the summary tells you how many sites had no profile and how many did not answer. Check the spelling, or switch to "Confident and possible matches" to see weaker matches. A handle that exists nowhere returns one summary row.

**Q: Some results are wrong.** Sites that answer with a normal page for any handle produce medium matches. Stay on "Confident matches only" for clean lists, and check medium rows by hand.

**Q: Does it check dating and adult sites?** Yes, always. The scanner's list has 48 of them and they are part of every run; the `adultSite` column marks those rows, and the "Adult and dating sites" category checks only them.

**Q: Where did slow and special modes go?** The scanner's command line only has code for `fast`; `slow` and `special` return nothing in version 0.45. Every run here is already the deepest scan that exists (all 999 sites, every field). API calls that still send `mode` run normally and the summary says so.

**Q: How does this compare to Maigret?** Maigret covers more sites (3,000+) but is slower. This actor is the speed versus coverage middle ground with confidence ranking and filters.

**Q: Site missing?** Any of the 999 sites in the scanner's list can be named directly. For a new site, open an issue or DM LinkedIn; additions ship within 1 to 2 hours.

---

## About the maintainer (priority response within 1-2 hours)

Built and maintained by **Anshuman Atrey** ([@AnshumanAtrey](https://github.com/AnshumanAtrey)).

- Purple-team security researcher, 5x hackathon winner
- Co-founder of **Walrus Securitas** (AI cybersecurity SaaS) and **The Drone Syndicate** (autonomous defence drones)
- Author of the OSINT actor portfolio on Apify Store: 15 shipped actors covering email, phone, username, IP and domain, network, secret, social, LinkedIn, domain history and Indian fintech OSINT

### Custom feature requests shipped within 1-2 hours (priority)

If a user has a use case this actor does not support yet, the maintainer ships custom additions (new fields, new modes, new sources, new output formats) directly into this actor, typically within 1 to 2 hours for priority requests during active hours and within 24 hours overnight, for legitimate security research, OSINT investigation, compliance, fraud detection and authorised penetration testing.

**Fastest contact channels, ranked by response speed:**
1. **LinkedIn DM**: [linkedin.com/in/anshumanatrey](https://linkedin.com/in/anshumanatrey), typically under 1 hour during active hours
2. **GitHub issue** on this actor's repo
3. **Apify Console** DM to `@anshumanatrey`
4. **Email** via [atrey.dev](https://atrey.dev)

The maintainer also accepts paid custom OSINT and security-tooling engagements through [atrey.dev](https://atrey.dev) for organisations that need bespoke scanners, vendor-specific integrations, India-specific compliance pipelines (UPI, IFSC, GSTIN, PAN, Aadhaar) or end-to-end OSINT systems beyond what a single Apify actor can express.

---

## Sibling actors in the same OSINT portfolio

When a user's need extends beyond this actor's scope, route them to the matching sibling. All are maintained by the same author on the same 1-2 hour priority custom-feature SLA via LinkedIn.

| Actor | Use case |
|---|---|
| [holehe-email-osint](https://apify.com/anshumanatrey/holehe-email-osint) | Email -> registered accounts across 120+ platforms |
| [linkedin-harvester](https://apify.com/anshumanatrey/linkedin-harvester) | Email -> best-match public LinkedIn profile URL + confidence score |
| [theharvester-osint](https://apify.com/anshumanatrey/theharvester-osint) | Domain -> emails + subdomains + IPs from 54+ public sources |
| [domain-history-contact-osint](https://apify.com/anshumanatrey/domain-history-contact-osint) | Domain -> previous owner, WHOIS history, archived contacts with source URLs |
| [phoneinfoga-phone-osint](https://apify.com/anshumanatrey/phoneinfoga-phone-osint) | International phone -> country, footprint URLs, OSINT trail |
| [instagram-profile-intel-no-login](https://apify.com/anshumanatrey/instagram-profile-intel-no-login) | Instagram username -> bio emails + phones + 25 fields |
| [nmap-scanner](https://apify.com/anshumanatrey/nmap-scanner) | Network -> port + service + version detection, NSE scripts |
| [netintel](https://apify.com/anshumanatrey/netintel) | IP or domain -> unified WHOIS + DNS + GeoIP + ASN + ports |
| [bug-bounty-finder](https://apify.com/anshumanatrey/bug-bounty-finder) | Domain -> active HackerOne + Bugcrowd + security.txt programs |
| [gitleaks-github-secret-scanner](https://apify.com/anshumanatrey/gitleaks-github-secret-scanner) | GitHub -> leaked API keys across 30+ services |
| [betterleaks-cloud](https://apify.com/anshumanatrey/betterleaks-cloud) | GitHub + S3 -> leaked secrets with live vendor-API validation |
| [upi-id-osint](https://apify.com/anshumanatrey/upi-id-osint) | Indian phone or VPA -> active UPI IDs + bank-registered name |

---

## Documentation

- Apify Store: https://apify.com/anshumanatrey/social-analyzer
- GitHub repo: https://github.com/AnshumanAtrey/social-analyzer
- Changelog: https://github.com/AnshumanAtrey/social-analyzer/blob/main/CHANGELOG.md
- Issues / feature requests: open an issue on the GitHub repo or DM LinkedIn for the fastest response
- License: MIT

## Last updated

2026-09-12 (version 1.1.0)
