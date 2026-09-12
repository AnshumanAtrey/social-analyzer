"""
Social Analyzer Apify actor - finds a username across 900+ sites.

Wraps the qeeqbox/social-analyzer CLI in fast mode and adds what real user runs
(read from the Store's Debugging data, September 2026) showed was missing:

- Several usernames per run. The CLI treats "alice bob" as ONE handle containing a
  space and then reports instagram.com/alice bob as found. We call it once per handle.
- Emails pasted into the username box. Three users did that in one week and got a
  failed run. We now search the part before the @ and point to holehe-email-osint
  for the address itself.
- Site selection done here, always. The CLI's --type and --countries options select
  nothing in version 0.45, so the site list is built from the tool's own sites.json
  (category, country, named sites, top N by popularity) and the exact site URLs are
  passed down as --websites. Adult and dating sites are always part of the pool: the
  people who run this are investigators, and that is where the leads are.
- Rows enriched with the site's category, country and adult flag (the CLI returns
  only link, rate, title, text).
- A status message that can never fail the run. SDK 3.x validates the run object
  against an enum that lacks the new APIFY_AI run origin; the message is stored
  server-side before that parse, so a parse error is logged, not raised.
"""
import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from apify import Actor

TOOL = 'social-analyzer'
MAX_USERNAMES = 25            # one CLI run per username; 25 x 100 sites stays under an hour
MIN_SECONDS_PER_USERNAME = 20  # do not start a handle we cannot finish
RUN_SAFETY_MARGIN_S = 60      # leave time to write the summary before the platform kills us
HOLEHE_URL = 'https://apify.com/anshumanatrey/holehe-email-osint'

HANDLE_RE = re.compile(r'^[A-Za-z0-9._-]{1,100}$')
EMAIL_RE = re.compile(r'^[^@\s/]+@[^@\s/]+\.[^@\s/]+$')

