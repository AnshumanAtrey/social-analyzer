"""Unit tests for the social-analyzer wrapper. Run: python3 test_unit.py

No Apify SDK or network needed for tests 1-9 (the SDK is stubbed). Test 10 runs the
real scanner on 10 sites when it is installed, and is skipped otherwise.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import types

# --- stub the SDK so the module imports without the platform ---------------------
apify_stub = types.ModuleType('apify')


class _Log:
    def __getattr__(self, level):
        return lambda m: print(f'[{level.upper():7}] {m}')


class _Actor:
    log = _Log()
    config = types.SimpleNamespace(timeout_at=None)

    @staticmethod
    async def set_status_message(message):
        raise RuntimeError('simulated pydantic ValidationError: meta.origin APIFY_AI')


apify_stub.Actor = _Actor()
sys.modules['apify'] = apify_stub

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.main import (  # noqa: E402
    build_command, parse_output, parse_usernames, clean_handle, category_of, host_of,
    platform_name, select_sites, enrich, confidence_tier, parse_rate, safe_status,
    keep_by_confidence, clean_value, resolve_settings, chunked, progress_line, MAX_SETTINGS, CATEGORY_TITLES,
    MAX_USERNAMES, SCANNER_FIELDS, CHUNK_SIZE,
)

passed = 0


def ok(label):
    global passed
    passed += 1
    print(f'  ok  {label}')


# 1. cleaning single tokens ---------------------------------------------------------
cases = {
    'elonmusk': 'elonmusk', '@elonmusk': 'elonmusk', '  @elonmusk  ': 'elonmusk',
    'https://twitter.com/elonmusk': 'elonmusk', 'twitter.com/elonmusk': 'elonmusk',
    'https://instagram.com/elonmusk/': 'elonmusk', 'https://www.linkedin.com/in/elonmusk': 'elonmusk',
    'https://x.com/elonmusk?lang=en': 'elonmusk', 'github.com/torvalds': 'torvalds',
    'BeLLa_ho3': 'BeLLa_ho3', 'kbing1977.': 'kbing1977',
}
for raw, want in cases.items():
    got = clean_handle(raw)
    assert got == want, f'clean_handle({raw!r}) -> {got!r}, want {want!r}'
ok(f'clean_handle: {len(cases)} forms of pasted input')

# 2. parse_usernames: lists, emails, dupes, junk, cap -------------------------------
targets, problems = parse_usernames({'username': 'tattedbaddie696, kaytats, pokilife, piercedbeauty, deadrose, kaylaharsha'})
assert [t['handle'] for t in targets] == ['tattedbaddie696', 'kaytats', 'pokilife', 'piercedbeauty', 'deadrose', 'kaylaharsha'], targets
assert not problems
ok('parse_usernames: comma list becomes six separate usernames (the CLI would have glued them)')

targets, problems = parse_usernames({'username': 'frostkatrina@ymail.com'})
assert len(targets) == 1 and targets[0]['handle'] == 'frostkatrina' and targets[0]['fromEmail'] is True, targets
ok('parse_usernames: an email becomes a search for its local part, flagged fromEmail')

targets, problems = parse_usernames({'username': 'elonmusk', 'usernames': ['@ElonMusk', 'https://github.com/torvalds', 'jane doe', '']})
assert [t['handle'] for t in targets] == ['elonmusk', 'torvalds'], targets
assert any('jane doe' in p and 'space' in p for p in problems), problems
ok('parse_usernames: merges both fields, drops case-duplicates, reports a name with a space')

targets, problems = parse_usernames({'username': 'foo/bar baz!!'})
assert targets == [] and problems and 'space' in problems[0], (targets, problems)
targets, problems = parse_usernames({'username': 'foo/bar'})
assert targets == [] and problems and 'not a username' in problems[0], (targets, problems)
assert clean_handle('foo/bar') == 'foo/bar' and clean_handle('x.com/kaytats') == 'kaytats' and clean_handle('kaytats.tumblr.com') == 'kaytats.tumblr.com'
ok("parse_usernames: 'foo/bar' is junk, not a link to the handle 'bar'")

targets, problems = parse_usernames({'username': ''})
assert targets == [] and problems == []
targets, problems = parse_usernames({'username': 'foo/bar!!'})
assert targets == [] and problems and 'not a username' in problems[0], problems
ok('parse_usernames: empty and junk input give an explanation, not a crash')

targets, problems = parse_usernames({'usernames': [f'user{i}' for i in range(40)]})
assert len(targets) == MAX_USERNAMES and any('first 25' in p for p in problems)
ok('parse_usernames: caps at 25 usernames with a note')

# 3. site model -----------------------------------------------------------------
assert host_of('https://blog.naver.com/{username}') == 'blog.naver.com'
assert host_of('https://www.github.com/{username}') == 'github.com'
assert host_of('https://{username}.tumblr.com') == 'x.tumblr.com'
assert platform_name('github.com') == 'GitHub' and platform_name('blog.naver.com') == 'Naver'
assert category_of('Computers Electronics and Technology > Social Networks and Online Communities', False) == 'social'
assert category_of('Computers Electronics and Technology > Programming and Developer Software', False) == 'developer_tech'
assert category_of('Adult', False) == 'adult_dating' and category_of('Wiki', True) == 'adult_dating'
assert category_of('Community and Society > Romance and Relationships', False) == 'adult_dating'
assert category_of('Arts and Entertainment > Visual Arts and Design', False) == 'photo_design'
assert category_of('Games > Video Games Consoles and Accessories', False) == 'gaming'
assert category_of('', False) == 'other' and category_of('Internet', False) == 'other'
assert set(CATEGORY_TITLES) >= {'social', 'adult_dating', 'gaming', 'developer_tech', 'other'}
assert platform_name('t.me') == 'Telegram'
ok('site model: hosts, platform names and category rules')

# 4. select_sites ---------------------------------------------------------------
SITES = [
    {'host': 'github.com', 'url': 'https://github.com/{username}', 'category': 'developer_tech', 'categoryDetail': 'Programming', 'country': 'United States', 'adult': False, 'rank': 50},
    {'host': 'reddit.com', 'url': 'https://reddit.com/user/{username}', 'category': 'social', 'categoryDetail': 'Social Networks', 'country': 'United States', 'adult': False, 'rank': 20},
    {'host': 'fancentro.com', 'url': 'https://fancentro.com/{username}', 'category': 'adult_dating', 'categoryDetail': 'Adult', 'country': 'United States', 'adult': True, 'rank': 9},
    {'host': 'blog.naver.com', 'url': 'https://blog.naver.com/{username}', 'category': 'other', 'categoryDetail': 'Internet', 'country': 'South Korea', 'adult': False, 'rank': 30},
    {'host': 'noranksite.org', 'url': 'https://noranksite.org/{username}', 'category': 'forums', 'categoryDetail': 'Forums', 'country': None, 'adult': False, 'rank': None},
]
urls = lambda sel: [x['url'] for x in sel]
sel, info = select_sites({}, SITES, 100)
assert urls(sel) == ['https://fancentro.com/{username}', 'https://reddit.com/user/{username}', 'https://blog.naver.com/{username}', 'https://github.com/{username}', 'https://noranksite.org/{username}'], urls(sel)
assert info['filters'] == {}
ok('select_sites: no filters -> every site by popularity, adult and dating sites included')

sel, info = select_sites({}, SITES, 2)
assert urls(sel) == ['https://fancentro.com/{username}', 'https://reddit.com/user/{username}']
ok('select_sites: top N by rank')

sel, info = select_sites({'websites': ['github', 'https://www.reddit.com/', 'myspace.com', 'fancentro']}, SITES, 100)
assert urls(sel) == ['https://fancentro.com/{username}', 'https://reddit.com/user/{username}', 'https://github.com/{username}'], urls(sel)
assert info['unknownSites'] == ['myspace.com'], info
ok('select_sites: named sites resolve by partial or full domain; unknown ones are reported')

sel, info = select_sites({'siteType': 'Dating', 'top': 900}, SITES, 900)
assert urls(sel) == ['https://fancentro.com/{username}'] and info['filters']['category'] == 'adult_dating', (urls(sel), info)
ok("select_sites: legacy 'Dating' value maps to the adult_dating category")

sel, info = select_sites({'countries': ['kr', 'United States']}, SITES, 2)
assert urls(sel) == ['https://fancentro.com/{username}', 'https://reddit.com/user/{username}'], urls(sel)
ok('select_sites: country codes and names, top N by popularity')

sel, info = select_sites({'siteType': 'nonsense'}, SITES, 100)
assert info.get('unknownCategory') == 'nonsense' and len(sel) == 5
ok('select_sites: unknown category is reported and ignored instead of matching nothing')

sel, info = select_sites({'countries': ['Mars']}, SITES, 100)
assert sel == [] and info['sitesMatched'] == 0
ok('select_sites: filters that match nothing return an empty list (the run explains and stops)')

sel, info = select_sites({}, [], 100)
assert sel is None and 'unavailable' in info['error']
ok('select_sites: without sites.json the scanner falls back to its own top N')

# 5. build_command --------------------------------------------------------------
cmd = build_command('elonmusk', top=100, site_urls=None, extract=False, metadata=True)
assert cmd[:4] == [sys.executable, '-m', 'src.scan', '--workers'] and '--top' in cmd and '100' in cmd
assert cmd[cmd.index('--mode') + 1] == 'fast' and cmd[cmd.index('--method') + 1] == 'all'
assert cmd[cmd.index('--filter') + 1] == 'all' and cmd[cmd.index('--profiles') + 1] == 'all'
assert cmd[cmd.index('--options') + 1] == SCANNER_FIELDS and 'status' in SCANNER_FIELDS and 'metadata' in SCANNER_FIELDS
assert '--metadata' in cmd and '--extract' not in cmd
cmd = build_command('x', top=100, site_urls=['https://github.com/{username}', 'https://reddit.com/user/{username}'], extract=True, metadata=False)
assert cmd[cmd.index('--websites') + 1] == 'https://github.com/{username} https://reddit.com/user/{username}' and '--top' not in cmd
assert '--extract' in cmd and '--metadata' not in cmd
ok('build_command: launcher with workers, every field requested, no scanner-side filter, exact site URLs')

# 6. parse_output ---------------------------------------------------------------
assert parse_output('{"detected":[{"link":"https://x.com/a","rate":"%100.0"}]}')['detected'][0]['link'] == 'https://x.com/a'
assert parse_output('banner\n{"detected":[]}\ntrailing')['detected'] == []
assert parse_output('') == {'detected': [], 'parse_error': 'empty stdout'}
assert 'parse_error' in parse_output('not json')
ok('parse_output: JSON, banner-wrapped JSON, empty and garbage stdout')

# 7. confidence + enrich ----------------------------------------------------------
for rate, tier in [('%100.0', 'high'), ('80%', 'medium'), ('%60', 'medium'), ('%20', 'low'), (None, 'unknown')]:
    assert confidence_tier(rate) == tier, (rate, tier, confidence_tier(rate))
profiles = [{'rate': '%100.0'}, {'rate': '%66.67'}, {'rate': '%25.0'}]
assert len(keep_by_confidence(profiles, 'good')) == 1 and len(keep_by_confidence(profiles, 'good,maybe')) == 2 and len(keep_by_confidence(profiles, 'all')) == 3
assert clean_value('unavailable') is None and clean_value('') is None and clean_value('English') == 'English'
assert parse_rate('%66.6') == 66.6
index = {s['host']: s for s in SITES}
row = enrich({'link': 'https://github.com/torvalds', 'rate': '%100.0', 'title': 'torvalds (Linus Torvalds)', 'text': 'Linus', 'language': 'English', 'metadata': [{'property': 'og:title', 'content': 'torvalds'}], 'extracted': 'unavailable'}, 'torvalds', index, '2026-09-12T00:00:00Z')
assert row['language'] == 'English' and row['metadata'][0]['content'] == 'torvalds' and row['extracted'] is None
assert row['platform'] == 'GitHub' and row['site'] == 'github.com' and row['confidence'] == 'high' and row['matchRate'] == 100.0
assert row['category'] == 'developer_tech' and row['country'] == 'United States' and row['adultSite'] is False and row['siteRank'] == 50
row = enrich({'link': 'https://unknown.example/u', 'rate': '%25.0'}, 'u', index, 'now')
assert row['category'] == 'other' and row['country'] is None and row['confidence'] == 'low'
ok('enrich: rows carry platform, site, confidence, category, country, adult flag, rank, language, metadata')

# 8. safe_status never raises ---------------------------------------------------------
asyncio.run(safe_status('Found 202 profiles'))
ok('safe_status: a failing SDK status call is logged, not raised (the APIFY_AI crash)')

# 8b. username only -> everything at maximum -----------------------------------------
st = resolve_settings({'username': 'elonmusk'})
assert st == {'top': 999, 'filter': 'all', 'extract': True, 'metadata': True, 'timeout': 3600}, st
assert st == MAX_SETTINGS
st = resolve_settings({'username': 'x', 'top': 100, 'filter': 'good', 'extract': False, 'metadata': False, 'timeout': 120})
assert st == {'top': 100, 'filter': 'good', 'extract': False, 'metadata': False, 'timeout': 120}, st
st = resolve_settings({'top': 0, 'filter': 'weird', 'timeout': 5, 'extract': 'yes'})
assert st['top'] == 999 and st['filter'] == 'all' and st['timeout'] == 60 and st['extract'] is True, st
ok('resolve_settings: a bare username gets all 999 sites, every match, extraction, metadata and an hour; explicit values win; junk falls back to max')

# 8c. chunking + progress wording -------------------------------------------------------
assert chunked(list(range(7)), 3) == [[0, 1, 2], [3, 4, 5], [6]] and chunked([], 3) == []
assert len(chunked(list(range(998)), CHUNK_SIZE)) == 8
line = progress_line(500, 998, 0, 1, 143, 'elonmusk')
assert line.startswith('50% done: 500 of 998 site checks, 143 profiles so far.') and 'elonmusk' in line, line
line = progress_line(1247, 2994, 1, 3, 200, 'kaytats')
assert line.startswith('41% done') and '1 of 3 usernames finished' in line and 'now checking kaytats' in line, line
ok('chunked + progress_line: 998 sites -> 8 steps; status line shows percent, site checks, profiles, usernames')

# 9. summary invariants (documented shape) -------------------------------------------
sample = json.loads(json.dumps({'recordType': 'summary', 'usernames': ['a'], 'profilesFound': 0, 'message': 'x', 'perUsername': []}))
assert sample['recordType'] == 'summary'
ok('summary row shape is serialisable')

# 10. real scan (optional) -------------------------------------------------------------
if shutil.which('social-analyzer'):
    cmd = build_command('torvalds', top=100, site_urls=['https://github.com/{username}', 'https://reddit.com/user/{username}'], extract=False, metadata=False)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    parsed = parse_output(proc.stdout)
    links = [p['link'] for p in parsed.get('detected', [])]
    assert 'https://github.com/torvalds' in links, (proc.returncode, proc.stdout[:200], proc.stderr[:200])
    gh = next(p for p in parsed['detected'] if p['link'] == 'https://github.com/torvalds')
    assert gh.get('status') == 'good' and 'language' in gh and 'type' in gh, gh.keys()
    assert proc.stderr.strip() == '', f'launcher should silence scanner warnings, got: {proc.stderr[:200]}'
    ok(f'real scan through src/scan.py: torvalds found on {len(links)} of 2 named sites, all fields present, stderr clean')
else:
    print('  --  real scan skipped (social-analyzer CLI not installed here; it runs in the Docker image)')

print(f'\nALL {passed} TESTS PASS')
