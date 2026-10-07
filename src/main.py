"""
Social Analyzer Apify actor - finds a username across 900+ sites.

Wraps the qeeqbox/social-analyzer CLI in fast mode and adds what real user runs
(read from the Store's Debugging data, September 2026) showed was missing:

- Several usernames per run. The CLI treats "alice bob" as ONE handle containing a
  space and then reports instagram.com/alice bob as found. We call it once per handle.
- Emails pasted into the username box. Three users did that in one week and got a
  failed run. We now search the part before the @ and point to holehe-email-osint
  for the address itself.
- Full names typed into the username box ("Lisa Vor", also what an AI assistant passes
  when asked to find a person). No site has a handle with a space, so the name is
  searched as its joined handle (lisavor, accents dropped) and the summary names the
  dotted and underscored forms to try next. It used to be skipped, and alone it failed.
- Site selection done here, always. The CLI's --type and --countries options select
  nothing in version 0.45, so the site list is built from the tool's own sites.json
  (category, country, named sites, top N by popularity) and the exact site URLs are
  passed down as --websites. Adult and dating sites are always part of the pool: the
  people who run this are investigators, and that is where the leads are.
- Filters only narrow. One that would leave no site to check (208 of the 299 category
  and country pairs the form offers, such as adult and dating sites based in India; a
  named site the scanner does not know; a country no site is based in) is left out
  with a note, and the scan covers what the other filters select. It used to fail.
- Rows enriched with the site's category, country and adult flag (the CLI returns
  only link, rate, title, text).
- Every scan is as deep as the scanner can go: all 999 sites by default, every output
  field it can produce (status, language, metadata, extracted patterns), and its most
  permissive detection level. The scanner's "slow" and "special" modes have no code
  behind them in 0.45, and its "extreme" level is a stricter gate that returns fewer
  hits, so neither is offered. Depth here means sites and detail, not a mode.
- A status message that can never fail the run. SDK 3.x validates the run object
  against an enum that lacks the APIFY_AI run origin (runs started by Apify's AI
  chatbot); the message is stored server-side before that parse, so a parse error is
  logged, not raised.
- Scan steps sized to the run's memory. Each step is its own scanner process of about
  300 MB (more when CPU is short), so 8 run at once at the default 4 GB, 5 at 2 GB and 1
  at 1 GB (eight at 2 GB peaked at 2046 MB and lost two steps to the OOM killer). A step
  that still comes back empty is retried alone after the others, and sites that could not
  be checked are counted and explained in the summary, never reported as "no profile".
- The run fails only when there is nothing to search (no usable username), when every
  scan broke before saving anything, or on an unexpected error with nothing saved, and
  OUTPUT still says why. A spending limit too small for one profile, or a run timeout
  too short for one scan, ends SUCCEEDED with nothing searched or charged and the
  numbers in the message: a limit the user set is an answer, not a fault.
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
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from apify import Actor

TOOL = 'social-analyzer'
SCANNER_WORKERS = 60          # the CLI hardcodes 15; 60 does all 999 sites in about 2 minutes
CHUNK_SIZE = 125              # sites per scanner process; each chunk is a visible step with its own rows
PARALLEL_CHUNKS = 8           # all steps of a full scan in flight at once: the scanner's own retry rounds
                              # dominate a process's wall time, so splitting saves nothing unless steps overlap
STEP_MEMORY_MB = 320          # one step is its own scanner process; with BASE_MEMORY_MB this fits every Apify run of
BASE_MEMORY_MB = 400          # 2026-10-07: 8 at once peaked at 2591 MB of 4 GB; 5 at 1907-1942 MB of 2 GB, in 113 s where
                              # 4 took 189 s side by side; 2 at 1 GB lost one to the OOM killer while 1 peaked at 583 MB.
                              # A step holds more while it waits for CPU (1 vCPU per 4 GB), hence the larger base
PROFILE_EVENT = 'profile'     # pay-per-event name for one profile row; the summary row is free
SCANNER_FIELDS = 'link,rate,title,text,status,type,country,language,rank,extracted,metadata'
MAX_USERNAMES = 25            # one scanner run per username
MIN_SECONDS_PER_USERNAME = 20  # do not start a handle we cannot finish
RUN_SAFETY_MARGIN_S = 60      # leave time to write the summary before the platform kills us
HOLEHE_URL = 'https://apify.com/anshumanatrey/holehe-email-osint'

HANDLE_RE = re.compile(r'^[A-Za-z0-9._-]{1,100}$')
EMAIL_RE = re.compile(r'^[^@\s/]+@[^@\s/]+\.[^@\s/]+$')
NAME_WORD_RE = re.compile(r"^[^\W\d_]+(?:['’-][^\W\d_]+)*$")   # letters of any script; O'Brien, Mary-Jane

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
    try:
        import importlib.util
        spec = importlib.util.find_spec('social-analyzer')
        for loc in (spec.submodule_search_locations or []) if spec else []:
            p = Path(loc) / 'data' / 'sites.json'
            if p.is_file():
                return p
    except Exception:  # noqa: BLE001
        pass
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


def handle_from_name(text: str) -> tuple[str, list[str]] | None:
    """'Lisa Vor' -> ('lisavor', ['lisa.vor', 'lisa_vor']); 'José García' -> ('josegarcia', ...).

    Only text that reads as a person's name: two to four words of letters. The joined
    form is the handle people use most; the dotted and underscored ones are returned
    for the note, not searched, so a name costs what one username costs.
    """
    words = text.split()
    if not 2 <= len(words) <= 4 or not all(NAME_WORD_RE.match(w) for w in words):
        return None
    ascii_words = [re.sub(r"['’-]", '', unicodedata.normalize('NFKD', w).encode('ascii', 'ignore').decode()).lower()
                   for w in words]
    if not all(ascii_words):
        return None                 # a script with no Latin letters (Иван Петров) has no handle to guess
    return ''.join(ascii_words), ['.'.join(ascii_words), '_'.join(ascii_words)]


def parse_usernames(inp: dict) -> tuple[list[dict], list[str]]:
    """Collect targets from `username` (text, may hold a list) and `usernames` (list).

    Returns (targets, problems). Each target: {handle, original, fromEmail, fromName, alsoTry}.
    An email address becomes a search for its local part plus a pointer to holehe; a
    full name becomes a search for its joined handle (handle_from_name).
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
        from_email, from_name, also = False, False, []
        if any(c.isspace() for c in tok):
            name = handle_from_name(tok.lstrip('@').rstrip('.,;:'))
            if name is None:
                problems.append(f'"{tok}" contains a space. Usernames have no spaces; separate several usernames with commas.')
                continue
            (handle, also), from_name = name, True
        else:
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
        targets.append({'handle': handle, 'original': tok, 'fromEmail': from_email, 'fromName': from_name, 'alsoTry': also})
    if len(targets) > MAX_USERNAMES:
        problems.append(f'{len(targets)} usernames given; this run checks the first {MAX_USERNAMES}')
        targets = targets[:MAX_USERNAMES]
    return targets, problems