# Friendly categories over the tool's 90 raw "type" strings. Order matters: the
# first matching rule wins, so "Social Networks and Online Communities" is social,
# not developer, even though it sits under "Computers Electronics and Technology".
CATEGORY_RULES = [
    ('adult_dating', ('adult', 'romance', 'dating')),
    ('social', ('social network', 'online communit', 'community and society')),
    ('gaming', ('game',)),
    ('forums', ('forum',)),
    ('wikis_reference', ('wiki', 'dictionar', 'encyclop', 'reference')),
    ('photo_design', ('photograph', 'visual arts', 'design', 'graphics')),
    ('entertainment', ('music', 'entertainment', 'tv', 'streaming', 'video', 'anim', 'comic', 'humor', 'book', 'literature')),
    ('developer_tech', ('programming', 'software', 'computer', 'technology', 'hosting', 'file sharing', 'search engine', 'hardware', 'electronics')),
    ('news_blogs', ('news', 'blog', 'media')),
    ('shopping', ('commerce', 'shopping', 'marketplace', 'auction')),
    ('jobs_business', ('job', 'career', 'business', 'finance', 'marketing', 'investing', 'banking')),
    ('education', ('education', 'science', 'universit', 'librar', 'biology', 'physics', 'philosophy')),
]
CATEGORY_TITLES = {
    '': 'Any category',
    'social': 'Social networks and communities',
    'adult_dating': 'Adult and dating sites',
    'gaming': 'Gaming',
    'developer_tech': 'Developer and tech',
    'forums': 'Forums',
    'wikis_reference': 'Wikis and reference',
    'entertainment': 'Music, video and entertainment',
    'photo_design': 'Photography and design',
    'news_blogs': 'News and blogs',
    'shopping': 'Shopping and marketplaces',
    'jobs_business': 'Jobs, business and finance',
    'education': 'Education and science',
    'other': 'Everything else',
}
# Values users typed into the old free-text "siteType" field, kept working.
CATEGORY_ALIASES = {
    'dating': 'adult_dating', 'adult': 'adult_dating', 'adult content': 'adult_dating',
    'social networks': 'social', 'social': 'social', 'gaming': 'gaming', 'games': 'gaming',
    'music': 'entertainment', 'video': 'entertainment', 'video sharing': 'entertainment',
    'photo': 'photo_design', 'photo sharing': 'photo_design', 'blog': 'news_blogs',
    'blog platforms': 'news_blogs', 'news': 'news_blogs', 'forum': 'forums',
    'shopping': 'shopping', 'shopping / marketplaces': 'shopping',
    'professional': 'jobs_business', 'professional / linkedin-style': 'jobs_business',
}
# Well-known platforms whose raw type is a generic "Internet" or missing.
CATEGORY_BY_HOST = {
    'vk.com': 'social', 'twitter.com': 'social', 'mobile.twitter.com': 'social', 'x.com': 'social',
    't.me': 'social', 'telegram.org': 'social', 'snapchat.com': 'social', 'discord.com': 'social',
    'threads.net': 'social', 'quora.com': 'social', 'social.msdn.microsoft.com': 'forums',
    'youtube.com': 'entertainment', 'play.google.com': 'shopping', 'google.com': 'other',
}
COUNTRY_ALIASES = {
    'us': 'United States', 'usa': 'United States', 'in': 'India', 'ru': 'Russia', 'jp': 'Japan',
    'ir': 'Iran', 'id': 'Indonesia', 'pl': 'Poland', 'ca': 'Canada', 'cn': 'China', 'eg': 'Egypt',
    'cz': 'Czech Republic', 'br': 'Brazil', 'au': 'Australia', 'pk': 'Pakistan', 'tw': 'Taiwan',
    'de': 'Germany', 'kr': 'South Korea', 'tr': 'Turkey', 'ch': 'Switzerland', 'gb': 'United Kingdom',
    'uk': 'United Kingdom', 'fr': 'France', 'es': 'Spain', 'vn': 'Vietnam', 'sa': 'Saudi Arabia',
    'sg': 'Singapore', 'mx': 'Mexico', 'ng': 'Nigeria', 'ma': 'Morocco', 'ao': 'Angola',
}


# --------------------------------------------------------------------------- sites --
def find_sites_json() -> Path | None:
    """sites.json ships inside the pip package as social-analyzer/data/sites.json."""
    candidates = [Path(sysconfig.get_paths()['purelib']), Path(sysconfig.get_paths()['platlib'])]
    candidates += [Path(p) for p in sys.path if p]
    tool = shutil.which(TOOL)
    if tool:
        # <venv>/bin/social-analyzer -> <venv>/lib/pythonX.Y/site-packages
        candidates += list(Path(tool).resolve().parent.parent.glob('lib/python*/site-packages'))
    for base in candidates:
        p = base / 'social-analyzer' / 'data' / 'sites.json'
        if p.is_file():
            return p
    return None


def host_of(url: str) -> str:
    """'https://blog.naver.com/{username}' -> 'blog.naver.com'."""
    netloc = urlparse(url.replace('{username}', 'x')).netloc.lower()
    return netloc.split('@')[-1].split(':')[0].removeprefix('www.')


def category_of(raw_type: str, nsfw: bool) -> str:
    t = (raw_type or '').lower()
    if nsfw:
        return 'adult_dating'
    for slug, needles in CATEGORY_RULES:
        if any(n in t for n in needles):
            return slug
    return 'other'


