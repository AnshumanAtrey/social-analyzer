"""
Social Analyzer Apify actor - wraps qeeqbox/social-analyzer.

Finds a username across 900+ social media / online platforms.
Pushes one dataset record per detected profile plus one summary record.
"""
import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timezone

from apify import Actor


# Hosts we recognize in a pasted profile link, so we can pull the handle out of it.
KNOWN_HOSTS = ('twitter.com', 'x.com', 'instagram.com', 'facebook.com', 'github.com',
               'linkedin.com', 'tiktok.com', 'youtube.com', 'reddit.com', 't.me', 'medium.com')


def _clean_one(tok: str) -> str:
    """Clean a single handle: drop @, and pull the handle out of a pasted profile link."""
    s = tok.strip()
    if not s:
        return ''
    # If it looks like a URL or a host/path, take the last meaningful path segment.
    if '/' in s or '://' in s or any(h in s.lower() for h in KNOWN_HOSTS):
        s = re.sub(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://', '', s)   # strip scheme
        s = s.split('?', 1)[0].split('#', 1)[0]              # strip query / fragment
        segs = [p for p in s.split('/') if p]
        if segs:
            s = segs[-1]                                     # last path segment = handle
    return s.lstrip('@').strip()


def clean_username(raw: str) -> str:
    """Normalize pasted input into bare handle(s) the scanner can use.

    Strips '@', turns a pasted profile link (twitter.com/elonmusk) into 'elonmusk',
    and accepts comma- or space-separated lists, returning them space-joined (the
    multi-username format the scanner expects). Without this, '@elonmusk' or a link
    is searched literally and produces confidently-wrong URLs like github.com/@elonmusk.
    """
    if not raw:
        return raw
    toks = re.split(r'[,\s]+', str(raw).strip())
    cleaned = [c for c in (_clean_one(t) for t in toks) if c]
    return ' '.join(cleaned)


def valid_username(handle: str) -> bool:
    """True if every token is a plausible handle (no junk, spaces, or leftover URL bits)."""
    if not handle:
        return False
    return all(re.fullmatch(r'[A-Za-z0-9._\-]{1,100}', p) for p in handle.split(' '))


def parse_rate(rate) -> float:
    """qeeqbox returns a match rate like '%100.0' or '100.0%'. Return it as a float, or None."""
    if rate is None:
        return None
    m = re.search(r'[\d.]+', str(rate))
    return float(m.group()) if m else None


def confidence_tier(rate) -> str:
    """Bucket a match rate into a plain-language confidence tier."""
    v = parse_rate(rate)
    if v is None:
        return 'unknown'
    return 'high' if v >= 75 else 'medium' if v >= 50 else 'low'


def build_command(input_data: dict) -> tuple:
    """Build the social-analyzer argv. Returns (cmd, summary_label)."""
    username = input_data['username']
    cmd = [
        'social-analyzer',
        '--username', username,
        '--output', 'json',
    ]

    mode = input_data.get('mode', 'fast')
    cmd.extend(['--mode', mode])

    method = input_data.get('method', 'find')
    cmd.extend(['--method', method])

    filt = input_data.get('filter', 'good')
    cmd.extend(['--filter', filt])

    # 'websites' takes precedence over 'top' if both present.
    # Accept BOTH array (new schema) and string (legacy) for backward compat.
    websites_raw = input_data.get('websites')
    if isinstance(websites_raw, list):
        websites = ' '.join(s.strip() for s in websites_raw if s and isinstance(s, str))
    else:
        websites = (websites_raw or '').strip()
    if websites:
        cmd.extend(['--websites', websites])
    else:
        top = input_data.get('top', 100)
        cmd.extend(['--top', str(top)])

    countries_raw = input_data.get('countries')
    if isinstance(countries_raw, list):
        countries = ' '.join(s.strip() for s in countries_raw if s and isinstance(s, str))
    else:
        countries = (countries_raw or '').strip()
    if countries:
        cmd.extend(['--countries', countries])

    site_type = (input_data.get('siteType') or '').strip()
    if site_type:
        cmd.extend(['--type', site_type])

    if input_data.get('extract'):
        cmd.append('--extract')
    if input_data.get('metadata'):
        cmd.append('--metadata')
    if input_data.get('trim'):
        cmd.append('--trim')

    # Always request the 4 main option groups so dataset rows are rich
    cmd.extend(['--options', 'link,rate,title,text'])

    return cmd, mode


def parse_output(stdout: str) -> dict:
    """social-analyzer outputs JSON on stdout. Returns {detected: [...], extra: ...} or {error: ...}."""
    if not stdout or not stdout.strip():
        return {'detected': [], 'parse_error': 'empty stdout'}

    # Try to parse the whole thing first
    try:
        return json.loads(stdout.strip())
    except json.JSONDecodeError:
        pass

    # Fallback: find the first {...} block (CLI can prefix banners)
    start = stdout.find('{')
    end = stdout.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(stdout[start:end + 1])
        except json.JSONDecodeError as e:
            return {'detected': [], 'parse_error': f'JSON decode failed: {e}'}

    return {'detected': [], 'parse_error': 'no JSON object found in stdout'}


async def main() -> None:
    async with Actor:
        Actor.log.info('Social Analyzer actor starting')

        input_data = await Actor.get_input() or {}
        raw_username = input_data.get('username')
        if not raw_username or not str(raw_username).strip():
            await Actor.fail(status_message='Enter a username, e.g. elonmusk (just the handle, not @elonmusk or a link).')
            return

        # Auto-clean pasted input (@, profile link, comma list) into bare handle(s), then
        # validate. A raw '@handle' or link is searched literally and returns confidently
        # broken URLs (github.com/@handle) while still saying "success" - this stops that.
        username = clean_username(raw_username)
        if not valid_username(username):
            await Actor.fail(status_message=(
                f'"{raw_username}" does not look like a username. Enter just the handle, '
                f'e.g. elonmusk - no @, no link, no spaces.'))
            return
        if username != str(raw_username).strip():
            Actor.log.info(f'Cleaned input "{raw_username}" -> "{username}"')
        input_data['username'] = username   # use the cleaned value downstream

        cmd, mode = build_command(input_data)
        Actor.log.info(f'Username: {username}')
        Actor.log.info(f'Mode: {mode}')
        Actor.log.info(f'Command: {" ".join(cmd)}')

        timeout = int(input_data.get('timeout', 1800))
        timestamp = datetime.now(timezone.utc).isoformat()
        start_wall = datetime.now(timezone.utc)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            await Actor.fail(status_message=f'social-analyzer timed out after {timeout}s')
            return
        except FileNotFoundError as e:
            await Actor.fail(status_message=f'social-analyzer binary not found: {e}')
            return

        Actor.log.info(f'social-analyzer exit code: {result.returncode} (stdout {len(result.stdout)} chars, stderr {len(result.stderr)} chars)')

        # Surface stderr if anything bad happened (CLI writes errors there)
        if result.stderr:
            for line in [l for l in result.stderr.splitlines() if l.strip()][-20:]:
                Actor.log.warning(line)

        parsed = parse_output(result.stdout)

        if 'parse_error' in parsed:
            Actor.log.error(f'Output parse error: {parsed["parse_error"]}')
            # surface first chunk of stdout for debugging
            Actor.log.info(f'stdout sample: {result.stdout[:500]}')
            await Actor.set_status_message(
                f'The scan did not return readable results for "{username}". Try again, or switch to fast mode.')
            await Actor.push_data({
                'recordType': 'summary',
                'username': username,
                'mode': mode,
                'success': False,
                'error': parsed['parse_error'],
                'exitCode': result.returncode,
                'timestamp': timestamp,
            })
            return

        detected = parsed.get('detected', []) or []
        unknown  = parsed.get('unknown', []) or []
        failed   = parsed.get('failed', []) or []

        # Rank strongest matches first so real profiles surface above long-shot guesses,
        # and tag each with a plain confidence tier (the raw match rate alone reads as noise).
        detected.sort(key=lambda p: parse_rate(p.get('rate')) or 0, reverse=True)
        tier_counts = {'high': 0, 'medium': 0, 'low': 0, 'unknown': 0}

        # Push one dataset record per detected profile
        for profile in detected:
            tier = confidence_tier(profile.get('rate'))
            tier_counts[tier] += 1
            await Actor.push_data({
                'recordType': 'profile',
                'username': username,
                'platform': profile.get('title') or profile.get('text'),
                'link': profile.get('link'),
                'confidence': tier,
                'status': profile.get('status'),
                'rate': profile.get('rate'),
                'country': profile.get('country'),
                'language': profile.get('language'),
                'type': profile.get('type'),
                'rank': profile.get('rank'),
                'extracted': profile.get('extracted'),
                'metadata': profile.get('metadata'),
                'text': profile.get('text'),
                'timestamp': timestamp,
            })

        wall_duration = (datetime.now(timezone.utc) - start_wall).total_seconds()
        found = len(detected)
        high = tier_counts['high']

        # Tell the user clearly what happened. A 0-result run, or one where every match is
        # weak, must not look identical to a strong run - that silent gap is the main churn.
        if found == 0:
            note = ('0 profiles found. Enter just the handle (like elonmusk), not @elonmusk or a '
                    'profile link. Check the spelling, or raise "How many sites to scan" for wider coverage.')
            Actor.log.warning(note)
            await Actor.set_status_message(note)
        elif high == 0:
            note = (f'{found} possible profiles for "{username}", but none high-confidence. '
                    f'These are weaker matches - check the top-ranked ones first.')
            Actor.log.info(note)
            await Actor.set_status_message(note)
        else:
            note = None
            await Actor.set_status_message(
                f'Found {found} profiles for "{username}" ({high} high-confidence). Strongest first.')

        await Actor.push_data({
            'recordType': 'summary',
            'username': username,
            'mode': mode,
            'method': input_data.get('method', 'find'),
            'filter': input_data.get('filter', 'good'),
            'top': input_data.get('top'),
            'sitesChecked': len(detected) + len(unknown) + len(failed),
            'profilesFound': found,
            'highConfidence': tier_counts['high'],
            'mediumConfidence': tier_counts['medium'],
            'lowConfidence': tier_counts['low'],
            'profilesUnknown': len(unknown),
            'profilesFailed': len(failed),
            'duration': round(wall_duration, 2),
            'cmd': ' '.join(cmd),
            'success': True,
            'foundAnything': found > 0,
            'message': note,
            'timestamp': timestamp,
        })

        Actor.log.info(f'Scan complete: {found} profiles ({high} high-confidence) for "{username}" in {wall_duration:.1f}s')


if __name__ == '__main__':
    asyncio.run(main())