def as_list(value) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in re.split(r'[,;\n ]+', value) if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def countries_of(pool: list[dict], n: int = 3) -> list[str]:
    """The countries most of these sites are based in, most common first."""
    counts: dict[str, int] = {}
    for s in pool:
        if s['country']:
            counts[s['country']] = counts.get(s['country'], 0) + 1
    return sorted(counts, key=counts.get, reverse=True)[:n]


def select_sites(inp: dict, sites: list[dict], top: int) -> tuple[list[dict] | None, dict]:
    """Choose the sites to probe for every username.

    The user's filters apply in turn (named sites, one category, countries), then the
    most popular `top` sites by global rank. Every site in the scanner's list is
    eligible, adult and dating sites included. Options only narrow: a filter that would
    leave no site to check is left out, with the reason in info['dropped'], and the scan
    covers what the other filters select; info['filters'] holds the filters applied.
    Returns (selected sites, info). None means the site list was not available and the
    scanner's own top N has to be used.
    """
    wanted_sites = [w.lower().removeprefix('https://').removeprefix('http://').removeprefix('www.').strip('/')
                    for w in as_list(inp.get('websites'))]
    category = inp.get('siteType') or inp.get('siteCategory') or ''
    category = category.strip() if isinstance(category, str) else ''
    category = CATEGORY_ALIASES.get(category.lower(), category)
    countries = [COUNTRY_ALIASES.get(c.lower(), c) for c in as_list(inp.get('countries'))]

    info = {'filters': {}, 'unknownSites': [], 'suggestions': {}, 'sitesMatched': None, 'dropped': []}
    if not sites:
        info['error'] = 'site list unavailable; the scanner picked its own top sites'
        return None, info

    chosen, named = sites, False
    if wanted_sites:
        hits, hosts = [], {s['host'] for s in sites}
        for w in wanted_sites:
            matched = [s for s in sites if w == s['host'] or w in s['host'] or s['host'] in w]
            if not matched:
                info['unknownSites'].append(w)
                close = difflib.get_close_matches(w, hosts, n=3, cutoff=0.6)
                if close:
                    info['suggestions'][w] = close
            hits += matched
        if hits:
            chosen, named = hits, True
            info['filters']['websites'] = wanted_sites
        else:
            info['dropped'].append(f'None of the sites you named is one of the {len(sites)} sites this scanner checks, '
                                   f'so that filter was left out.')
    if category and category in CATEGORY_TITLES:
        narrowed = [s for s in chosen if s['category'] == category]
        if narrowed:
            chosen = narrowed
            info['filters']['category'] = category
        else:
            info['dropped'].append(f'{"None of the sites you named is" if named else "No site in the list is"} in the '
                                   f'category "{CATEGORY_TITLES[category]}", so the category filter was left out.')
    elif category:
        info['unknownCategory'] = category
    if countries:
        narrowed = [s for s in chosen if s['country'] in countries]
        if narrowed:
            chosen = narrowed
            info['filters']['countries'] = countries
        else:
            lead = ('None of the sites you named is' if named else
                    f'None of the {len(chosen)} sites in "{CATEGORY_TITLES[category]}" is' if 'category' in info['filters'] else
                    'No site in the list is')
            based = countries_of(chosen)
            info['dropped'].append(f'{lead} based in {" or ".join(countries)}, so the country filter was left out.'
                                   + (f' The most common countries there: {", ".join(based)}.' if based else ''))

    chosen = sorted({s['url']: s for s in chosen}.values(), key=lambda s: (s['rank'] is None, s['rank'] or 0))
    if not named:
        chosen = chosen[:top]
    info['sitesMatched'] = len(chosen)
    return chosen, info