def platform_name(host: str) -> str:
    """'blog.naver.com' -> 'Naver', 'about.me' -> 'About.me', 'github.com' -> 'GitHub'."""
    known = {'github.com': 'GitHub', 'gitlab.com': 'GitLab', 'youtube.com': 'YouTube',
             'tiktok.com': 'TikTok', 'linkedin.com': 'LinkedIn', 'soundcloud.com': 'SoundCloud',
             'deviantart.com': 'DeviantArt', 'x.com': 'X (Twitter)', 'twitter.com': 'X (Twitter)',
             'vk.com': 'VK', 'ok.ru': 'OK.ru', 'about.me': 'About.me', 'last.fm': 'Last.fm',
             't.me': 'Telegram', 'telegram.org': 'Telegram', 'chess.com': 'Chess.com', '9gag.com': '9GAG'}
    if host in known:
        return known[host]
    parts = host.split('.')
    if len(parts) >= 3 and parts[-2] in ('co', 'com', 'org', 'net'):
        return parts[-3].capitalize()
    return parts[-2].capitalize() if len(parts) >= 2 else host


def load_sites() -> list[dict]:
    """The tool's site list, normalised: host, category, country, adult flag, rank."""
    path = find_sites_json()
    if not path:
        Actor.log.warning('sites.json not found; category, country and adult filters are unavailable this run')
        return []
    raw = json.loads(path.read_text(encoding='utf-8'))
    entries = raw.get('websites_entries', raw if isinstance(raw, list) else [])
    sites = []
    for s in entries:
        url = s.get('url') or ''
        if '{username}' not in url:
            continue
        nsfw = str(s.get('nsfw', 'false')).lower() == 'true' or 'adult' in (s.get('type') or '').lower()
        rank = s.get('global_rank')
        # "https://{username}.tumblr.com" -> host "tumblr.com"; a found link like
        # kaytats.tumblr.com is matched back to it by stripping leading labels.
        host = host_of(url).removeprefix('x.') if '{username}.' in url else host_of(url)
        sites.append({
            'host': host,
            'url': url,
            'category': CATEGORY_BY_HOST.get(host) or category_of(s.get('type'), nsfw),
            'categoryDetail': s.get('type') or None,
            'country': s.get('country') or None,
            'adult': nsfw,
            'rank': int(rank) if isinstance(rank, (int, float)) and rank else None,
        })
    return sites


# ------------------------------------------------------------------------ input --
URL_RE = re.compile(r'^(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?::\d+)?/', re.I)


