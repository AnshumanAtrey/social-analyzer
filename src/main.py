"""
Social Analyzer Apify actor — wraps qeeqbox/social-analyzer.

Finds a username across 900+ social media / online platforms.
Pushes one dataset record per detected profile plus one summary record.
"""
import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone

from apify import Actor


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
        username = input_data.get('username')
        if not username:
            await Actor.fail(status_message='username is required (single or comma-separated)')
            return

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

        # Push one dataset record per detected profile
        for profile in detected:
            await Actor.push_data({
                'recordType': 'profile',
                'username': username,
                'platform': profile.get('title') or profile.get('text'),
                'link': profile.get('link'),
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

        await Actor.push_data({
            'recordType': 'summary',
            'username': username,
            'mode': mode,
            'method': input_data.get('method', 'find'),
            'filter': input_data.get('filter', 'good'),
            'top': input_data.get('top'),
            'sitesChecked': len(detected) + len(unknown) + len(failed),
            'profilesFound': len(detected),
            'profilesUnknown': len(unknown),
            'profilesFailed': len(failed),
            'duration': round(wall_duration, 2),
            'cmd': ' '.join(cmd),
            'success': True,
            'timestamp': timestamp,
        })

        Actor.log.info(f'Scan complete: {len(detected)} profiles found for "{username}" in {wall_duration:.1f}s')


if __name__ == '__main__':
    asyncio.run(main())