# ------------------------------------------------------------------------- tool --
def build_command(username: str, *, top: int, site_urls: list[str] | None, extract: bool, metadata: bool) -> list[str]:
    """Run the scanner through src/scan.py (same CLI, more workers).

    Always asks for every field the scanner can emit, for all probed sites (method all,
    profiles all) with no confidence filter: the confidence filter is applied here, on
    the match rate, because the scanner's own --filter silently does nothing unless
    `status` is among the requested fields. --websites gets exact URL patterns from
    sites.json; a bare host token would over-match (t.me also selects about.me).
    """
    cmd = [sys.executable, '-m', 'src.scan', '--workers', str(SCANNER_WORKERS),
           '--username', username, '--output', 'json', '--mode', 'fast',
           '--method', 'all', '--filter', 'all', '--profiles', 'all',
           '--options', SCANNER_FIELDS, '--trim']
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
    """Same thresholds as the scanner's own status: good = 100%, maybe = 50-99%, bad = below."""
    v = parse_rate(rate)
    if v is None:
        return 'unknown'
    return 'high' if v >= 100 else 'medium' if v >= 50 else 'low'


MIN_RATE = {'good': 100.0, 'good,maybe': 50.0, 'all': 0.0}

# A username with nothing else means the widest scan there is: every site, worldwide,
# every category, every match kept and labelled, every detail extracted, an hour to do it.
MAX_SETTINGS = {'top': 999, 'filter': 'all', 'extract': True, 'metadata': True, 'timeout': 3600}


def resolve_settings(inp: dict) -> dict:
    """Effective run settings: whatever the user set, everything else at maximum."""
    top = inp.get('top')
    conf = inp.get('filter')
    timeout = inp.get('timeout')
    return {
        'top': max(10, min(int(top), 999)) if isinstance(top, (int, float)) and top else MAX_SETTINGS['top'],
        'filter': conf if conf in MIN_RATE else MAX_SETTINGS['filter'],
        'extract': bool(inp['extract']) if isinstance(inp.get('extract'), bool) else MAX_SETTINGS['extract'],
        'metadata': bool(inp['metadata']) if isinstance(inp.get('metadata'), bool) else MAX_SETTINGS['metadata'],
        'timeout': max(60, min(int(timeout), 3600)) if isinstance(timeout, (int, float)) and timeout else MAX_SETTINGS['timeout'],
    }