def clean_handle(token: str) -> str:
    """Bare handle out of '@name', 'https://x.com/name/', 'instagram.com/name?hl=en'.

    Only something that looks like a real URL (a domain followed by a path) is
    treated as a link; 'foo/bar' is junk, not the handle 'bar'.
    """
    s = token.strip().strip('"\'<>')
    if URL_RE.match(s):
        s = re.sub(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', '', s)
        s = s.split('?', 1)[0].split('#', 1)[0]
        segs = [p for p in s.split('/') if p]
        s = segs[-1] if len(segs) > 1 else ''
    return s.lstrip('@').strip().rstrip('.,;:')


def parse_usernames(inp: dict) -> tuple[list[dict], list[str]]:
    """Collect targets from `username` (text, may hold a list) and `usernames` (list).

    Returns (targets, problems). Each target: {handle, original, fromEmail}.
    An email address becomes a search for its local part plus a pointer to holehe.
    """
    # Commas, semicolons and newlines separate usernames. A space does not: "john doe"
    # is one wrong entry (a name, not a handle), not two searches for "john" and "doe".
    raw: list[str] = []
    single = inp.get('username')
    if isinstance(single, str) and single.strip():
        raw += re.split(r'[,;\n\r\t]+', single.strip())
    many = inp.get('usernames')
    if isinstance(many, str):
        raw += re.split(r'[,;\n\r\t]+', many.strip())
    elif isinstance(many, list):
        for item in many:
            if isinstance(item, str) and item.strip():
                raw += re.split(r'[,;\n\r\t]+', item.strip())

    targets, problems, seen = [], [], set()
    for tok in raw:
        tok = tok.strip()
        if not tok:
            continue
        if ' ' in tok:
            problems.append(f'"{tok}" contains a space. Usernames have no spaces; separate several usernames with commas.')
            continue
        from_email = bool(EMAIL_RE.match(tok))
        handle = clean_handle(tok.split('@', 1)[0]) if from_email else clean_handle(tok)
        if not handle:
            problems.append(f'"{tok}" has no usable handle')
            continue
        if not HANDLE_RE.match(handle):
            problems.append(f'"{tok}" is not a username (letters, digits, dot, underscore and dash only)')
            continue
        key = handle.lower()
        if key in seen:
            continue
        seen.add(key)
        targets.append({'handle': handle, 'original': tok, 'fromEmail': from_email})
    if len(targets) > MAX_USERNAMES:
        problems.append(f'{len(targets)} usernames given; this run checks the first {MAX_USERNAMES}')
        targets = targets[:MAX_USERNAMES]
    return targets, problems


def as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r'[,;\n ]+', value) if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def select_sites(inp: dict, sites: list[dict], top: int) -> tuple[list[dict] | None, dict]:
    """Choose the sites to probe for every username.

    The user's filters apply first (named sites, one category, countries), then the
    most popular `top` sites by global rank. Every site in the scanner's list is
    eligible, adult and dating sites included. Returns (selected sites, info). None
    means the site list was not available and the scanner's own top N has to be used.
    """
    wanted_sites = [w.lower().removeprefix('https://').removeprefix('http://').removeprefix('www.').strip('/')
                    for w in as_list(inp.get('websites'))]
    category = (inp.get('siteType') or inp.get('siteCategory') or '').strip()
    category = CATEGORY_ALIASES.get(category.lower(), category)
    countries = [COUNTRY_ALIASES.get(c.lower(), c) for c in as_list(inp.get('countries'))]

    info = {'filters': {}, 'unknownSites': [], 'suggestions': {}, 'sitesMatched': None}
    if wanted_sites:
        info['filters']['websites'] = wanted_sites
    if category:
        info['filters']['category'] = category
    if countries:
        info['filters']['countries'] = countries
    if not sites:
        info['error'] = 'site list unavailable; the scanner picked its own top sites'
        return None, info

    chosen = sites
    if wanted_sites:
        chosen, hosts = [], {s['host'] for s in sites}
        for w in wanted_sites:
            hits = [s for s in sites if w == s['host'] or w in s['host'] or s['host'] in w]
            if not hits:
                info['unknownSites'].append(w)
                close = difflib.get_close_matches(w, hosts, n=3, cutoff=0.6)
                if close:
                    info['suggestions'][w] = close
            chosen += hits
    if category and category in CATEGORY_TITLES:
        chosen = [s for s in chosen if s['category'] == category]
    elif category:
        info['unknownCategory'] = category
    if countries:
        chosen = [s for s in chosen if s['country'] in countries]

    chosen = sorted({s['url']: s for s in chosen}.values(), key=lambda s: (s['rank'] is None, s['rank'] or 0))
    if not wanted_sites:
        chosen = chosen[:top]
    info['sitesMatched'] = len(chosen)
    return chosen, info


# ------------------------------------------------------------------------- tool --
def build_command(username: str, *, top: int, site_urls: list[str] | None, confidence_filter: str,
                  extract: bool, metadata: bool) -> list[str]:
    """--websites gets the exact URL patterns from sites.json. The scanner matches
    tokens as substrings of a site's URL, so a bare host like t.me would also select
    about.me; the full pattern selects exactly one entry."""
    cmd = [TOOL, '--username', username, '--output', 'json', '--mode', 'fast',
           '--method', 'find', '--filter', confidence_filter, '--options', 'link,rate,title,text', '--trim']
    if site_urls:
        cmd += ['--websites', ' '.join(site_urls)]
    else:
        cmd += ['--top', str(top)]
    if extract:
        cmd.append('--extract')
    if metadata:
        cmd.append('--metadata')
    return cmd


