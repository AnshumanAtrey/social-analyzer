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
from src.main import build_command, parse_output


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
print('TEST 6: REAL scan against "github" username (top 10)')
print('=' * 60)
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
for p in detected[:5]:
    print(f'    - {p.get("title") or p.get("text","?")[:30]:30s} {p.get("status","?"):6s} {p.get("link","")[:60]}')
assert len(detected) >= 1, 'expected at least 1 profile for "github" username'
print('  ✓ real scan returned detections')

print()
print('ALL TESTS PASS ✓')