def keep_by_confidence(profiles: list[dict], confidence_filter: str) -> list[dict]:
    """Confident only = 100% match; confident and possible = 50% and up; all = everything."""
    floor = MIN_RATE.get(confidence_filter, 100.0)
    return [p for p in profiles if (parse_rate(p.get('rate')) or 0) >= floor]


def clean_value(v):
    """The scanner writes the string 'unavailable' where it has nothing."""
    return None if v in (None, '', 'unavailable') else v


def chunked(items: list, size: int) -> list[list]:
    """[1..7], 3 -> [[1,2,3],[4,5,6],[7]]."""
    return [items[i:i + size] for i in range(0, len(items), size)] if items else []


def progress_line(done_sites: int, total_sites: int, usernames_done: int, usernames_total: int,
                  profiles: int, current: str | None) -> str:
    """One sentence for the run's status message while the scan is running."""
    pct = int(100 * done_sites / total_sites) if total_sites else 0
    who = f'{usernames_done} of {usernames_total} usernames finished' if usernames_total > 1 else current or ''
    now = f', now checking {current}' if current and usernames_total > 1 else ''
    return (f'{pct}% done: {done_sites:,} of {total_sites:,} site checks, {profiles} profiles so far. '
            f'{who}{now}.').replace(' .', '.').replace('..', '.')


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
        'pageTitle': clean_value(profile.get('title')),
        'pageText': clean_value(profile.get('text')),
        'language': clean_value(profile.get('language')),
        'metadata': clean_value(profile.get('metadata')),
        'extracted': clean_value(profile.get('extracted')),
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


