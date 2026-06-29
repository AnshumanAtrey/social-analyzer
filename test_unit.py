"""Unit + integration test for social-analyzer wrapper."""
import sys, types, os, subprocess

apify_stub = types.ModuleType('apify')
class _ActorLog:
    def info(self, m): print(f'[INFO ] {m}')
    def warning(self, m): print(f'[WARN ] {m}')
    def error(self, m): print(f'[ERROR] {m}')
class _Actor:
    log = _ActorLog()
apify_stub.Actor = _Actor()
sys.modules['apify'] = apify_stub

sys.path.insert(0, os.path.dirname(__file__))
from src.main import (build_command, parse_output, clean_username, valid_username,
                      confidence_tier, parse_rate)
import shutil


print('=' * 60)
print('TEST 1: build_command — minimal input')
print('=' * 60)
cmd, mode = build_command({'username': 'elonmusk'})
print(f'  cmd: {cmd}')
assert cmd[0] == 'social-analyzer'
assert '--username' in cmd and 'elonmusk' in cmd
assert '--output' in cmd and 'json' in cmd
assert '--mode' in cmd and 'fast' in cmd  # default
assert '--top' in cmd and '100' in cmd     # default
assert '--filter' in cmd and 'good' in cmd
assert '--method' in cmd and 'find' in cmd
assert '--options' in cmd
print('  ✓ defaults applied')

print()
print('=' * 60)
print('TEST 2: build_command — all knobs')
print('=' * 60)
cmd, mode = build_command({
    'username': 'a,b',
    'mode': 'slow',
    'top': 500,
    'websites': 'youtube tiktok github',  # overrides top
    'countries': 'us br',
    'siteType': 'Social Networks',
    'method': 'all',
    'filter': 'good,maybe',
    'extract': True,
    'metadata': True,
    'trim': True,
})
print(f'  cmd: {cmd}')
assert '--websites' in cmd and 'youtube tiktok github' in cmd
assert '--top' not in cmd  # websites takes precedence
assert '--countries' in cmd and 'us br' in cmd
assert '--type' in cmd and 'Social Networks' in cmd
assert '--method' in cmd and 'all' in cmd
assert '--filter' in cmd and 'good,maybe' in cmd
assert '--extract' in cmd
assert '--metadata' in cmd
assert '--trim' in cmd
print('  ✓ all flags wired')

print()
print('=' * 60)
print('TEST 3: parse_output — valid JSON')
print('=' * 60)
data = parse_output('{"detected":[{"link":"https://x.com/a","status":"good"}]}')
assert data == {'detected': [{'link': 'https://x.com/a', 'status': 'good'}]}
print('  ✓ valid JSON parsed')

print()
print('=' * 60)
print('TEST 4: parse_output — JSON with banner prefix')
print('=' * 60)
data = parse_output('Some CLI banner text\n{"detected":[]}\nTrailing junk')
assert data == {'detected': []}
print('  ✓ banner stripped, JSON extracted')

print()
print('=' * 60)
print('TEST 5: parse_output — empty / bad input')
print('=' * 60)
assert parse_output('') == {'detected': [], 'parse_error': 'empty stdout'}
assert 'parse_error' in parse_output('not json at all')
print('  ✓ error paths handled')

print()
print('=' * 60)
print('TEST 6: clean_username auto-cleans pasted input')
print('=' * 60)
clean_cases = {
    'elonmusk': 'elonmusk',
    '@elonmusk': 'elonmusk',
    'https://twitter.com/elonmusk': 'elonmusk',
    'twitter.com/elonmusk': 'elonmusk',
    'https://instagram.com/elonmusk/': 'elonmusk',
    'https://www.linkedin.com/in/elonmusk': 'elonmusk',
    '  @elonmusk  ': 'elonmusk',
    'johndoe,janedoe': 'johndoe janedoe',     # comma list -> space-joined
    '@a, @b': 'a b',
    'github.com/torvalds': 'torvalds',
}
for raw, want in clean_cases.items():
    got = clean_username(raw)
    assert got == want, f'clean_username({raw!r}) -> {got!r}, expected {want!r}'
print(f'  ✓ {len(clean_cases)} cases pass (@, profile links, comma lists all handled)')

print()
print('=' * 60)
print('TEST 7: valid_username + confidence_tier')
print('=' * 60)
for h in ['elonmusk', 'john.doe', 'a_b-c', 'johndoe janedoe']:
    assert valid_username(h), f'should be valid: {h!r}'
for h in ['', 'foo/bar', 'a b!', 'has space/slash']:
    assert not valid_username(h), f'should be invalid: {h!r}'
assert valid_username(clean_username('@still')) and clean_username('@still') == 'still'
for rate, tier in [('%100.0', 'high'), ('80%', 'high'), ('%60', 'medium'), ('%20', 'low'), (None, 'unknown')]:
    assert confidence_tier(rate) == tier, f'confidence_tier({rate!r}) -> {confidence_tier(rate)!r}, want {tier!r}'
assert parse_rate('%66.6') == 66.6
print('  ✓ validation gate + confidence tiers correct')

print()
print('=' * 60)
print('TEST 8: REAL scan against "github" (skipped if CLI not installed)')
print('=' * 60)
if shutil.which('social-analyzer'):
    real_cmd = ['social-analyzer', '--username', 'github', '--top', '10', '--mode', 'fast',
                '--output', 'json', '--method', 'find', '--filter', 'good']
    print(f'  running: {" ".join(real_cmd)}')
    proc = subprocess.run(real_cmd, capture_output=True, text=True, timeout=120)
    print(f'  exit={proc.returncode}, stdout={len(proc.stdout)} chars')
    assert proc.returncode == 0, f'CLI failed: {proc.stderr[:300]}'
    parsed = parse_output(proc.stdout)
    assert 'parse_error' not in parsed, f'parse error: {parsed.get("parse_error")}'
    detected = parsed.get('detected', [])
    print(f'  detected: {len(detected)} profiles')
    assert len(detected) >= 1, 'expected at least 1 profile for "github" username'
    print('  ✓ real scan returned detections')
else:
    print('  - social-analyzer CLI not installed locally; skipping (runs in the Docker image)')

print()
print('ALL TESTS PASS ✓')