def parse_output(stdout: str) -> dict:
    """The CLI prints one JSON object; on some paths it prints nothing at all."""
    if not stdout or not stdout.strip():
        return {'detected': [], 'parse_error': 'empty stdout'}
    try:
        return json.loads(stdout.strip())
    except json.JSONDecodeError:
        pass
    start, end = stdout.find('{'), stdout.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(stdout[start:end + 1])
        except json.JSONDecodeError as e:
            return {'detected': [], 'parse_error': f'JSON decode failed: {e}'}
    return {'detected': [], 'parse_error': 'no JSON object found in stdout'}


def parse_rate(rate) -> float | None:
    """'%100.0' or '66.6%' -> 100.0 / 66.6."""
    if rate is None:
        return None
    m = re.search(r'[\d.]+', str(rate))
    return float(m.group()) if m else None


def confidence_tier(rate) -> str:
    v = parse_rate(rate)
    if v is None:
        return 'unknown'
    return 'high' if v >= 75 else 'medium' if v >= 50 else 'low'


def enrich(profile: dict, username: str, site_index: dict[str, dict], checked_at: str) -> dict:
    link = profile.get('link') or ''
    host = host_of(link) if link else ''
    site = site_index.get(host) or {}
    if not site and host:
        # subdomain-pattern sites: kaytats.tumblr.com -> tumblr.com
        labels = host.split('.')
        for i in range(1, len(labels) - 1):
            site = site_index.get('.'.join(labels[i:])) or {}
            if site:
                break
    return {
        'recordType': 'profile',
        'username': username,
        'platform': platform_name(host) if host else (profile.get('title') or 'unknown'),
        'site': host or None,
        'link': link or None,
        'confidence': confidence_tier(profile.get('rate')),
        'matchRate': parse_rate(profile.get('rate')),
        'category': site.get('category') or 'other',
        'categoryDetail': site.get('categoryDetail'),
        'country': site.get('country'),
        'adultSite': bool(site.get('adult', False)),
        'siteRank': site.get('rank'),
        'pageTitle': profile.get('title'),
        'pageText': profile.get('text'),
        'extracted': profile.get('extracted'),
        'metadata': profile.get('metadata'),
        'checkedAt': checked_at,
    }


async def safe_status(message: str) -> None:
    """Set the run's status message without ever failing the run.

    The message is stored by the API before the SDK parses the response; SDK 3.x
    then validates the run object against an enum missing the APIFY_AI origin and
    raises. That raise used to turn 8 finished runs a week into FAILED.
    """
    try:
        await Actor.set_status_message(message)
    except Exception as exc:  # noqa: BLE001
        Actor.log.debug(f'status message stored but not confirmed by the SDK: {exc}')


def seconds_left_in_run() -> float | None:
    """Seconds until the platform kills this run, or None when not on the platform."""
    config = getattr(Actor, 'configuration', None) or getattr(Actor, 'config', None)
    timeout_at = getattr(config, 'timeout_at', None) if config else None
    if not timeout_at:
        return None
    now = datetime.now(timezone.utc)
    return (timeout_at - now).total_seconds()