def parallel_steps() -> int:
    """How many scan steps fit in this run's memory at once: 8 at the default 4 GB, 5 at 2 GB, 1 at 1 GB.

    Eight at 2 GB peaked at 2046 MB and two steps were OOM-killed (exit -9), losing 250
    sites (build 1.0.8 on Apify, 2026-09-12). Off the platform the memory is unknown: all 8.
    """
    config = getattr(Actor, 'configuration', None) or getattr(Actor, 'config', None)
    memory = getattr(config, 'memory_mbytes', None) if config else None
    if not isinstance(memory, (int, float)) or memory <= 0:
        return PARALLEL_CHUNKS
    return max(1, min(PARALLEL_CHUNKS, int((memory - BASE_MEMORY_MB) // STEP_MEMORY_MB)))


def step_failure(parsed: dict) -> str:
    """Why a scan step came back with no data, in words."""
    code = parsed.get('_exit')
    if code == -9:
        return 'the scanner process was stopped by the system (exit -9, usually out of memory)'
    return f'the scanner returned no data (exit {code}, {parsed.get("parse_error")})'


def usd(amount) -> str:
    """A limit or a price as people read it: $0.0043, $0.005, $5, $0."""
    return '$' + (f'{float(amount):.4f}'.rstrip('0').rstrip('.') or '0')


def plural(n: int, word: str) -> str:
    return f'{n:,} {word}' + ('' if n == 1 else 's')


class Delivery:
    """Pushes profile rows charged as PROFILE_EVENT and notices the spending limit (as maigret-username-search does)."""

    def __init__(self, cm=None):
        self.cm = cm or Actor.get_charging_manager()
        self.ppe = self.cm.get_pricing_info().is_pay_per_event
        self.rows = 0
        self.limit = False

    def can_pay(self) -> bool:
        if self.ppe and not self.limit:
            left = self.cm.calculate_max_event_charge_count_within_limit(PROFILE_EVENT)
            self.limit = left is not None and left < 1
        return not self.limit

    def budget_note(self) -> str:
        """The run's spending limit set against what one profile costs, for a run that could pay for none."""
        info = self.cm.get_pricing_info()
        price = info.per_event_prices.get(PROFILE_EVENT)
        return (f"this run's spending limit is {usd(info.max_total_charge_usd)}"
                + (f', which cannot pay for one profile ({usd(price)})' if price else ''))

    async def push(self, rows: list[dict]) -> int:
        """Returns how many rows were delivered: the SDK drops the rows the limit cannot pay for."""
        if not rows:
            return 0
        result = await Actor.push_data(rows, charged_event_name=PROFILE_EVENT)
        done = result.charged_count if self.ppe else len(rows)
        if self.ppe and (result.event_charge_limit_reached or done < len(rows)):
            self.limit = True
        self.rows += done
        return done


async def write_output(record: dict) -> None:
    try:
        await Actor.set_value('OUTPUT', record)
    except Exception as exc:  # noqa: BLE001 - a record that cannot be written must not end the scan
        Actor.log.warning(f'Could not write the OUTPUT record: {exc}')


async def fail_run(message: str, notes: list[str] | None = None) -> None:
    """End the run FAILED, with the reason in the status message and in the OUTPUT record."""
    Actor.log.error(message)
    await write_output({'status': 'failed', 'message': message, 'notes': notes or []})
    await Actor.fail(status_message=message[:500])


# -------------------------------------------------------------------------- main --
async def main() -> None:
    async with Actor:
        Actor.log.info('Social Analyzer starting')
        inp = await Actor.get_input()
        if not isinstance(inp, dict):
            inp = {}                    # input that is not an object reads as no input

        targets, problems = parse_usernames(inp)
        for p in problems:
            Actor.log.warning(p)
        if not targets:
            hint = problems[0] if problems else 'No username given.'
            await fail_run(f'{hint} Enter the username to look up, for example elonmusk. You can paste an @handle, '
                           f'a profile link or a full name, and list several separated by commas.', problems)
            return

        if not shutil.which(TOOL):
            await fail_run('The scanner is missing from this build. This is on us, not you: please report it '
                           'and we will ship a fixed build within hours.')
            return

        settings = resolve_settings(inp)
        top, confidence_filter = settings['top'], settings['filter']
        extract, metadata = settings['extract'], settings['metadata']

        sites = load_sites()
        site_index = {s['host']: s for s in sites}
        selected, selection = select_sites(inp, sites, top)
        site_urls = [s['url'] for s in selected] if selected is not None else None
        sites_per_username = selection['sitesMatched'] if selected is not None else top

        # Notes that changed what was searched come first: the status message keeps 500 characters.
        notes: list[str] = list(selection['dropped'])
        for t in targets:
            if t['fromName']:
                notes.append(f'"{t["original"]}" is a name, and usernames have no spaces, so it is read as the handle '
                             f'{t["handle"]}. People with that name also use {" and ".join(t["alsoTry"])}: add them to '
                             f'search those too.')
        notes += problems
        for w in selection['unknownSites']:
            sugg = selection['suggestions'].get(w)
            notes.append(f'Site "{w}" is not in the list' + (f'; did you mean {", ".join(sugg)}?' if sugg else '.'))
        if selection.get('unknownCategory'):
            notes.append(f'Category "{selection["unknownCategory"]}" is not known; '
                         f'valid values: {", ".join(k for k in CATEGORY_TITLES if k)}. No category filter applied.')
        if selection.get('error'):
            notes.append(selection['error'])
        for t in targets:
            if t['fromEmail']:
                notes.append(f'"{t["original"]}" is an email address. Searched the part before the @ '
                             f'("{t["handle"]}") as a username. To find accounts registered with the '
                             f'email itself, use {HOLEHE_URL}')
        legacy_mode = inp.get('mode')
        if legacy_mode and legacy_mode != 'fast':
            notes.append(f'Scan depth "{legacy_mode}" is no longer offered (it returned no data in the '
                         f'current scanner); ran the fast scan instead.')
        for note in selection['dropped']:
            Actor.log.warning(note)
        steps_at_once = parallel_steps()
        Actor.log.info(f'{len(targets)} username(s), {sites_per_username} site(s) each, '
                       f'filters={selection["filters"] or "none"}, {steps_at_once} scan step(s) at once')

        # Time budget: the user's limit, capped so the summary is written before the
        # platform kills the run. The CLI has no partial output, so an unfinished step
        # yields nothing, which is why we refuse to start a username we cannot finish.
        user_limit = settings['timeout']
        platform_left = seconds_left_in_run()
        budget = user_limit if platform_left is None else min(user_limit, platform_left - RUN_SAFETY_MARGIN_S)
        deadline = time.monotonic() + budget
        delivery = Delivery()

        checked_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        per_username: list[dict] = []
        totals = {'profiles': 0, 'high': 0, 'medium': 0, 'low': 0, 'failed': 0, 'unknown': 0}
        progress = {'done': 0}
        total_units = sites_per_username * len(targets)
        lost_all: dict[str, int] = {}    # sites not checked over the run, by cause: 'time', 'limit' or a scanner failure
        skipped_for_time: list[str] = []
        skipped_for_limit: list[str] = []
        crash: str | None = None

        def push_progress(current: str | None) -> str:
            line = progress_line(progress['done'], total_units, len(per_username), len(targets), totals['profiles'], current)
            Actor.log.info(line)
            return line

        async def run_chunk(username: str, urls: list[str] | None, remaining: float) -> tuple[dict | None, float]:
            cmd = build_command(username, top=top, site_urls=urls, extract=extract, metadata=metadata)
            t0 = time.monotonic()
            try:
                result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=max(10, remaining))
            except subprocess.TimeoutExpired:
                return None, time.monotonic() - t0
            noise = ('REPLACEMENT CHARACTER', 'XMLParsedAsHTMLWarning', 'warnings.filterwarnings', 'import warnings',
                     'BeautifulSoup(', 'from bs4 import')
            for line in [l for l in result.stderr.splitlines() if l.strip() and not any(n in l for n in noise)][-3:]:
                Actor.log.warning(f'{username}: {line[:300]}')
            parsed = parse_output(result.stdout)
            parsed['_exit'] = result.returncode
            return parsed, time.monotonic() - t0

        async def scan(t: dict, i: int) -> None:
            username = t['handle']
            chunks = chunked(site_urls, CHUNK_SIZE) if site_urls else [None]   # None = scanner's own top N
            entry = {'username': username, 'original': t['original'], 'fromEmail': t['fromEmail'], 'fromName': t['fromName'],
                     'status': None, 'sitesChecked': 0, 'sitesNotChecked': 0, 'profilesFound': 0, 'high': 0, 'medium': 0,
                     'low': 0, 'sitesNotFound': 0, 'sitesFailed': 0, 'seconds': 0.0, 'note': None}
            lost: dict[str, int] = {}    # this username's sites not checked, by cause
            Actor.log.info(f'[{i}/{len(targets)}] {username}: {sites_per_username} sites in {len(chunks)} step(s)')
            await safe_status(push_progress(username))
            t_user = time.monotonic()
            sem = asyncio.Semaphore(steps_at_once)

            def count_lost(cause: str, size: int) -> None:
                lost[cause] = lost.get(cause, 0) + size
                lost_all[cause] = lost_all.get(cause, 0) + size

            async def step(urls):
                async with sem:
                    if delivery.limit:
                        return urls, None, 'limit'        # nothing a later step finds could be paid for
                    remaining_now = deadline - time.monotonic()
                    if remaining_now < 15:
                        return urls, None, 'time'
                    parsed, _ = await run_chunk(username, urls, remaining_now)
                    return urls, parsed, None if parsed is not None else 'time'

            async def take(urls, parsed, why) -> None:
                """One step's outcome: its rows delivered, or its sites counted as not checked, with the cause."""
                size = len(urls) if urls else top
                progress['done'] += size
                if why is None and 'parse_error' in parsed:
                    why = step_failure(parsed)
                    Actor.log.error(f'{username}: a step of {size} sites came back empty again: {why}')
                elif why is None and delivery.limit:
                    why = 'limit'
                if why:
                    count_lost(why, size)
                    return
                detected_all = parsed.get('detected') or []
                detected = keep_by_confidence(detected_all, confidence_filter)
                detected.sort(key=lambda p: parse_rate(p.get('rate')) or 0, reverse=True)
                rows = [enrich(p, username, site_index, checked_at) for p in detected]
                saved = await delivery.push(rows)
                if saved < len(rows):
                    count_lost('limit', 0)                # checked, but the limit paid for only the strongest rows
                entry['sitesChecked'] += size
                entry['sitesFailed'] += len(parsed.get('failed') or [])
                entry['sitesNotFound'] += len(parsed.get('unknown') or []) + (len(detected_all) - len(detected))
                entry['profilesFound'] += saved
                for r in rows[:saved]:
                    entry[r['confidence'] if r['confidence'] in ('high', 'medium', 'low') else 'low'] += 1
                totals['profiles'] += saved
                for k in ('high', 'medium', 'low'):
                    totals[k] = sum(e[k] for e in per_username) + entry[k]
                totals['failed'] = sum(e['sitesFailed'] for e in per_username) + entry['sitesFailed']
                totals['unknown'] = sum(e['sitesNotFound'] for e in per_username) + entry['sitesNotFound']
                line = push_progress(username)
                await safe_status(line)
                await write_output({'status': 'running', 'percentDone': int(100 * progress['done'] / total_units) if total_units else 0,
                                    'message': line, 'perUsername': per_username + [entry]})

            try:
                retry = []
                for fut in asyncio.as_completed([asyncio.ensure_future(step(urls)) for urls in chunks]):
                    urls, parsed, why = await fut
                    if why is None and 'parse_error' in parsed:
                        # Usually a process killed when memory ran short: alone, after the others, it fits.
                        Actor.log.warning(f'{username}: a step of {len(urls) if urls else top} sites came back empty '
                                          f'({step_failure(parsed)}); retrying it on its own after the others')
                        retry.append(urls)
                        continue
                    await take(urls, parsed, why)
                for urls in retry:
                    remaining_now = deadline - time.monotonic()
                    why = 'limit' if delivery.limit else 'time' if remaining_now < 15 else None
                    parsed = None
                    if why is None:
                        parsed, _ = await run_chunk(username, urls, remaining_now)
                        why = None if parsed is not None else 'time'
                    await take(urls, parsed, why)
            finally:
                entry['seconds'] = round(time.monotonic() - t_user, 1)
                entry['sitesNotChecked'] = sum(lost.values())
                per_username.append(entry)        # a crash mid-scan keeps what this username already delivered

            failures = [c for c in lost if c not in ('time', 'limit')]
            if not entry['sitesChecked']:
                entry['status'] = 'error' if failures else 'not_run'
            else:
                entry['status'] = 'partial' if lost else 'done'
            if failures and not entry['sitesChecked']:
                entry['note'] = (f'the scanner returned no data for this username ({failures[0]}). '
                                 f'Usually a temporary problem; run it again.')
            elif lost:
                entry['note'] = '; '.join(
                    f'stopped after {entry["sitesChecked"]} of {sites_per_username} sites: '
                    + ('your spending limit for this run was reached' if cause == 'limit' else 'time limit reached')
                    if cause in ('time', 'limit') else f'{n} of {sites_per_username} sites could not be checked: {cause}'
                    for cause, n in lost.items())
            elif not entry['profilesFound']:
                entry['note'] = 'no profile with this exact handle on the sites checked (or none above your confidence setting)'
            Actor.log.info(f'{username}: {entry["profilesFound"]} profiles ({entry["high"]} high) on '
                           f'{entry["sitesChecked"]} sites in {entry["seconds"]}s')

        # A limit the user or the run set is an answer, not a fault: nothing searched, nothing charged.
        refused = None
        if budget < MIN_SECONDS_PER_USERNAME:
            refused = (f'Nothing was searched and nothing was charged: the run had {max(0, round(platform_left or 0))} seconds '
                       f'left, too little for a scan. Raise the run timeout in the run options: one username on all '
                       f'999 sites takes about 2 to 3 minutes.')
        elif not delivery.can_pay():
            refused = (f'Nothing was searched and nothing was charged: {delivery.budget_note()}. '
                       "Raise the run's maximum cost in the run options, or add credit, then run it again.")
        else:
            i = 0
            try:
                for i, t in enumerate(targets, 1):
                    if not delivery.can_pay():
                        skipped_for_limit.append(t['handle'])
                    elif deadline - time.monotonic() < MIN_SECONDS_PER_USERNAME:
                        skipped_for_time.append(t['handle'])
                    else:
                        await scan(t, i)
            except Exception as exc:  # noqa: BLE001 - whatever broke, the rows already saved and the summary still go out
                crash = f'{type(exc).__name__}: {exc}'[:300]
                Actor.log.exception('The scan stopped with an unexpected error')
                if per_username and per_username[-1]['status'] is None:
                    last = per_username[-1]
                    last['status'] = 'partial' if last['sitesChecked'] else 'error'
                    last['sitesNotChecked'] = sites_per_username - last['sitesChecked']
                    last['note'] = f'stopped by an unexpected error: {crash}'
                not_started = [t['handle'] for t in targets[i:]]
                if not_started:
                    notes.append(f'The error stopped the run before {plural(len(not_started), "username")} could start: '
                                 f'{", ".join(not_started)}. Run them again.')

        # ---- summary: one free row and the final OUTPUT record
        duration = round(time.monotonic() - started, 1)
        ran = [e for e in per_username if e['sitesChecked']]
        broke = [e for e in per_username if e['status'] == 'error']
        failed = not refused and not ran and bool(crash or broke)
        not_run = not ran and not failed
        failure_causes = [c for c in lost_all if c not in ('time', 'limit')]
        if skipped_for_limit:
            notes.append(f'Your spending limit for this run was reached before {len(skipped_for_limit)} username(s) could '
                         f'start: {", ".join(skipped_for_limit)}. Raise the limit or run them separately.')
        if skipped_for_time:
            notes.append(f'Time limit reached before {len(skipped_for_time)} username(s) could start: '
                         f'{", ".join(skipped_for_time)}. Raise the time limit or check fewer sites.')

        if refused:
            headline = refused
        elif failed and crash:
            headline = f'No search could run: {crash}'
        elif failed:
            headline = (f'No results: the scanner returned no data for {", ".join(e["username"] for e in broke)} '
                        f'({failure_causes[0] if failure_causes else "no output"}). Usually a temporary problem; run it again.')
        elif not_run:
            headline = (f'Time limit reached before {", ".join(e["username"] for e in per_username) or "any username"} '
                        f'finished on {sites_per_username} sites, so there is nothing to show. A full scan of all sites needs '
                        f'about 2 to 3 minutes per username: raise the time limit or lower "How many sites to check".')
        elif totals['profiles'] == 0:
            widen = ('Try the "confident and possible" setting to see weaker matches.' if confidence_filter == 'good'
                     else 'Try "everything" to see weak guesses too.' if confidence_filter == 'good,maybe'
                     else 'Even weak guesses were included, so this handle is not in use on these sites.')
            headline = (f'No profiles found for {", ".join(e["username"] for e in ran)} on {plural(sites_per_username, "site")}. '
                        f'The handle may be spelled differently there, or the person does not use it publicly. {widen}').strip()
        else:
            who = ran[0]['username'] if len(ran) == 1 else f'{len(ran)} usernames'
            headline = (f'Found {plural(totals["profiles"], "profile")} for {who} across {plural(sites_per_username, "site")}: '
                        f'{totals["high"]} high, {totals["medium"]} medium, {totals["low"]} low confidence. Strongest first. '
                        f'{plural(totals["unknown"], "site")} had no profile with this handle, {totals["failed"]} did not answer.')
        if ran:
            if failure_causes:
                headline += (f' {plural(sum(lost_all[c] for c in failure_causes), "site check")} could not be done because a '
                             f'scan step stopped ({failure_causes[0]}); run again to cover them.')
            if 'limit' in lost_all:
                headline += ' Stopped early: your spending limit for this run was reached. Raise the limit to get the rest.'
            elif 'time' in lost_all:
                headline += ' Stopped early: the run reached its time limit. Raise the time limit to get the rest.'
            if crash:
                headline += f' Stopped early by an unexpected error: {crash.rstrip(".")}.'
        if notes and not refused:
            headline += ' ' + ' '.join(notes)

        summary = {
            'recordType': 'summary',
            'status': 'failed' if failed else 'not_run' if not_run else 'finished',
            'percentDone': 100,
            'usernames': [t['handle'] for t in targets],
            'usernamesRequested': len(targets),
            'usernamesChecked': len(ran),
            'sitesPerUsername': sites_per_username,
            'siteChecksDone': sum(e['sitesChecked'] for e in per_username),
            'sitesNotChecked': sum(e['sitesNotChecked'] for e in per_username),
            'filters': selection['filters'],
            'confidenceFilter': confidence_filter,
            'settings': settings,
            'profilesFound': totals['profiles'],
            'highConfidence': totals['high'],
            'mediumConfidence': totals['medium'],
            'lowConfidence': totals['low'],
            'sitesNotFound': totals['unknown'],
            'sitesFailed': totals['failed'],
            'durationSeconds': duration,
            'perUsername': per_username,
            'notes': notes,
            'message': headline,
            'success': not failed,
            'checkedAt': checked_at,
        }
        try:
            await Actor.push_data(summary)
        except Exception as exc:  # noqa: BLE001 - the OUTPUT record below still carries the summary
            Actor.log.warning(f'Could not save the summary row: {exc}')
        await write_output(summary)
        Actor.log.info(headline)
        if failed:
            await Actor.fail(status_message=headline[:500])
        else:
            await safe_status(headline[:500])


if __name__ == '__main__':
    asyncio.run(main())