# -------------------------------------------------------------------------- main --
async def main() -> None:
    async with Actor:
        Actor.log.info('Social Analyzer starting')
        inp = await Actor.get_input() or {}

        targets, problems = parse_usernames(inp)
        for p in problems:
            Actor.log.warning(p)
        if not targets:
            hint = problems[0] if problems else 'No username given.'
            await Actor.fail(status_message=(
                f'{hint} Enter the username to look up, for example elonmusk. You can paste an '
                f'@handle or a profile link, and list several separated by commas.'))
            return

        if not shutil.which(TOOL):
            await Actor.fail(status_message=(
                'The scanner is missing from this build. This is on us, not you: please report it '
                'and we will ship a fixed build within hours.'))
            return

        top = max(10, min(int(inp.get('top') or 100), 999))
        confidence_filter = inp.get('filter') or 'good'
        if confidence_filter not in ('good', 'good,maybe', 'all'):
            confidence_filter = 'good'
        extract = bool(inp.get('extract', False))
        metadata = bool(inp.get('metadata', True))

        notes: list[str] = []
        legacy_mode = inp.get('mode')
        if legacy_mode and legacy_mode != 'fast':
            notes.append(f'Scan depth "{legacy_mode}" is no longer offered (it returned no data in the '
                         f'current scanner); ran the fast scan instead.')
        for t in targets:
            if t['fromEmail']:
                notes.append(f'"{t["original"]}" is an email address. Searched the part before the @ '
                             f'("{t["handle"]}") as a username. To find accounts registered with the '
                             f'email itself, use {HOLEHE_URL}')
        notes += problems

        sites = load_sites()
        site_index = {s['host']: s for s in sites}
        selected, selection = select_sites(inp, sites, top)
        site_urls = [s['url'] for s in selected] if selected is not None else None
        if selection.get('unknownSites'):
            for w in selection['unknownSites']:
                sugg = selection['suggestions'].get(w)
                notes.append(f'Site "{w}" is not in the list' + (f'; did you mean {", ".join(sugg)}?' if sugg else '.'))
        if selection.get('unknownCategory'):
            notes.append(f'Category "{selection["unknownCategory"]}" is not known; '
                         f'valid values: {", ".join(k for k in CATEGORY_TITLES if k)}. No category filter applied.')
        if selection.get('error'):
            notes.append(selection['error'])
        sites_per_username = selection['sitesMatched'] if selected is not None else top
        if selected is not None and not selected:
            await Actor.fail(status_message=(
                'Your filters match no sites. ' + ' '.join(notes) if notes else
                'Your site, category or country filters match no sites in the list. Loosen a filter and run again.'))
            return
        Actor.log.info(f'{len(targets)} username(s), {sites_per_username} site(s) each, filters={selection["filters"] or "none"}')

        # Time budget: the user's limit, capped so the summary is written before the
        # platform kills the run. The CLI has no partial output, so an unfinished
        # username yields nothing, which is why we refuse to start one we cannot finish.
        user_limit = int(inp.get('timeout') or 1800)
        platform_left = seconds_left_in_run()
        budget = user_limit if platform_left is None else min(user_limit, max(30, platform_left - RUN_SAFETY_MARGIN_S))
        deadline = time.monotonic() + budget

        checked_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        per_username: list[dict] = []
        totals = {'profiles': 0, 'high': 0, 'medium': 0, 'low': 0, 'failed': 0, 'unknown': 0}
        skipped_for_time: list[str] = []
        tool_errors = 0

        for i, t in enumerate(targets, 1):
            username = t['handle']
            remaining = deadline - time.monotonic()
            if remaining < MIN_SECONDS_PER_USERNAME:
                skipped_for_time.append(username)
                continue
            cmd = build_command(username, top=top, site_urls=site_urls, confidence_filter=confidence_filter,
                                extract=extract, metadata=metadata)
            Actor.log.info(f'[{i}/{len(targets)}] {username}: checking {sites_per_username} sites')
            t0 = time.monotonic()
            entry = {'username': username, 'original': t['original'], 'fromEmail': t['fromEmail'],
                     'sitesChecked': sites_per_username, 'profilesFound': 0, 'high': 0, 'medium': 0,
                     'low': 0, 'sitesFailed': 0, 'sitesUnclear': 0, 'seconds': 0.0, 'note': None}
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(10, remaining))
            except subprocess.TimeoutExpired:
                entry['note'] = f'did not finish within the time limit ({int(remaining)} s left); no results for this username'
                entry['seconds'] = round(time.monotonic() - t0, 1)
                per_username.append(entry)
                Actor.log.warning(f'{username}: {entry["note"]}')
                continue
            entry['seconds'] = round(time.monotonic() - t0, 1)
            noise = ('REPLACEMENT CHARACTER', 'XMLParsedAsHTMLWarning', 'warnings.filterwarnings', 'import warnings',
                     'BeautifulSoup(', 'from bs4 import')
            for line in [l for l in result.stderr.splitlines() if l.strip() and not any(n in l for n in noise)][-5:]:
                Actor.log.warning(f'{username}: {line[:300]}')

            parsed = parse_output(result.stdout)
            if 'parse_error' in parsed:
                tool_errors += 1
                entry['note'] = (f'the scanner returned no data (exit code {result.returncode}, {parsed["parse_error"]}). '
                                 f'Usually a temporary problem; run this username again.')
                Actor.log.error(f'{username}: {entry["note"]}')
                per_username.append(entry)
                continue

            detected = parsed.get('detected') or []
            entry['sitesFailed'] = len(parsed.get('failed') or [])
            entry['sitesUnclear'] = len(parsed.get('unknown') or [])
            detected.sort(key=lambda p: parse_rate(p.get('rate')) or 0, reverse=True)
            rows = [enrich(p, username, site_index, checked_at) for p in detected]
            if rows:
                await Actor.push_data(rows)
            for r in rows:
                entry[r['confidence'] if r['confidence'] in ('high', 'medium', 'low') else 'low'] += 1
            entry['profilesFound'] = len(rows)
            totals['profiles'] += len(rows)
            for k in ('high', 'medium', 'low'):
                totals[k] += entry[k]
            totals['failed'] += entry['sitesFailed']
            totals['unknown'] += entry['sitesUnclear']
            if not rows:
                entry['note'] = 'no profile with this exact handle on the sites checked'
            per_username.append(entry)
            Actor.log.info(f'{username}: {len(rows)} profiles ({entry["high"]} high confidence) in {entry["seconds"]}s')

        # ---- summary: one row (charged like a profile row) and one free OUTPUT record
        duration = round(time.monotonic() - started, 1)
        checked = [e for e in per_username if e['note'] is None or e['note'].startswith('no profile')]
        if skipped_for_time:
            notes.append(f'Time limit reached before {len(skipped_for_time)} username(s) could start: '
                         f'{", ".join(skipped_for_time)}. Raise the time limit or check fewer sites.')

        if totals['profiles'] == 0 and len(checked) == len(targets):
            headline = (f'No profiles found for {", ".join(t["handle"] for t in targets)} on {sites_per_username} sites. '
                        f'The handle may be spelled differently there, or the person does not use it publicly. '
                        f'Try more sites or the "confident and possible" setting.')
        elif totals['profiles'] == 0:
            headline = (f'No results. {tool_errors} of {len(targets)} username(s) could not be scanned; '
                        f'see the notes and run again.')
        else:
            who = checked[0]['username'] if len(checked) == 1 else f'{len(checked)} usernames'
            headline = (f'Found {totals["profiles"]} profiles for {who} across {sites_per_username} sites: '
                        f'{totals["high"]} high, {totals["medium"]} medium, {totals["low"]} low confidence. Strongest first.')
        if notes:
            headline += ' ' + ' '.join(notes)

        summary = {
            'recordType': 'summary',
            'usernames': [t['handle'] for t in targets],
            'usernamesRequested': len(targets),
            'usernamesChecked': len(checked),
            'sitesPerUsername': sites_per_username,
            'filters': selection['filters'],
            'confidenceFilter': confidence_filter,
            'profilesFound': totals['profiles'],
            'highConfidence': totals['high'],
            'mediumConfidence': totals['medium'],
            'lowConfidence': totals['low'],
            'sitesFailed': totals['failed'],
            'sitesUnclear': totals['unknown'],
            'durationSeconds': duration,
            'perUsername': per_username,
            'notes': notes,
            'message': headline,
            'success': tool_errors < len(targets),
            'checkedAt': checked_at,
        }
        await Actor.push_data(summary)
        await Actor.set_value('OUTPUT', summary)
        await safe_status(headline[:500])
        Actor.log.info(headline)

        if tool_errors and tool_errors == len(targets):
            await Actor.fail(status_message=headline[:500])


if __name__ == '__main__':
    asyncio.run(main())
